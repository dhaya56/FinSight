"""An embedder that remembers, wrapped around one that computes.

**Why this exists.** Embedding is the whole cost of indexing this corpus: 4,867 child
chunks measured 40.7 minutes (ENV-011), and there is no faster way to embed — batching
is already at its knee (2.63 texts/s at 32 per request, 2.69 at 64) and client-side
threading was measured as worth nothing because Ollama serialises the work. The
remaining saving is not to embed faster but to embed less. Re-extracting the corpus
under a new configuration changes the text of 770 of those chunks and leaves 4,097
byte-identical, so 84.2% of the next re-index is recomputing vectors that already
exist. Measured on 200 real chunks, a hit runs at **825 texts/s against 1.93 cold**,
which turns a 42-minute re-index into about 6.7 minutes.

**Why it cannot live in Qdrant.** A re-chunk gives every chunk a new identifier
while its text survives, so a cache keyed on chunk identity would miss everything.
This one is keyed on content.

**Why it cannot change an answer.** This is a wrapper around the port, so a hit
returns the vector the port itself returned earlier — after the adapter's task
prefix, shape check and normalisation, because the cached value is the port's
*output*. The key carries the text, the model, the configuration version and
whether the text was a document or a query, which is everything the port consults;
the stored text is compared against the requested text on every hit, so a digest
collision becomes a miss rather than a wrong vector; and vectors round-trip through
``double precision``, so a cached vector is bit-identical to a fresh one rather than
merely close. Measured rather than asserted: 200 of 200 hits came back identical to
the model's own output, largest difference in any single component **exactly 0.0**.
*This is not a semantic cache.* Matching near-identical questions was
considered and rejected with measurement: changing the fiscal year in a question
moves its vector less than changing the metric, so a similarity threshold tight
enough to be safe admits almost nothing and a threshold loose enough to be useful
answers the wrong question.

**Why a cache failure is not an error.** The vector is identical with or without
this layer, so a database fault degrades throughput and nothing else. Read and write
faults are counted rather than raised, and :attr:`CachingEmbedder.statistics`
reports them so a cache that has quietly stopped working is visible as a hit rate
that collapsed rather than as silence.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from finsight.config.settings import Settings
from finsight.embedding.port import Embedder, Vector
from finsight.persistence.repositories.embedding_cache import (
    CacheKey,
    EmbeddingCacheRepository,
)
from finsight.persistence.tables.embedding_cache import KIND_DOCUMENT, KIND_QUERY

SessionScope = Callable[[], AbstractContextManager[Session]]


@dataclass(slots=True)
class CacheCounters:
    """In-process tallies for one embedder, reported after an indexing run.

    Not persisted. Hit rate is a property of a run, and the question an operator
    asks is "did this run skip what it should have skipped", which the next run's
    numbers would obscure.
    """

    hits: int = 0
    misses: int = 0
    stored: int = 0
    read_faults: int = 0
    write_faults: int = 0

    @property
    def lookups(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        """Share of lookups served from the cache, 0.0 when nothing was looked up."""
        return self.hits / self.lookups if self.lookups else 0.0


@dataclass(frozen=True, slots=True)
class _Request:
    """One text to embed, and every position its vector belongs at."""

    key: CacheKey
    positions: tuple[int, ...]


class CachingEmbedder:
    """Serves vectors from PostgreSQL when the exact text was embedded before.

    Satisfies :class:`~finsight.embedding.port.Embedder` structurally, so it
    substitutes for the adapter wherever one is wired.
    """

    def __init__(
        self,
        *,
        inner: Embedder,
        config_version: str,
        scope: SessionScope,
    ) -> None:
        self._inner = inner
        self._config_version = config_version
        self._scope = scope
        self._counters = CacheCounters()

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    @property
    def statistics(self) -> CacheCounters:
        return self._counters

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        """Embed indexing text, computing only what is not already held.

        Order is the port's contract — a caller pairs vectors with chunks by
        position — so duplicates within one batch are collapsed into a single
        lookup and then written back to every position they came from.
        """
        if not texts:
            return ()
        vectors = self._embed(texts, kind=KIND_DOCUMENT)
        return tuple(vectors)

    def embed_query(self, text: str) -> Vector:
        """Embed search text.

        Cached under a separate kind, never shared with the document form: the two
        carry different task prefixes and are not interchangeable. Worth caching
        because repeated questions are the normal case for a demonstration, a
        starter prompt and an evaluation run, and each miss is a round trip to a
        serialised model.
        """
        return self._embed([text], kind=KIND_QUERY)[0]

    def _embed(self, texts: Sequence[str], *, kind: str) -> list[Vector]:
        requests = self._deduplicated(texts, kind=kind)
        cached, hit_ids = self._cached(tuple(request.key for request in requests))

        missing = [request for request in requests if request.key not in cached]
        self._counters.hits += len(requests) - len(missing)
        self._counters.misses += len(missing)

        computed = self._compute([request.key.text for request in missing], kind=kind)
        fresh = {
            request.key: vector
            for request, vector in zip(missing, computed, strict=True)
        }
        self._remember(fresh, hit_ids=hit_ids)

        # Keyed by position and read back by index, so a vector that never got
        # assigned raises here rather than silently shortening the result and
        # shifting every chunk onto the wrong vector.
        answer: dict[int, Vector] = {}
        for request in requests:
            vector = (
                cached[request.key]
                if request.key in cached
                else fresh[request.key]
            )
            for position in request.positions:
                answer[position] = vector
        return [answer[position] for position in range(len(texts))]

    def _deduplicated(self, texts: Sequence[str], *, kind: str) -> list[_Request]:
        """One request per distinct text, remembering every position it filled."""
        positions: dict[str, list[int]] = {}
        for position, text in enumerate(texts):
            positions.setdefault(text, []).append(position)
        return [
            _Request(
                key=CacheKey(
                    text=text,
                    model=self._inner.model,
                    config_version=self._config_version,
                    kind=kind,
                ),
                positions=tuple(found),
            )
            for text, found in positions.items()
        ]

    def _cached(
        self, keys: Sequence[CacheKey]
    ) -> tuple[dict[CacheKey, Vector], tuple[int, ...]]:
        """Look vectors up, treating any database fault as a miss."""
        try:
            with self._scope() as session:
                return EmbeddingCacheRepository(session).fetch(keys)
        except SQLAlchemyError:
            self._counters.read_faults += 1
            return {}, ()

    def _compute(self, texts: Sequence[str], *, kind: str) -> tuple[Vector, ...]:
        """Call the model, outside any transaction (§29.7).

        The lookup above has committed and closed before this runs, and the write
        below opens a new transaction afterwards. A single scope around all three
        would hold a transaction open for the length of a model call, which is the
        one thing ``session_scope`` documents as forbidden.
        """
        if not texts:
            return ()
        if kind == KIND_QUERY:
            return tuple(self._inner.embed_query(text) for text in texts)
        return self._inner.embed_documents(texts)

    def _remember(
        self, fresh: dict[CacheKey, Vector], *, hit_ids: Sequence[int]
    ) -> None:
        """Store new vectors and mark reused ones, in one transaction.

        A fault here is counted and swallowed. The vectors are already computed and
        already correct; failing the batch would discard good work and re-embed it,
        and the caller's own database writes — the outbox completion — will surface
        a genuinely broken database a moment later.
        """
        if not fresh and not hit_ids:
            return
        try:
            with self._scope() as session:
                repository = EmbeddingCacheRepository(session)
                repository.touch(hit_ids)
                self._counters.stored += repository.store(
                    fresh, dimensions=self._inner.dimensions
                )
        except SQLAlchemyError:
            self._counters.write_faults += 1


def cached(
    inner: Embedder,
    *,
    config_version: str,
    scope: SessionScope | None = None,
) -> CachingEmbedder:
    """Wrap an embedder in the cache, with the project's session scope by default."""
    if scope is None:
        from finsight.persistence.database import session_scope

        scope = session_scope
    return CachingEmbedder(
        inner=inner, config_version=config_version, scope=scope
    )


