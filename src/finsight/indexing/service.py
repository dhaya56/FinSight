"""Draining the index outbox: embed, upsert, record, then activate.

The third arrow of the retrieval path, and the one §29.7 constrains hardest:
**no transaction may span the embedding or vector calls.** So the loop is
deliberately three-phase per batch —

1. read a batch of pending events (one short transaction),
2. embed and upsert (no transaction at all),
3. record completion (one short transaction),

— rather than the obvious single transaction per batch. A transaction held across
an Ollama call would hold it for the 2.6 seconds a 32-chunk batch takes, and across
a *stalled* Ollama call it would hold until the statement timeout, blocking vacuum
and holding back the transaction horizon for every other session.

**Activation is the last thing that happens, and only if everything succeeded**
(§11.13). A generation becomes queryable when its chunks are all in the index and
the index agrees with PostgreSQL about how many there are — not when the last batch
upserts. A failed run leaves the previous generation active and queryable, which is
the property that makes re-indexing safe to attempt at any time.

**Replay is safe by construction.** Point identifiers are derived from the chunk,
model and embedding configuration (§29.9), so re-running after a crash mid-batch
rewrites the same points rather than duplicating them, and the outbox makes it
visible which chunks still need the work.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from finsight.domain.errors import DomainError
from finsight.embedding.port import Embedder, EmbeddingUnavailableError
from finsight.indexing.enrichment import ChunkContext, embedded_text
from finsight.lexical.bm25 import BM25Config, document_vector
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.chunks import ChunkRepository, PendingChunk
from finsight.persistence.repositories.generations import GenerationRepository
from finsight.persistence.tables.generations import STATE_SHADOW
from finsight.vector_index.port import (
    IndexedChunk,
    VectorIndex,
    VectorIndexUnavailableError,
)

BATCH_CEILING: Final = 256
"""Upper bound on one read, independent of the embedder's own batching.

