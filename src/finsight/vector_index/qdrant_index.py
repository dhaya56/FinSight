"""The Qdrant adapter: the only module in FinSight that imports a vector SDK.

Mirrors the rule confining boto3 to ``s3_store.py``, which ADR-001 showed costs
six files under a real backend swap.

**The collection name encodes the embedding model and width.** Changing the model
therefore targets a different collection automatically, instead of writing
768-dimensional vectors into a space built for 1024 — or worse, into a space of the
same width produced by a different model, where the shapes agree and every distance
is meaningless. That failure has no error to raise, so the naming removes the
possibility rather than detecting it.

**Point identifiers are deterministic** (§29.9), derived from the chunk and the
embedding configuration. Replaying an outbox event rewrites the same point, which
is what makes the indexer safe to re-run after a crash mid-batch.

**Sparse vectors carry BM25 with server-side IDF** (ADR-005). The term weights are
computed where the corpus statistics live — PostgreSQL — and Qdrant applies the
inverse document frequency at query time, which is the half of BM25 that needs to
know about the whole collection.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Any, Final, cast

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ApiException, UnexpectedResponse

from finsight.config.settings import Settings, get_settings
from finsight.vector_index.port import (
    IndexedChunk,
    Match,
    Span,
    VectorIndexShapeError,
    VectorIndexUnavailableError,
)

FILTERED_FIELDS: Final[dict[str, models.PayloadSchemaType]] = {
    "generation_id": models.PayloadSchemaType.KEYWORD,
    "document_version_id": models.PayloadSchemaType.KEYWORD,
    "issuer_name": models.PayloadSchemaType.KEYWORD,
    "fiscal_period": models.PayloadSchemaType.KEYWORD,
    "reporting_basis": models.PayloadSchemaType.KEYWORD,
    "document_type": models.PayloadSchemaType.KEYWORD,
    "evidence_type": models.PayloadSchemaType.KEYWORD,
    "section": models.PayloadSchemaType.KEYWORD,
    "fiscal_year": models.PayloadSchemaType.INTEGER,
    "page_numbers": models.PayloadSchemaType.INTEGER,
}
"""Payload fields §20.2 filters on, each with the index type its queries need.

**Measured, not assumed.** Filtering on an unindexed payload field makes Qdrant
scan every candidate's payload: at 20,000 points a filtered dense search took
23.5 ms without an index and 11.5 ms with one, a **2.0x difference** that widens
with the collection because the scan is linear.

Keyword for the identity dimensions, which are only ever matched exactly. **Integer
for the two that need ordering**, and that is the whole reason they were added:

* ``fiscal_year`` makes a period *orderable*. ``fiscal_period`` is text as the
  document states it — "FY2024-25", "nine months ended 31 December 2024" — which
  §16.8 requires and which cannot express "the last three years". Two development
  filings share one ``fiscal_period`` string, so it is not even a discriminator on
  its own.
* ``page_numbers`` is a list, and a Qdrant integer index matches a range against any
  element, so a filter can restrict to a page span.

``section`` is the first element of the heading path. It exists so §20.2 can exclude
a section rather than merely report one: a question about financial figures should
not have to compete with a hundred pages of boilerplate risk factors, and until now
the heading was recorded on every chunk and filterable on none.

Created after the collection and before any ingest, which is the order Qdrant wants —
it uses payload indexes to build filterable HNSW links, so indexes added afterwards
do not retroactively improve the graph. They are also created on an *existing*
collection, which is how a field added later becomes filterable without rebuilding;
that path cannot improve links already built, and at this corpus size no graph is
built at all (ENV-009).
"""

DENSE_VECTOR: Final = "dense"
SPARSE_VECTOR: Final = "bm25"

_CHUNK_ID: Final = "chunk_id"
"""Payload key holding the authoritative chunk identifier.

The only field a search needs back. Everything else in the payload exists to be
filtered on, never read — §10.7 forbids a vector-store record becoming truth, so
the text is resolved from PostgreSQL by this id.
"""

_POINT_NAMESPACE: Final = uuid.UUID("6f9b2a1e-0c3d-4f5a-8b7c-1d2e3f4a5b6c")
"""A fixed namespace for deriving point identifiers.

