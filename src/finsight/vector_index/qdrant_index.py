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
from typing import Any, Final

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ApiException, UnexpectedResponse

from finsight.config.settings import Settings, get_settings
from finsight.vector_index.port import (
    IndexedChunk,
    Match,
    VectorIndexShapeError,
    VectorIndexUnavailableError,
)

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
        try:
            if self._client.collection_exists(self._collection):
                self._verify()
                return
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=self._dimensions,
                        # Cosine, on a measured property: the embedding port
                        # promises unit vectors and Nomic was measured returning
                        # norm 1.0. With unit vectors cosine and dot agree, and
                        # cosine stays correct if a later model does not normalise.
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    SPARSE_VECTOR: models.SparseVectorParams(
                        # Server-side IDF. This is the half of BM25 that needs
                        # collection-wide statistics, which a client computing
                        # term weights per chunk cannot know (ADR-005).
                        modifier=models.Modifier.IDF
                    )
                },
            )
        except (UnexpectedResponse, ApiException) as error:
            raise VectorIndexUnavailableError(
                f"could not prepare collection {self._collection!r}: {error}"
            ) from error
        except OSError as error:
            raise VectorIndexUnavailableError(
                f"could not reach qdrant: {error}"
            ) from error

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
    """
    if not filters:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in filters.items()
        ]
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