The embedder batches for round-trip efficiency; this bounds how much work is held
in memory and how much is lost to a crash. Unmeasured.
"""


class IndexingError(DomainError):
    """A generation could not be indexed."""


class NothingToIndexError(IndexingError):
    """The generation does not exist, or holds no chunks to index."""


class GenerationNotIndexableError(IndexingError):
    """The generation is in a state that must not be indexed.

    An already-active generation is the case that matters: re-indexing one in place
    would mutate the vectors a reader is being served from, which §11.13 forbids by
    requiring reprocessing to build a shadow generation and switch atomically.
    """


class IndexReconciliationError(IndexingError):
    """The index and PostgreSQL disagree on how many chunks were written.

    Raised instead of activating. §29.11 makes reconciliation a requirement rather
    than a diagnostic, and a generation activated with chunks missing from the index
    is the worst available outcome: it answers queries, it answers them incompletely,
    and nothing in the answer says so.

    **One case produces this error without anything being wrong**, and it is worth
    knowing before diagnosing it as corruption: reopening an *already-indexed*
    generation after bumping ``embedding_config_version``. Point identifiers include
    that version (§29.9), so the second run writes a second population under the
    same ``generation_id`` instead of overwriting the first, and the count comes back
    doubled.

    Not guarded against, because the ordinary path cannot reach it — a configuration
    change is a re-chunk under a *new* generation (§11.13), and a new generation id
    isolates the count. The recovery is to drop the collection, which §29.2 makes
    cheap: the index is derived and rebuildable from PostgreSQL.
    """


@dataclass(frozen=True, slots=True)
class RecordedIndexing:
    """What one indexing run did."""

    generation_id: UUID
    indexed: int
    already_complete: bool
    activated: bool
    collection: str


class IndexingRecorder(Protocol):
    """All database interaction for the indexing stage."""

    def prepare(self, generation_id: UUID) -> tuple[UUID, int, str]:
        """Return the document version, the chunks to index, and the state."""
        ...

    def pending(self, generation_id: UUID, limit: int) -> list[PendingChunk]: ...

    def complete(self, chunk_ids: Sequence[UUID]) -> None: ...

    def record_failure(self, chunk_ids: Sequence[UUID], reason: str) -> None: ...

    def indexed_count(self, generation_id: UUID) -> int: ...

    def activate(self, generation_id: UUID) -> None: ...

    def fail(self, generation_id: UUID) -> None: ...

    def reopen(self, generation_id: UUID) -> None:
        """Return a failed generation and its failed events to a retryable state."""
        ...


class TransactionalIndexingRecorder:
    """The recorder that owns the transactions. Each method is one of them."""

    def __init__(
        self,
        session_scope_factory: Callable[
            [], AbstractContextManager[Session]
        ] = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    def prepare(self, generation_id: UUID) -> tuple[UUID, int, str]:
        with self._session_scope() as session:
            generations = GenerationRepository(session)
            state = generations.state_of(generation_id=generation_id)
            if state is None:
                raise NothingToIndexError(f"no generation {generation_id}")
            version_id = generations.document_version_of(generation_id=generation_id)
            if version_id is None:
                raise NothingToIndexError(
                    f"generation {generation_id} has no document version"
                )
            total = ChunkRepository(session).event_count(generation_id=generation_id)
            return version_id, total, state

    def pending(self, generation_id: UUID, limit: int) -> list[PendingChunk]:
        with self._session_scope() as session:
            return ChunkRepository(session).pending_chunks(
                generation_id=generation_id, limit=limit
            )

    def complete(self, chunk_ids: Sequence[UUID]) -> None:
        with self._session_scope() as session:
            ChunkRepository(session).complete_events(chunk_ids=chunk_ids)

    def record_failure(self, chunk_ids: Sequence[UUID], reason: str) -> None:
        with self._session_scope() as session:
            ChunkRepository(session).fail_events(chunk_ids=chunk_ids, reason=reason)

    def indexed_count(self, generation_id: UUID) -> int:
        with self._session_scope() as session:
            return ChunkRepository(session).completed_event_count(
                generation_id=generation_id
            )

    def activate(self, generation_id: UUID) -> None:
        with self._session_scope() as session:
            GenerationRepository(session).activate(generation_id=generation_id)

    def fail(self, generation_id: UUID) -> None:
        with self._session_scope() as session:
            GenerationRepository(session).fail(generation_id=generation_id)

    def reopen(self, generation_id: UUID) -> None:
        """Failed events back to pending, and a failed generation back to shadow.

        One transaction, because half of it is worse than neither: events returned
        to pending under a generation still marked failed would be indexed and then
        refused activation, and a generation returned to shadow with its events
        still failed would reconcile short and fail again immediately.

        Safe because a failed generation was never queryable. Nothing reads it, so
        returning it to shadow takes nothing away from a reader — which is not true
        of an active generation, and :meth:`GenerationRepository.reopen` refuses
        that one.
        """
        with self._session_scope() as session:
            GenerationRepository(session).reopen(generation_id=generation_id)
            ChunkRepository(session).reset_failed_events(generation_id=generation_id)


class IndexingService:
    """Indexes one generation's chunks and activates it. Performs no I/O itself."""

    def __init__(
        self,
        *,
        recorder: IndexingRecorder,
        embedder: Embedder,
        index: VectorIndex,
        bm25: BM25Config | None = None,
        batch_size: int = 64,
    ) -> None:
        self._recorder = recorder
        self._embedder = embedder
        self._index = index
        self._bm25 = bm25 or BM25Config()
        self._batch_size = min(max(batch_size, 1), BATCH_CEILING)

    @property
    def embedder(self) -> Embedder:
        """The embedder this service used, so a caller can report what it did.

        Exposed rather than folded into :class:`RecordedIndexing`, which records
        what became queryable. How many vectors were recomputed is a property of
        the embedder and says nothing about whether the generation is correct.
        """
        return self._embedder

    def index(self, generation_id: UUID, *, retry: bool = False) -> RecordedIndexing:
        """Index every pending chunk, then activate the generation.

        Re-running after a transient failure needs no argument: the events are still
        pending and the loop resumes. ``retry`` is for the other case — a generation
        that was *marked* failed, whose events will not be picked up again until
        they are returned to pending. It is an explicit flag rather than automatic
        behaviour because resetting failures on every run would turn the attempt
        count into noise and hide a chunk that can never be indexed.

        Raises:
            NothingToIndexError: the generation does not exist or has no chunks.
            GenerationNotIndexableError: the generation is active, or is failed and
                ``retry`` was not asked for.
            IndexReconciliationError: PostgreSQL and the index disagree on how many
                chunks were written. The generation is marked failed rather than
                activated, and whichever generation was active stays active.
            EmbeddingError, VectorIndexError: the work could not be done. The
                generation stays shadow, so it is not queryable, and the previously
                active one is untouched (§11.13).
        """
        if retry:
            self._recorder.reopen(generation_id)
        _version_id, total, state = self._recorder.prepare(generation_id)
        if total == 0:
            raise NothingToIndexError(
                f"generation {generation_id} has no chunks queued for indexing"
            )
        if state != STATE_SHADOW:
            raise GenerationNotIndexableError(
                f"generation {generation_id} is {state!r}; only a shadow generation "
                "may be indexed, because re-indexing an active one would mutate "
                "what a reader is being served"
            )

        self._index.ensure_collection()

        indexed = 0
        while True:
            batch = self._recorder.pending(generation_id, self._batch_size)
            if not batch:
                break
            indexed += self._write(batch, generation_id)

        if indexed == 0:
            # Every event was already completed by an earlier run. The generation
            # may still be shadow if that run died between its last batch and
            # activation, so reconciliation and activation still have to happen.
            return self._finish(
                generation_id, total=total, indexed=0, already_complete=True
            )
        return self._finish(
            generation_id, total=total, indexed=indexed, already_complete=False
        )

    def _write(self, batch: Sequence[PendingChunk], generation_id: UUID) -> int:
        """Embed and upsert one batch outside any transaction, then record it.

        **A transient failure leaves the events pending.** That is the embedding
        port's documented contract — an unavailable model "is retryable and leaves
        the outbox event pending" — and it is what makes recovery require no flag:
        Ollama comes back, ``index`` runs again, and the loop resumes at the first
        chunk that never made it. Marking them failed instead would turn a restart
        into an operator task.

        **A permanent failure marks them failed.** A shape mismatch or an input the
        model refuses will fail identically on every retry, so leaving it pending
        would retry it forever and §29.10's ``attempts`` column exists to make that
        visible rather than to permit it.

        A permanent fault fails its whole batch, including chunks that would have
        succeeded. Acceptable today because the only per-chunk permanent fault is
        over-long input, and the chunker's cap puts the largest child at 3,808
        characters against a 6,000-character bound — so the state is currently
        unreachable. If it becomes reachable, the fix is to re-embed the batch one
        chunk at a time to isolate the offender, not to widen the bound.

        Either way the generation is **not** marked failed here. It is shadow, which
        already means "not queryable", and reconciliation refuses to activate it.
        Failing it here would make the transient case unrecoverable without
        reopening it.
        """
        chunk_ids = [chunk.chunk_id for chunk in batch]
        try:
            vectors = self._embedder.embed_documents(
                [self._text_for(chunk) for chunk in batch]
            )
            self._index.upsert(
                [
                    self._as_indexed(chunk, dense, generation_id)
                    for chunk, dense in zip(batch, vectors, strict=True)
                ]
            )
        except (EmbeddingUnavailableError, VectorIndexUnavailableError):
            raise
        except Exception as error:
            self._recorder.record_failure(chunk_ids, type(error).__name__)
            raise

        self._recorder.complete(chunk_ids)
        return len(batch)

    def _text_for(self, chunk: PendingChunk) -> str:
        """The enriched string, which is what gets embedded (§14.2, §14.6)."""
        return embedded_text(
            chunk.text,
            ChunkContext(
                issuer_name=chunk.issuer_name,
                document_type=chunk.document_type,
                fiscal_period=chunk.fiscal_period,
                reporting_basis=chunk.reporting_basis,
                currency=chunk.currency,
                heading_path=chunk.heading_path,
                page_numbers=chunk.page_numbers,
            ),
        )

    def _as_indexed(
        self,
        chunk: PendingChunk,
        dense: tuple[float, ...],
        generation_id: UUID,
    ) -> IndexedChunk:
        """Pair the vectors with the §20.2 filter payload.

        ``lexemes`` being ``None`` is a pipeline failure and not a term-free chunk:
        the chunking stage writes the vector for every row it inserts, so ``None``
        means this chunk was written by something that did not, and indexing it
        would put a dense-only point into the collection that no lexical query can
        ever reach. The empty string is the *legitimate* term-free case — eight
        development chunks analyse to no lexemes because every token is a stopword —
        and produces a point with no sparse vector, which is a real outcome.
        """
        if chunk.lexemes is None:
            raise IndexingError(
                f"chunk {chunk.chunk_id} has no lexical vector; it was not analysed "
                "by the chunking stage and would be unreachable by lexical search"
            )
        sparse = document_vector(chunk.lexemes, config=self._bm25)
        return IndexedChunk(
            chunk_id=chunk.chunk_id,
            dense=dense,
            sparse_indices=sparse.indices,
            sparse_values=sparse.values,
            payload=_payload(chunk, generation_id),
        )

    def _finish(
        self,
        generation_id: UUID,
        *,
        total: int,
        indexed: int,
        already_complete: bool,
    ) -> RecordedIndexing:
        """Reconcile, then activate. Never the other way round (§11.13, §29.11)."""
        completed = self._recorder.indexed_count(generation_id)
        if completed != total:
            self._recorder.fail(generation_id)
            raise IndexReconciliationError(
                f"generation {generation_id} has {total} queued chunk(s) and "
                f"{completed} recorded as indexed; refusing to activate a "
                "generation that would answer queries incompletely"
            )

        points = self._index.count(filters={"generation_id": str(generation_id)})
        if points != total:
            self._recorder.fail(generation_id)
            raise IndexReconciliationError(
                f"generation {generation_id} recorded {total} indexed chunk(s) and "
                f"the index holds {points} point(s); PostgreSQL and the index "
                "disagree, so neither can be trusted to bound a search"
            )

        self._recorder.activate(generation_id)
        return RecordedIndexing(
            generation_id=generation_id,
            indexed=indexed,
            already_complete=already_complete,
            activated=True,
            collection=self._index.collection,
        )


