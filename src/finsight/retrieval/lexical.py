"""The lexical path: BM25 through Qdrant, PostgreSQL full-text search behind it.

ADR-005 splits BM25 between this process and Qdrant — saturated, length-normalised
term weights at index time, inverse document frequency at query time — and keeps
PostgreSQL full-text search as §20.12's degradation path. Both live here so the one
thing that must be identical between them is impossible to get wrong: **the filters**.

**The fallback is not an equivalent.** ``ts_rank_cd`` has no term-frequency
saturation, no length normalisation and no IDF, so it orders the same corpus
differently. A result it produced carries a flag, and nothing downstream may treat a
flagged result as comparable with an unflagged one.

**Both retrievers resolve the active generations themselves.** §20.2 makes generation
a hard filter and §11.13 makes "active" the only visible state; a retriever that
accepted a generation list from its caller would let a mistake upstream serve
superseded evidence.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager

from sqlalchemy.orm import Session

from finsight.lexical.bm25 import BM25Config, query_vector
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.chunks import ChunkRepository
from finsight.persistence.repositories.generations import GenerationRepository
from finsight.retrieval.contracts import Candidate, RetrievalFilters, Retriever
from finsight.vector_index.port import Span, VectorIndex

__all__ = ["BM25Retriever", "FullTextRetriever"]


class BM25Retriever:
    """Sparse retrieval over Qdrant, with §20.2's filters applied in the engine.

    The query is analysed by :meth:`ChunkRepository.analyse_query` rather than here,
    which is what keeps the two sides of a match in step: the stored lexemes were
    produced by that same method's normalisation and configuration, and a query
    analysed any other way quietly stops matching.
    """

    name = Retriever.BM25

    def __init__(
        self,
        *,
        index: VectorIndex,
        text_search_config: str,
        bm25: BM25Config | None = None,
        session_scope_factory: Callable[
            [], AbstractContextManager[Session]
        ] = session_scope,
    ) -> None:
        self._index = index
        self._text_search_config = text_search_config
        self._bm25 = bm25 or BM25Config()
        self._session_scope = session_scope_factory

    def search(
        self, query: str, *, filters: RetrievalFilters, limit: int
    ) -> tuple[Candidate, ...]:
        """Raises VectorIndexError when the index cannot answer."""
        with self._session_scope() as session:
            generations = GenerationRepository(session).active_ids()
            if not generations:
                return ()
            analysed = ChunkRepository(session).analyse_query(
                query=query, text_search_config=self._text_search_config
            )

        vector = query_vector(analysed)
        if not vector.indices:
            # Every term was a stopword, or the query held no indexable token at
            # all. Matching nothing is correct and is not a failure — the dense side
            # still answers, and §20.12's flag is for an outage rather than for a
            # query with no lexical content.
            return ()

        matches = self._index.search_sparse(
            vector.indices,
            vector.values,
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


class FullTextRetriever:
    """PostgreSQL full-text search: §20.12's degradation path.

    Kept deliberately invocable rather than reachable only through a failure, because
    §9.7 still owes a recorded comparison between this and BM25 and an unreachable
    retriever cannot be compared.
    """

    name = Retriever.POSTGRES_FTS

    def __init__(
        self,
        *,
        text_search_config: str,
        session_scope_factory: Callable[
            [], AbstractContextManager[Session]
        ] = session_scope,
    ) -> None:
        self._text_search_config = text_search_config
        self._session_scope = session_scope_factory

    def search(
        self, query: str, *, filters: RetrievalFilters, limit: int
    ) -> tuple[Candidate, ...]:
        low, high = _year_bounds(filters.fiscal_year)
        with self._session_scope() as session:
            generations = GenerationRepository(session).active_ids()
            rows = ChunkRepository(session).search_full_text(
                query=query,
                text_search_config=self._text_search_config,
                generations=generations,
                limit=limit,
                issuer_name=filters.issuer_name,
                document_type=filters.document_type,
                fiscal_period=filters.fiscal_period,
                reporting_basis=filters.reporting_basis,
                evidence_type=filters.evidence_type,
                section=filters.section,
                document_version_id=filters.document_version_id,
                fiscal_year_low=low,
                fiscal_year_high=high,
            )
        return tuple(
            Candidate(chunk_id=chunk_id, score=score, rank=rank, retriever=self.name)
            for rank, (chunk_id, score) in enumerate(rows, start=1)
        )


def _year_bounds(value: int | Span | None) -> tuple[int | None, int | None]:
    """Flatten the year filter into the two bounds SQL takes.

    A bare ``int`` becomes an equality by bounding both ends, so the two retrievers
    agree: Qdrant matches the value exactly and this matches ``>= n AND <= n``.
    """
    if value is None:
        return None, None
    if isinstance(value, Span):
        return value.low, value.high
    return value, value


def build_retrievers(
    index: VectorIndex | None = None,
) -> tuple[BM25Retriever, FullTextRetriever]:
    """Wire both lexical retrievers from configuration."""
    from finsight.config.settings import get_settings
    from finsight.vector_index.qdrant_index import build_vector_index

    settings = get_settings()
    return (
        BM25Retriever(
            index=index if index is not None else build_vector_index(settings),
            text_search_config=settings.text_search_config,
        ),
        FullTextRetriever(text_search_config=settings.text_search_config),
    )


def candidate_ids(candidates: Sequence[Candidate]) -> tuple[str, ...]:
    """Chunk ids in rank order, for a trace or a log. Never the text."""
    return tuple(str(candidate.chunk_id) for candidate in candidates)