def build_cached_embedder(settings: Settings) -> Embedder:
    """The configured embedder, wrapped in the cache unless it is switched off.

    One function rather than three lines repeated at each wiring site, because the
    indexer and the retrieval pipeline must agree: an index built through the cache
    and a query embedded around it would still be comparable — the vectors are
    identical either way — but a disagreement about the configuration version would
    not be, and having one place to read makes that checkable.
    """
    from finsight.embedding.ollama_embedder import build_embedder

    embedder = build_embedder(settings)
    if not settings.embedding_cache_enabled:
        return embedder
    return cached(embedder, config_version=settings.embedding_config_version)


def describe(counters: CacheCounters) -> Iterator[str]:
    """Lines reporting a run's cache behaviour, for the CLI to print."""
    yield (
        f"embedding cache:      {counters.hits} hit(s), {counters.misses} miss(es)"
        f" ({counters.hit_rate:.1%} hit rate)"
    )
    yield f"  vectors stored:     {counters.stored}"
    if counters.read_faults or counters.write_faults:
        yield (
            f"  cache faults:       {counters.read_faults} read, "
            f"{counters.write_faults} write (vectors unaffected)"
        )


__all__ = [
    "CacheCounters",
    "CachingEmbedder",
    "build_cached_embedder",
    "cached",
    "describe",
]
