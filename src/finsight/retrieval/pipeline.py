"""The retrieval pipeline: retrieve wide, fuse, rerank, return narrow.

**Depth and the final count are different numbers, and conflating them was a real
gap.** Until this stage existed, one ``limit`` bounded both how many candidates were
retrieved and how many were returned, so asking for 5 results retrieved only 5 per type
and the reranker had nothing to reorder. §23.5 keeps "input depth and final evidence
count" separate and baseline-driven; production practice puts it more bluntly — a wide
window narrowed by fusion and then by a reranker, because a true answer that fusion
dropped to rank 40 never reaches a reranker that only sees 5.

**Depth is bought with latency, measured rather than assumed.** Reranking costs roughly
87 ms per candidate on this host's CPU, against 337 ms for the whole hybrid retrieval
before it. So depth is the dominant term in a query's cost and the default of 25 is a
latency decision, not a quality one — quality needs the golden set §22.6 requires.

**The reranker is optional by construction, not by accident** (§23.1). Losing it
returns the fused order with a flag (§23.8), and it can be switched off deliberately so
the two orderings can be compared on identical candidates — which is the comparison
§23.9's selection will need.
"""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from finsight.indexing.enrichment import ChunkContext, embedded_text
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.chunks import (
    ChunkRepository,
    Citation,
    PendingChunk,
)
from finsight.reranking.port import Passage, Reranker, RerankUnavailableError
from finsight.retrieval.contracts import RetrievalFilters
from finsight.retrieval.hybrid import HybridRetrievalService
from finsight.retrieval.selection import Collapsed, dedupe

__all__ = [
    "DEGRADED_RERANKER_UNAVAILABLE",
    "Result",
    "RetrievalPipeline",
    "RetrievedChunk",
]