def _payload(chunk: PendingChunk, generation_id: UUID) -> dict[str, object]:
    """The §20.2 filter fields, omitting what is not known.

    A field absent from the payload cannot match a filter on it, which is the
    correct behaviour for a document whose issuer was never recorded: a query
    restricted to an issuer must not return a document that might be someone
    else's. Writing a placeholder would make every such document match each other.

    Three fields exist here that the chunk also carries in PostgreSQL, and the
    duplication is the point — a value only a relational query can see cannot bound
    a vector search:

    * ``section`` is the outermost heading. Without it §20.2 can report which
      section a result came from but not exclude one, which is what a question
      about financial figures needs when a DRHP's boilerplate risk factors run to
      a hundred pages.
    * ``fiscal_year`` is derived from ``period_end``, the one date that is reliably
      orderable. ``fiscal_period`` is the document's own words and two development
      filings share the string "FY2024-25".
    * ``page_numbers`` is the list, so a filter can restrict to a page span.
    """
    payload: dict[str, object] = {
        "generation_id": str(generation_id),
        "document_version_id": str(chunk.document_version_id),
        "evidence_type": chunk.evidence_type,
    }
    if chunk.page_numbers:
        payload["page_numbers"] = list(chunk.page_numbers)
    if chunk.heading_path:
        payload["section"] = chunk.heading_path[0]
    if chunk.period_end is not None:
        payload["fiscal_year"] = chunk.period_end.year
    for key, value in (
        ("issuer_name", chunk.issuer_name),
        ("fiscal_period", chunk.fiscal_period),
        ("reporting_basis", chunk.reporting_basis),
        ("document_type", chunk.document_type),
    ):
        if value is not None:
            payload[key] = value
    return payload


def build_indexing_service() -> IndexingService:
    """Wire indexing to the configured model, index and database."""
    from finsight.config.settings import get_settings
    from finsight.embedding.cache import build_cached_embedder
    from finsight.vector_index.qdrant_index import build_vector_index

    settings = get_settings()
    return IndexingService(
        recorder=TransactionalIndexingRecorder(),
        embedder=build_cached_embedder(settings),
        index=build_vector_index(settings),
        batch_size=settings.embedding_batch_size,
    )


__all__ = [
    "GenerationNotIndexableError",
    "IndexReconciliationError",
    "IndexingError",
    "IndexingService",
    "NothingToIndexError",
    "RecordedIndexing",
    "TransactionalIndexingRecorder",
    "build_indexing_service",
]
