"""The lexical retrieval stage: BM25, falling back to full-text search.

One decision shapes this module: **which failures are survivable.** §20.12 requires
degradation rather than failure when the vector index is unavailable, and §10.9
classifies Qdrant as degradable for that reason. So an outage falls back and flags
it; anything else propagates.

The distinction is not cosmetic. ``VectorIndexUnavailableError`` means "try again
later or try something else"; ``VectorIndexShapeError`` means the collection
disagrees with the configuration, and falling back there would paper over a model or
dimensionality mismatch that will corrupt every subsequent write. The embedding port
draws the same line for the same reason.
"""

from dataclasses import dataclass
from typing import Protocol

from finsight.retrieval.contracts import (
    DEGRADED_LEXICAL_FALLBACK,
    Candidate,
    LexicalResult,
    RetrievalFilters,
)
from finsight.vector_index.port import VectorIndexUnavailableError

__all__ = ["LexicalRetrievalService", "LexicalRetriever"]


class LexicalRetriever(Protocol):
    """A lexical retriever. Structural, so neither adapter subclasses anything."""

    @property
    def name(self) -> str: ...

    def search(
        self, query: str, *, filters: RetrievalFilters, limit: int
    ) -> tuple[Candidate, ...]: ...


@dataclass(frozen=True, slots=True)
class LexicalRetrievalService:
    """Searches with BM25, and with full-text search when BM25 is unreachable."""

    primary: LexicalRetriever
    fallback: LexicalRetriever

    def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 20,
    ) -> LexicalResult:
        """Candidates in rank order, with a flag if the fallback answered.

        An **empty result is not a degradation**. A query of nothing but stopwords,
        or one whose terms appear nowhere in the active generations, legitimately
        matches nothing — and flagging that would make "no answer exists" look like
        "the system is unwell", which is the opposite of what §27 needs to tell a
        reader.

        Raises:
            VectorIndexShapeError: the collection disagrees with the configuration.
                Not survivable — it means the index and the model have diverged.
            ValueError: ``limit`` is not positive.
        """
        if limit < 1:
            raise ValueError(f"limit must be positive, got {limit}")
        resolved = filters or RetrievalFilters()

        try:
            candidates = self.primary.search(query, filters=resolved, limit=limit)
        except VectorIndexUnavailableError:
            candidates = self.fallback.search(query, filters=resolved, limit=limit)
            return LexicalResult(
                candidates=candidates,
                retriever=self.fallback.name,
                degraded=(DEGRADED_LEXICAL_FALLBACK,),
            )

        return LexicalResult(candidates=candidates, retriever=self.primary.name)


def build_lexical_retrieval_service() -> LexicalRetrievalService:
    """Wire the lexical stage from configuration."""
    from finsight.retrieval.lexical import build_retrievers

    primary, fallback = build_retrievers()
    return LexicalRetrievalService(primary=primary, fallback=fallback)