DEGRADED_RERANKER_UNAVAILABLE = "reranker_unavailable"
"""§23.8: failure returns fused order plus a degradation flag.

Carried separately from the retrieval flags because the consequence differs. A dense
outage changes *which* candidates were considered; a reranker outage changes only their
*order*, and the candidates are the same ones. A reader told "results may be ordered
less well" is being told something different from "half the index was unreachable".
"""


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One result, with every score that produced it (§20.13).

    All three scores are kept rather than collapsed into the final one, because they
    answer different questions: ``fused_score`` why it survived fusion,
    ``rerank_score`` why it ended up here, and ``contributions`` which retrievers found
    it and at what rank. A trace holding only the final order cannot explain itself.

    ``rerank_score`` is ``None`` when the reranker did not run. It is a logit, not a
    probability and not a confidence — §27 forbids presenting a support band as a
    correctness probability, and this is further from one.
    """

    chunk_id: UUID
    rank: int
    text: str
    heading_path: tuple[str, ...]
    page_numbers: tuple[int, ...]
    evidence_type: str
    issuer_name: str | None
    fiscal_period: str | None
    fused_score: float
    contributions: Mapping[str, int]
    rerank_score: float | None = None

    citations: tuple[Citation, ...] = ()
    """The source regions this passage was built from (§14.7, §14.9).

    Not decoration. A chunk is a *retrieval* representation and §14.1 keeps evidence
    with the source representation, so a result that cannot name its source elements
    cannot be used as evidence — which is the whole point of the phase.
    """


@dataclass(frozen=True, slots=True)
class Result:
    """What one search produced, and how."""

    candidates: tuple[RetrievedChunk, ...]
    degraded: tuple[str, ...] = ()
    depth: int = 0
    reranked: bool = False
    reranker_model: str | None = None
    lexical_retriever: str = ""
    dense_used: bool = True
    fusion_version: str = ""
    collapsed: tuple[Collapsed, ...] = ()
    """Candidates removed as duplicate evidence (§20.9), and what absorbed them.

    Reported rather than dropped silently, because "why are there four results when I
    asked for five" is a question the caller should be able to answer.
    """

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded)


@dataclass(frozen=True, slots=True)
class RetrievalPipeline:
    """Hybrid retrieval at depth, then reranking down to the final count."""

    hybrid: HybridRetrievalService
    reranker: Reranker | None = None
    depth: int = 25
    session_scope_factory: Callable[[], AbstractContextManager[Session]] = (
        session_scope
    )

    def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 5,
    ) -> Result:
        """Retrieve ``max(depth, limit)`` candidates and return the best ``limit``.

        ``max`` rather than ``depth``, so asking for more results than the configured
        depth widens the window instead of silently returning fewer than requested.

        Raises:
            ValueError: ``limit`` is not positive.
            VectorIndexShapeError, RerankShapeError: a configuration disagreement. Not
                survivable on either path.
        """
        if limit < 1:
            raise ValueError(f"limit must be positive, got {limit}")
        window = max(self.depth, limit)

        fused = self.hybrid.search(query, filters=filters, limit=window)
        if not fused.candidates:
            return Result(
                candidates=(),
                degraded=fused.degraded,
                depth=window,
                lexical_retriever=fused.lexical_retriever,
                dense_used=fused.dense_used,
                fusion_version=fused.fusion_version,
            )

        order = [candidate.chunk_id for candidate in fused.candidates]
        with self.session_scope_factory() as session:
            repository = ChunkRepository(session)
            loaded = repository.load_context(chunk_ids=order)
            citations = repository.citations_for(chunk_ids=order)
            regions = repository.source_elements_for(chunk_ids=order)
        by_id = {chunk.chunk_id: chunk for chunk in loaded}

        degraded = list(fused.degraded)
        ranked = order
        scores: dict[UUID, float] = {}
        reranked = False
        reranker_model: str | None = None

        if self.reranker is not None:
            passages = [
                Passage(chunk_id=chunk_id, text=_reranker_input(by_id[chunk_id]))
                for chunk_id in order
                if chunk_id in by_id
            ]
            try:
                scored = self.reranker.rerank(query, passages)
            except RerankUnavailableError:
                # §23.8: keep the fused order, say so, answer anyway.
                degraded.append(DEGRADED_RERANKER_UNAVAILABLE)
            else:
                ranked = [item.chunk_id for item in scored]
                scores = {item.chunk_id: item.score for item in scored}
                reranked = True
                reranker_model = self.reranker.model

        # §20.9 before the limit, not after: collapsing duplicates afterwards would
        # return fewer results than asked for, and the whole point is that the budget
        # is spent on distinct evidence.
        distinct, collapsed = dedupe(
            [_Ordered(chunk_id=chunk_id) for chunk_id in ranked], elements=regions
        )

        fused_by_id = {c.chunk_id: c for c in fused.candidates}
        results: list[RetrievedChunk] = []
        for chunk_id in (item.chunk_id for item in distinct):
            chunk = by_id.get(chunk_id)
            if chunk is None:
                # Resolved to nothing: the chunk was removed between retrieval and
                # resolution. Dropping it is correct — §10.7 keeps PostgreSQL
                # authoritative, so a chunk it no longer holds is not evidence.
                continue
            candidate = fused_by_id[chunk_id]
            results.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    rank=len(results) + 1,
                    text=chunk.text,
                    heading_path=chunk.heading_path,
                    page_numbers=chunk.page_numbers,
                    evidence_type=chunk.evidence_type,
                    issuer_name=chunk.issuer_name,
                    fiscal_period=chunk.fiscal_period,
                    fused_score=candidate.score,
                    contributions=candidate.contributions,
                    rerank_score=scores.get(chunk_id),
                    citations=citations.get(chunk_id, ()),
                )
            )
            if len(results) >= limit:
                break

        return Result(
            candidates=tuple(results),
            degraded=tuple(degraded),
            depth=window,
            reranked=reranked,
            reranker_model=reranker_model,
            lexical_retriever=fused.lexical_retriever,
            dense_used=fused.dense_used,
            fusion_version=fused.fusion_version,
            collapsed=collapsed,
        )


@dataclass(frozen=True, slots=True)
class _Ordered:
    """A rank-carrying wrapper, so deduplication works on ids alone.

    ``dedupe`` is generic over anything with ``chunk_id``; at this point the ordering is
    just a list of ids, and wrapping them is cheaper than threading full candidates
    through a stage that only compares source regions.
    """

    chunk_id: UUID


def _reranker_input(chunk: PendingChunk) -> str:
    """The deterministic context §23.6 permits, plus the chunk body.

    Built by the same projection the embedding uses, so the reranker and the index
    cannot disagree about a chunk's context, and neither can introduce a generated
    summary — which §23.6 forbids being received "as fact".
    """
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


def build_retrieval_pipeline() -> RetrievalPipeline:
    """Wire the whole retrieval path from configuration."""
    from finsight.config.settings import get_settings
    from finsight.reranking.cross_encoder import build_reranker
    from finsight.retrieval.hybrid import build_hybrid_retrieval_service

    settings = get_settings()
    return RetrievalPipeline(
        hybrid=build_hybrid_retrieval_service(),
        reranker=build_reranker() if settings.rerank_enabled else None,
        depth=settings.rerank_depth,
    )
