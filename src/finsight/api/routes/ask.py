"""The answer route: the first surface that serves a composed answer over HTTP.

**Authentication is on the router, not the endpoint** (§28.2). See :mod:`finsight.api.auth` for
why that distinction is the whole control.

**Defined with ``def``, not ``async def``.** Everything below it blocks — SQLAlchemy, the Qdrant
and Ollama clients, and a CPU-bound cross-encoder forward pass — and generation blocks for
minutes. Declared ``async``, that would hold the event loop and stall every other request in the
process, health probes included. A plain ``def`` makes FastAPI run it in a threadpool.

**This request is slow, and the contract says so rather than hiding it.** Measured on this host
with no GPU offload available, prompt evaluation runs at roughly 26 tokens per second and decode
at roughly 3.3, so a question over eight passages takes **two to four minutes**. A client must
set its read timeout accordingly; a default 30-second timeout will abandon a request the server
then completes and records. Asynchronous submission would fix the shape of this, and it is an
architecture change rather than a route, so it is not made here.

**An abstention is 200, not 4xx.** Declining to answer is a correct outcome (§26.1) — the
passages may not contain the answer, or the Evidence Gate may have removed every claim — and a
status code in the 400s would tell a client its *request* was wrong. The decision is in the body,
where §27.11 puts it.

**Losing the model is 200 as well** (§26.10). Generation failures are reported as degradation
flags with the evidence still attached, because a caller given five cited passages and told no
prose was composed has more than a caller given a 503.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from finsight.api.auth import require_token
from finsight.api.dependencies import get_ask_service
from finsight.api.schemas.ask import (
    AskRequest,
    AskResponse,
    ClaimCitationModel,
    EvidencePassageModel,
    FindingModel,
    ReleasedClaimModel,
    WithheldClaimModel,
)
from finsight.embedding.port import EmbeddingShapeError
from finsight.generation.decision import ReleasedClaim, WithheldClaim
from finsight.generation.evidence import EvidencePassage
from finsight.generation.service import AskedAnswer, AskService
from finsight.generation.validation import Finding
from finsight.reranking.port import RerankShapeError
from finsight.retrieval.contracts import RetrievalFilters
from finsight.vector_index.port import VectorIndexShapeError

router = APIRouter(
    prefix="/v1",
    tags=["generation"],
    dependencies=[Depends(require_token)],
)


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Answer a question from the indexed corpus, with citations",
    description=(
        "Retrieves evidence, composes an answer under a JSON schema, resolves every "
        "citation to stored source spans, and applies the Evidence Gate before releasing "
        "anything.\n\n"
        "**This call takes minutes, not seconds.** Measured on a host with no GPU offload: "
        "two to four minutes for a question over eight passages. Set a read timeout of at "
        "least 600 seconds.\n\n"
        "An abstention and a failed model are both 200 with the reason in the body."
    ),
    responses={
        401: {"description": "Missing or invalid bearer token."},
        503: {"description": "Authentication unconfigured, or a configuration "
                             "disagreement between the index and the models."},
    },
)
def ask(
    request: AskRequest,
    service: Annotated[AskService, Depends(get_ask_service)],
) -> AskResponse:
    """Answer one question and return the decision, the evidence and what was withheld."""
    try:
        answer = service.ask(
            request.question, filters=_filters_of(request), limit=request.limit
        )
    except (VectorIndexShapeError, EmbeddingShapeError, RerankShapeError) as error:
        # Not survivable and not the caller's fault: the collection, the embedding model or
        # the reranker disagree about shape. Degrading would mean answering from a
        # configuration known to be inconsistent, which §20.12's degradation paths
        # deliberately do not cover. The message names the disagreement and no document
        # content.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Retrieval configuration is inconsistent: {error}",
        ) from error

    return _as_response(answer)


def _filters_of(request: AskRequest) -> RetrievalFilters:
    """Map the wire filters onto the §20.2 filter set.

    The generation bound is absent on purpose and is not the caller's to supply:
    :meth:`RetrievalFilters.as_payload` adds the active generations itself, so a request
    cannot widen the evidence to superseded or still-building content.
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


def _as_response(answer: AskedAnswer) -> AskResponse:
    """Project the answer onto its wire shape.

    Passages are serialized in :attr:`EvidenceSet.ranked` order rather than presentation
    order. The prompt places the strongest passages at both ends because a long context loses
    its middle; a reader wants them numbered one downwards, and the ids mean the same thing
    either way.
    """
    decision = answer.decision
    evidence = answer.evidence
    return AskResponse(
        question=answer.question,
        decision=decision.decision.value,
        support_band=decision.support_band.value,
        reason_codes=decision.reason_codes,
        degraded=decision.degraded,
        claims=tuple(_released(claim) for claim in decision.released),
        withheld=tuple(_withheld(claim) for claim in decision.withheld),
        passages=tuple(_passage(passage) for passage in evidence.ranked),
        model=answer.model,
        answer_id=answer.answer_id,
        evidence_budget_chars=evidence.budget_chars,
        evidence_used_chars=evidence.used_chars,
        passages_considered=evidence.considered,
        passages_dropped_for_budget=evidence.dropped_for_budget,
        passages_merged=evidence.merged,
        passages_expanded=evidence.expanded,
        prompt_tokens=answer.prompt_tokens,
        completion_tokens=answer.completion_tokens,
        timings_ms=dict(answer.timings_ms),
    )


def _released(claim: ReleasedClaim) -> ReleasedClaimModel:
    """One released claim, with its passage labels alongside its spans.

    The deduplicated passage ids are computed here rather than left to the client. There is one
    citation per *source element*, so a claim resting on a single passage built from eleven table
    rows carries eleven citations naming the same passage — a client marking each one renders
    ``[4][4][4]...``. The CLI already deduplicated; putting it in the contract means the next
    client does not have to discover the trap.
    """
    return ReleasedClaimModel(
        text=claim.text,
        cited_passage_ids=tuple(
            sorted({citation.passage_id for citation in claim.citations})
        ),
        citations=tuple(
            ClaimCitationModel(
                passage_id=citation.passage_id,
                source_element_id=citation.source_element_id,
                locator=citation.locator,
                text=citation.text,
            )
            for citation in claim.citations
        ),
        disclosures=tuple(_finding(finding) for finding in claim.disclosures),
    )


def _withheld(claim: WithheldClaim) -> WithheldClaimModel:
    return WithheldClaimModel(
        text=claim.text,
        findings=tuple(_finding(finding) for finding in claim.findings),
    )


def _finding(finding: Finding) -> FindingModel:
    return FindingModel(
        code=finding.code,
        severity=finding.severity.value,
        detail=finding.detail,
    )


def _passage(passage: EvidencePassage) -> EvidencePassageModel:
    return EvidencePassageModel(
        id=passage.id,
        chunk_id=passage.chunk_id,
        text=passage.text,
        heading_path=passage.heading_path,
        page_numbers=passage.page_numbers,
        evidence_type=passage.evidence_type,
        issuer_name=passage.issuer_name,
        document_type=passage.document_type,
        fiscal_period=passage.fiscal_period,
        reporting_basis=passage.reporting_basis,
        rerank_score=passage.rerank_score,
        expanded=passage.expanded,
        stands_for=passage.stands_for,
    )
