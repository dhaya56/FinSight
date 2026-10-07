"""The retrieval route: the first surface that serves evidence over HTTP.

**Authentication is on the router, not the endpoint** (§28.2). See
:mod:`finsight.api.auth` for why that distinction is the whole control.

**Defined with ``def``, not ``async def``, and that is not an oversight.** Every
stage below it blocks: SQLAlchemy is synchronous, the Ollama and Qdrant clients are
synchronous, and the cross-encoder is a CPU-bound torch forward pass that holds the
interpreter for roughly two seconds. Declared ``async``, that work would run on the
event loop and stall every other request in the process, health probes included. A
plain ``def`` makes FastAPI run it in a threadpool, which is correct here.

**The pipeline is built once per process** and shared with the answer route; see
:mod:`finsight.api.dependencies` for why that caching lives there rather than here.

**This route generates nothing.** It returns source passages and the citations
behind them. No answer is composed, no number is restated, and no Evidence Gate runs
— ``POST /v1/ask`` is that surface, and a response from here must not be presented
as an answer.
"""

from dataclasses import replace
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from finsight.api.auth import require_token
from finsight.api.dependencies import get_pipeline
from finsight.api.schemas.search import (
    CitationModel,
    RetrievedChunkModel,
    SearchRequest,
    SearchResponse,
)
from finsight.embedding.port import EmbeddingShapeError
from finsight.reranking.port import RerankShapeError
from finsight.retrieval.contracts import RetrievalFilters
from finsight.retrieval.pipeline import RetrievalPipeline, RetrievedChunk
from finsight.vector_index.port import VectorIndexShapeError

router = APIRouter(
    prefix="/v1",
    tags=["retrieval"],
    dependencies=[Depends(require_token)],
)


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Retrieve source passages",
    responses={
        401: {"description": "Missing or invalid bearer token."},
        503: {"description": "Authentication unconfigured, or a configuration "
                             "disagreement between the index and the model."},
    },
)
def search(
    request: SearchRequest,
    pipeline: Annotated[RetrievalPipeline, Depends(get_pipeline)],
) -> SearchResponse:
    """Retrieve ranked passages for a question, with the citations behind each.

    Returns 200 with an empty candidate list when nothing matches, because a question
    the corpus cannot answer is a real outcome rather than an error — and because 404
    would say the *route* was missing.
    """
    effective = pipeline if request.rerank else _without_reranker(pipeline)

    started = perf_counter()
    try:
        result = effective.search(
            request.query,
            filters=_filters_of(request),
            limit=request.limit,
        )
    except (VectorIndexShapeError, EmbeddingShapeError, RerankShapeError) as error:
        # Not survivable and not the caller's fault: the collection, the embedding
        # model or the reranker disagree about shape. Degrading would mean answering
        # from a configuration known to be inconsistent, which §20.12's degradation
        # paths deliberately do not cover. The message names the disagreement and no
        # document content.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Retrieval configuration is inconsistent: {error}",
        ) from error
    elapsed_ms = int((perf_counter() - started) * 1000)

    return SearchResponse(
        query=request.query,
        candidates=tuple(_as_model(candidate) for candidate in result.candidates),
        degraded=result.degraded,
        depth=result.depth,
        reranked=result.reranked,
        reranker_model=result.reranker_model,
        lexical_retriever=result.lexical_retriever,
        dense_used=result.dense_used,
        fusion_version=result.fusion_version,
        collapsed_count=len(result.collapsed),
        elapsed_ms=elapsed_ms,
        timings_ms=dict(result.timings_ms),
    )


def _without_reranker(pipeline: RetrievalPipeline) -> RetrievalPipeline:
    """A copy of the pipeline with reranking off, leaving the original untouched.

    ``dataclasses.replace`` is avoided: the pipeline carries a loaded model, and
    rebuilding the dataclass per request is both cheap and non-mutating only because
    every field is shared by reference. Mutating the cached instance instead would
    leak one request's choice into the next.
    """
    return replace(pipeline, reranker=None)


def _filters_of(request: SearchRequest) -> RetrievalFilters:
    """Map the wire filters onto the §20.2 filter set.

    The generation bound is absent on purpose and is not the caller's to supply:
    :meth:`RetrievalFilters.as_payload` adds the active generations itself, so a
    request cannot widen the search to superseded or still-building evidence.
    """
    return RetrievalFilters(
        issuer_name=request.issuer_name,
        document_type=request.document_type,
        fiscal_period=request.fiscal_period,
        fiscal_year=request.fiscal_year,
        reporting_basis=request.reporting_basis,
        evidence_type=request.evidence_type,
        section=request.section,
    )


def _as_model(candidate: RetrievedChunk) -> RetrievedChunkModel:
    """Project one pipeline result onto its wire shape."""
    return RetrievedChunkModel(
        chunk_id=candidate.chunk_id,
        rank=candidate.rank,
        text=candidate.text,
        heading_path=candidate.heading_path,
        page_numbers=candidate.page_numbers,
        evidence_type=candidate.evidence_type,
        issuer_name=candidate.issuer_name,
        fiscal_period=candidate.fiscal_period,
        fused_score=candidate.fused_score,
        rerank_score=candidate.rerank_score,
        contributions=dict(candidate.contributions),
        citations=tuple(
            CitationModel(
                source_element_id=citation.source_element_id,
                locator=citation.locator,
                position=citation.position,
            )
            for citation in candidate.citations
        ),
    )
