"""Dense retrieval over Qdrant (§20.4).

**The query is not enriched and the documents are.** §20.4 describes dense matching
"over context-enriched retrieval representations", and the enrichment is on the
document side only: the indexer composes issuer, period, basis, section and page into
the embedded string, while a query is embedded as the reader typed it. That asymmetry
is deliberate and recorded in the limitation register — a query carries no document
context to add, and inventing one would mean guessing which filing the reader meant.

**Two failures degrade rather than propagate**, and they are different services:
Qdrant being unreachable and Ollama being unreachable both cost dense retrieval and
nothing else. Both are translated into the same unavailability, because a caller's
response to either is identical — fall back to lexical and flag it (§20.12).
"""

from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy.orm import Session

from finsight.embedding.port import Embedder, EmbeddingUnavailableError
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.generations import GenerationRepository
from finsight.retrieval.contracts import Candidate, RetrievalFilters, Retriever
from finsight.vector_index.port import VectorIndex, VectorIndexUnavailableError

__all__ = ["DenseRetriever"]


class DenseRetriever:
    """Nearest neighbours under §20.2's hard filters."""

    name = Retriever.DENSE

    def __init__(
        self,
        *,
        embedder: Embedder,
        index: VectorIndex,
        session_scope_factory: Callable[
            [], AbstractContextManager[Session]
        ] = session_scope,
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._session_scope = session_scope_factory

    def search(
        self, query: str, *, filters: RetrievalFilters, limit: int
    ) -> tuple[Candidate, ...]:
        """Raises:
        VectorIndexUnavailableError: Qdrant or the embedding model could not be
            reached. An embedding outage is reported as index unavailability
            because the caller's response is the same, and because the dense path
            is the only thing either of them costs.
        VectorIndexShapeError, EmbeddingShapeError: the configuration and the
            collection disagree. Not survivable.
        """
        with self._session_scope() as session:
            generations = GenerationRepository(session).active_ids()
        if not generations:
            return ()

        try:
            vector = self._embedder.embed_query(query)
        except EmbeddingUnavailableError as error:
            raise VectorIndexUnavailableError(
                f"dense retrieval unavailable: the embedding model could not be "
                f"reached ({error})"
            ) from error

        matches = self._index.search_dense(
            vector,
            limit=limit,
            filters=filters.as_payload(generations=generations),
        )
        return tuple(
            Candidate(
                chunk_id=match.chunk_id,
                score=match.score,
                rank=rank,
                retriever=self.name,
            )
            for rank, match in enumerate(matches, start=1)
        )