Arbitrary but constant. Changing it would orphan every existing point, so it is
written here rather than generated.
"""

_UNSAFE: Final = re.compile(r"[^a-zA-Z0-9_]+")


def collection_name(model: str, dimensions: int) -> str:
    """The collection a given model and width writes to.

    Derived rather than configured so the two cannot drift. A deployment that
    changed ``FINSIGHT_EMBEDDING_MODEL`` and forgot the collection would otherwise
    mix vector spaces silently.
    """
    return f"chunks__{_UNSAFE.sub('_', model)}__{dimensions}"


def point_id(chunk_id: uuid.UUID, *, model: str, config_version: str) -> str:
    """A deterministic identifier for one chunk's vectors (§29.9).

    Includes the model and configuration, so re-embedding under a new model
    produces different points rather than overwriting the old ones — which
    matters because the old generation may still be active and queryable while
    the new one is built (§11.12).
    """
    return str(
        uuid.uuid5(_POINT_NAMESPACE, f"{chunk_id}:{model}:{config_version}")
    )


class QdrantVectorIndex:
    """Stores chunk vectors in Qdrant, dense and sparse in one collection."""

    def __init__(
        self,
        *,
        client: QdrantClient,
        model: str,
        dimensions: int,
        config_version: str,
    ) -> None:
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._config_version = config_version
        self._collection = collection_name(model, dimensions)

    @property
    def collection(self) -> str:
        return self._collection

    def ensure_collection(self) -> None:
        """Create or verify the collection, and ensure every payload index exists.

        The index step runs on **both** paths. An earlier version created them only
        with the collection, so a field added to ``FILTERED_FIELDS`` later was
        filterable in a fresh deployment and silently unindexed in an existing one —
        same code, same configuration, a 2x latency difference and nothing to show
        why.
        """
        try:
            if self._client.collection_exists(self._collection):
                self._verify()
            else:
                self._create()
            self._index_payload()
        except (UnexpectedResponse, ApiException) as error:
            raise VectorIndexUnavailableError(
                f"could not prepare collection {self._collection!r}: {error}"
            ) from error
        except OSError as error:
            raise VectorIndexUnavailableError(
                f"could not reach qdrant: {error}"
            ) from error

    def _create(self) -> None:
        """Create the collection, then its payload indexes, then nothing else.

        The order is Qdrant's requirement rather than a preference: payload
        indexes inform the filterable HNSW links built during ingest, so indexes
        added after data is loaded do not retroactively improve the graph.

        HNSW parameters are stated explicitly even though they match the current
        defaults. ``m=16`` and ``ef_construct=100`` are the values a production
        baseline wants, and inheriting them silently means a future Qdrant that
        changes its defaults would change this collection's recall with nothing in
        the diff to show it.
        """
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                DENSE_VECTOR: models.VectorParams(
                    size=self._dimensions,
                    # Cosine rather than dot, and deliberately so. The embedding
                    # port promises unit vectors, which makes the two
                    # mathematically identical, and dot measured 1.4x faster
                    # (13.8 ms against 19.2 ms at 20,000 points). Cosine wins
                    # anyway because Qdrant normalises on insert: if the port's
                    # guarantee were ever violated, dot would rank by vector
                    # magnitude — longer documents first, silently — while cosine
                    # stays correct. Revisit if latency becomes the constraint.
                    distance=models.Distance.COSINE,
                    hnsw_config=models.HnswConfigDiff(m=16, ef_construct=100),
                )
            },
            sparse_vectors_config={
                SPARSE_VECTOR: models.SparseVectorParams(
                    # Server-side IDF: the half of BM25 that needs
                    # collection-wide statistics, which a client computing term
                    # weights per chunk cannot know (ADR-005).
                    modifier=models.Modifier.IDF
                )
                # No quantization here, ever. Sparse vectors carry meaning in
                # exact, irregular float weights across a wide index space, and
                # dense quantization schemes destroy that. Dense quantization is
                # also unused — see ENV-009 — because a financial answer built on
                # subtly-wrong neighbours is the failure this system exists to
                # avoid, and §22.6 has measured no recall cost to justify it.
            },
        )

    def _index_payload(self) -> None:
        """Create each filter field's index, idempotently.

        Qdrant treats re-creating an index with the same schema as a no-op, so this
        is safe to call on every ``ensure_collection`` rather than being guarded by a
        read of the existing schema — which would be a second round trip and a race.
        """
        for field, schema in FILTERED_FIELDS.items():
            self._client.create_payload_index(
                collection_name=self._collection,
                field_name=field,
                field_schema=schema,
                wait=True,
            )

    def _verify(self) -> None:
        """Refuse a collection whose shape disagrees with this configuration."""
        info = self._client.get_collection(self._collection)
        params: Any = info.config.params
        dense = (params.vectors or {}).get(DENSE_VECTOR)
        if dense is None or dense.size != self._dimensions:
            found = "absent" if dense is None else dense.size
            raise VectorIndexShapeError(
                f"collection {self._collection!r} holds dense vectors of {found}; "
                f"this configuration produces {self._dimensions}"
            )
        if SPARSE_VECTOR not in (params.sparse_vectors or {}):
            raise VectorIndexShapeError(
                f"collection {self._collection!r} has no {SPARSE_VECTOR!r} sparse "
                "vector; lexical retrieval would silently return nothing"
            )

    def upsert(self, chunks: Sequence[IndexedChunk]) -> int:
        if not chunks:
            return 0
        points = [
            models.PointStruct(
                id=point_id(
                    chunk.chunk_id,
                    model=self._model,
                    config_version=self._config_version,
                ),
                vector=self._vectors(chunk),
                payload={**chunk.payload, _CHUNK_ID: str(chunk.chunk_id)},
            )
            for chunk in chunks
        ]
        try:
            self._client.upsert(
                collection_name=self._collection, points=points, wait=True
            )
        except (UnexpectedResponse, ApiException) as error:
            raise _translate(error, self._dimensions) from error
        except OSError as error:
            raise VectorIndexUnavailableError(
                f"could not reach qdrant: {error}"
            ) from error
        return len(points)

    def _vectors(self, chunk: IndexedChunk) -> dict[str, Any]:
        """Build the named vectors, refusing a malformed sparse pair.

        Mismatched indices and values is a pipeline bug that Qdrant would either
        reject obscurely or, worse, accept — and a point whose sparse weights are
        shifted against their terms scores nonsense on every lexical query while
        looking perfectly healthy.
        """
        if len(chunk.sparse_indices) != len(chunk.sparse_values):
            raise VectorIndexShapeError(
                f"chunk {chunk.chunk_id} has {len(chunk.sparse_indices)} sparse "
                f"indices and {len(chunk.sparse_values)} values; the pairing "
                "would be meaningless"
            )
        vectors: dict[str, Any] = {DENSE_VECTOR: list(chunk.dense)}
        if chunk.sparse_indices:
            vectors[SPARSE_VECTOR] = models.SparseVector(
                indices=list(chunk.sparse_indices),
                values=list(chunk.sparse_values),
            )
        return vectors

    def search_dense(
        self,
        vector: Sequence[float],
        *,
        limit: int,
        filters: Mapping[str, object] | None = None,
    ) -> tuple[Match, ...]:
        return self._query(
            query=list(vector), using=DENSE_VECTOR, limit=limit, filters=filters
        )

    def search_sparse(
        self,
        indices: Sequence[int],
        values: Sequence[float],
        *,
        limit: int,
        filters: Mapping[str, object] | None = None,
    ) -> tuple[Match, ...]:
        if not indices:
            # A query with no indexable terms matches nothing. Returning empty is
            # correct and is not a failure: the dense side still answers.
            return ()
        return self._query(
            query=models.SparseVector(indices=list(indices), values=list(values)),
            using=SPARSE_VECTOR,
            limit=limit,
            filters=filters,
        )

    def _query(
        self,
        *,
        query: Any,
        using: str,
        limit: int,
        filters: Mapping[str, object] | None,
    ) -> tuple[Match, ...]:
        try:
            response = self._client.query_points(
                collection_name=self._collection,
                query=query,
                using=using,
                limit=limit,
                query_filter=_as_filter(filters),
                # The chunk id, and only the chunk id. The point id is derived
                # from chunk + model + config (§29.9) and resolves to nothing in
                # PostgreSQL, so returning it would make every search find
                # matches that cannot be read back.
                with_payload=[_CHUNK_ID],
            )
        except (UnexpectedResponse, ApiException) as error:
            raise VectorIndexUnavailableError(
                f"qdrant rejected a {using} query: {error}"
            ) from error
        except OSError as error:
            raise VectorIndexUnavailableError(
                f"could not reach qdrant: {error}"
            ) from error

        return tuple(_match(point) for point in response.points)

    def count(self, *, filters: Mapping[str, object] | None = None) -> int:
        try:
            return self._client.count(
                collection_name=self._collection,
                count_filter=_as_filter(filters),
                exact=True,
            ).count
        except (UnexpectedResponse, ApiException) as error:
            raise VectorIndexUnavailableError(
                f"qdrant rejected a count: {error}"
            ) from error
        except OSError as error:
            raise VectorIndexUnavailableError(
                f"could not reach qdrant: {error}"
            ) from error

    def close(self) -> None:
        self._client.close()


def _match(point: Any) -> Match:
    """Read the chunk identifier a search returned.

    Raises rather than falling back to the point id. A fallback is what hid this
    the first time: with the payload not requested, every search returned derived
    point identifiers that resolve to no chunk, and the symptom was an empty
    result set rather than an error.
    """
    payload = point.payload or {}
    if _CHUNK_ID not in payload:
        raise VectorIndexShapeError(
            f"point {point.id} carries no {_CHUNK_ID!r}; it cannot be resolved "
            "against PostgreSQL"
        )
    return Match(chunk_id=uuid.UUID(str(payload[_CHUNK_ID])), score=point.score)


def _as_filter(filters: Mapping[str, object] | None) -> models.Filter | None:
    """Turn the §20.2 filter fields into a conjunction.

    Every field must match. These are *hard* filters — §20.2 constrains both
    retrievers by issuer, period, basis, scope, generation, security state and
    authorization scope — and §7 forbids semantic similarity overriding them, so
    an OR here would let a close vector escape its scope.

    Three value shapes, because one is not enough for §20.2:

    * a scalar matches exactly;
    * a **sequence** matches any of its members, which is how a search is bounded to
      the *set* of active generations — the one filter retrieval must never omit;
    * a :class:`Span` matches a range, which is how a period becomes orderable.

    An **empty sequence matches nothing**, and that is deliberate rather than an edge
    case to smooth over. It arises when no generation is active, and returning
    everything there would serve chunks from superseded or still-building
    generations — the precise failure §11.13's activation machinery exists to
    prevent.
    """
    if not filters:
        return None
    return models.Filter(must=[_condition(key, value) for key, value in filters.items()])


def _condition(key: str, value: object) -> models.FieldCondition:
    if isinstance(value, Span):
        return models.FieldCondition(
            key=key, range=models.Range(gte=value.low, lte=value.high)
        )
    if isinstance(value, str | bool | int | float):
        return models.FieldCondition(key=key, match=models.MatchValue(value=value))
    if isinstance(value, Sequence):
        members = list(value)
        if all(isinstance(member, int) and not isinstance(member, bool) for member in members):
            return models.FieldCondition(
                key=key, match=models.MatchAny(any=cast("list[int]", members))
            )
        return models.FieldCondition(
            key=key, match=models.MatchAny(any=[str(member) for member in members])
        )
    raise VectorIndexShapeError(
        f"filter {key!r} has value of type {type(value).__name__}, which is not a "
        "scalar, a sequence or a Span"
    )


def _translate(error: Exception, dimensions: int) -> Exception:
    """Separate a shape disagreement from an outage.

    Qdrant reports a dimensionality mismatch as a generic bad request, and the two
    need different responses: one is retryable and one means the model changed
    without the collection changing.
    """
    text = str(error).lower()
    if "dimension" in text or "vector size" in text:
        return VectorIndexShapeError(
            f"qdrant refused vectors of {dimensions} dimensions: {error}"
        )
    return VectorIndexUnavailableError(f"qdrant rejected a write: {error}")


def build_vector_index(settings: Settings | None = None) -> QdrantVectorIndex:
    """Wire the index from configuration."""
    resolved = settings if settings is not None else get_settings()
    return QdrantVectorIndex(
        client=QdrantClient(
            url=resolved.qdrant_url, timeout=int(resolved.qdrant_timeout_seconds)
        ),
        model=resolved.embedding_model,
        dimensions=resolved.embedding_dimensions,
        config_version=resolved.embedding_config_version,
    )


def is_vector_index_reachable() -> bool:
    """Whether Qdrant answers, for the readiness probe.

    Returns a plain bool and swallows the reason, matching the database probes:
    readiness is unauthenticated, and §30.17 keeps connection details out of
    anything a caller can see.

    Qdrant is **degradable**, not essential (§10.9, §20.12). Losing it costs dense
    retrieval, which falls back to the lexical path with a flag — so an unhealthy
    answer here must not take the service out of readiness.
    """
    index = None
    try:
        index = build_vector_index()
        index.ensure_collection()
    except Exception:
        return False
    else:
        return True
    finally:
        if index is not None:
            with suppress(Exception):
                index.close()
