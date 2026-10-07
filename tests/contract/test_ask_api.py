"""Contract tests for the answer route.

The answer path is substituted through ``dependency_overrides``, so these run with no database,
no index, no model and no reranker. What they assert is the contract a client depends on:

* the route is guarded, and no work runs for an unauthenticated request;
* the decision, the support band and the reasons are in the body — a client given only claim
  text cannot tell an answer from a dismantled one;
* an abstention and a failed model are **200**, because declining to answer is a correct
  outcome and §26.10 keeps the evidence when the model is lost;
* what the Evidence Gate withheld is returned, with its reasons;
* every citation carries the stored span, so a reader can follow it.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from finsight.api.app import create_app
from finsight.api.dependencies import get_ask_service
from finsight.config.settings import Settings, get_settings
from finsight.generation.decision import (
    AnswerDecision,
    Decision,
    ReleasedClaim,
    SupportBand,
    WithheldClaim,
)
from finsight.generation.evidence import EvidencePassage, EvidenceSet
from finsight.generation.resolution import ResolvedCitation
from finsight.generation.service import (
    DEGRADED_GENERATION_UNAVAILABLE,
    AskedAnswer,
)
from finsight.generation.validation import (
    REASON_MIXED_PERIOD,
    REASON_UNSUPPORTED_NUMERAL,
    Finding,
    Severity,
)
from finsight.retrieval.contracts import RetrievalFilters

TOKEN = "a-token-long-enough-to-pass-the-length-floor"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

ELEMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
ANSWER_ID = UUID("33333333-3333-3333-3333-333333333333")
SPAN = "Revenue from operations for the year was 1,62,990 crore."


def a_passage(identifier: int = 1, *, expanded: bool = False) -> EvidencePassage:
    return EvidencePassage(
        id=identifier,
        chunk_id=uuid4(),
        stands_for=(uuid4(),),
        text="The Company monitors credit risk through counterparty limits.",
        char_count=61,
        expanded=expanded,
        best_rank=identifier,
        heading_path=("7. Risk factors", "7.2 Credit risk"),
        page_numbers=(41, 42),
        issuer_name="Probe Limited",
        document_type="annual_report",
        fiscal_period="FY2024-25",
        reporting_basis="consolidated",
        rerank_score=-2.5,
    )


def an_evidence_set(*passages: EvidencePassage) -> EvidenceSet:
    chosen = passages or (a_passage(1), a_passage(2))
    return EvidenceSet(
        passages=chosen,
        budget_chars=22_000,
        used_chars=sum(passage.char_count for passage in chosen),
        considered=len(chosen) + 1,
        dropped_for_budget=1,
        expanded=sum(1 for passage in chosen if passage.expanded),
        merged=0,
        _by_id={passage.id: passage for passage in chosen},
    )


def a_released_claim(*, citations: tuple[ResolvedCitation, ...] | None = None) -> ReleasedClaim:
    return ReleasedClaim(
        text="The Company monitors credit risk through counterparty limits.",
        citations=citations
        if citations is not None
        else (
            ResolvedCitation(
                passage_id=1,
                source_element_id=ELEMENT_ID,
                locator="p. 41",
                text=SPAN,
            ),
        ),
        disclosures=(
            Finding(
                claim_index=0,
                code=REASON_MIXED_PERIOD,
                severity=Severity.DISCLOSE,
                detail="rests on passages from two or more periods: FY2023-24, FY2024-25",
            ),
        ),
    )


def a_withheld_claim() -> WithheldClaim:
    return WithheldClaim(
        text="Revenue was 99,999 crore.",
        findings=(
            Finding(
                claim_index=1,
                code=REASON_UNSUPPORTED_NUMERAL,
                severity=Severity.REMOVE,
                detail="states 99,999, which appears in none of the passages it cites",
            ),
        ),
    )


def an_answer(
    *,
    decision: Decision = Decision.PARTIAL,
    band: SupportBand = SupportBand.WEAK,
    released: tuple[ReleasedClaim, ...] | None = None,
    withheld: tuple[WithheldClaim, ...] | None = None,
    reason_codes: tuple[str, ...] = (REASON_UNSUPPORTED_NUMERAL,),
    degraded: tuple[str, ...] = (),
    evidence: EvidenceSet | None = None,
) -> AskedAnswer:
    return AskedAnswer(
        question="what does the company say about credit risk",
        decision=AnswerDecision(
            decision=decision,
            reason_codes=reason_codes,
            support_band=band,
            released=(a_released_claim(),) if released is None else released,
            withheld=(a_withheld_claim(),) if withheld is None else withheld,
            degraded=degraded,
        ),
        evidence=evidence if evidence is not None else an_evidence_set(),
        model="llama3.1:8b",
        answer_id=ANSWER_ID,
        timings_ms={"retrieval_ms": 900, "generation_ms": 132_000, "total_ms": 133_000},
        prompt_tokens=2_332,
        completion_tokens=60,
    )


@dataclass
class FakeAskService:
    """Records every call, and returns whatever it was constructed with.

    The returned answer carries the question it was asked, as the real service does. A fake
    that echoed a canned question instead would make the response's ``question`` field
    untestable — the route echoes what the service *processed*, not what the request body
    said, and those are the same value only because the service puts it there.
    """

    answer: AskedAnswer = field(default_factory=an_answer)
    error: Exception | None = None
    calls: list[tuple[str, RetrievalFilters | None, int]] = field(default_factory=list)

    def ask(
        self,
        question: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 8,
    ) -> AskedAnswer:
        self.calls.append((question, filters, limit))
        if self.error is not None:
            raise self.error
        return replace(self.answer, question=question)


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def service(app: FastAPI) -> FakeAskService:
    fake = FakeAskService()
    app.dependency_overrides[get_ask_service] = lambda: fake
    settings = Settings(
        postgres_password=SecretStr("not-a-real-password-just-a-fixture"),
        api_token=SecretStr(TOKEN),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return fake


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def post(client: TestClient, **body: object) -> httpx.Response:
    return client.post(
        "/v1/ask", json={"question": "credit risk", **body}, headers=AUTH
    )


class TestAuthentication:
    def test_the_route_is_guarded(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        response = client.post("/v1/ask", json={"question": "credit risk"})

        assert response.status_code == 401
        assert service.calls == []

    def test_no_work_runs_for_a_wrong_token(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        """Generation costs minutes; an unauthenticated request must not start one."""
        client.post(
            "/v1/ask",
            json={"question": "credit risk"},
            headers={"Authorization": "Bearer wrong-but-equally-long-token-value!!"},
        )

        assert service.calls == []


class TestRequest:
    def test_filters_reach_the_service_unaltered(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        post(
            client,
            limit=4,
            issuer_name="Probe Limited",
            fiscal_year=2025,
            reporting_basis="consolidated",
            section="7. Risk factors",
        )

        question, filters, limit = service.calls[0]
        assert question == "credit risk"
        assert filters == RetrievalFilters(
            issuer_name="Probe Limited",
            fiscal_year=2025,
            reporting_basis="consolidated",
            section="7. Risk factors",
        )
        assert limit == 4

    def test_an_unknown_filter_is_refused_rather_than_ignored(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        """A dropped filter would let an answer rest on out-of-scope evidence."""
        response = post(client, issuer="Probe Limited")

        assert response.status_code == 422
        assert service.calls == []

    def test_an_empty_question_is_refused(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        response = client.post("/v1/ask", json={"question": ""}, headers=AUTH)

        assert response.status_code == 422
        assert service.calls == []

    def test_a_limit_beyond_the_bound_is_refused(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        """Every admitted passage is prompt the model must read, so the bound is low."""
        assert post(client, limit=50).status_code == 422

    def test_the_default_limit_is_eight(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        post(client)

        assert service.calls[0][2] == 8


class TestResponse:
    def test_the_decision_is_in_the_body(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        """A client given only claim text cannot tell an answer from a partial one."""
        body = post(client).json()

        assert body["decision"] == "partial"
        assert body["support_band"] == "weak"
        assert body["reason_codes"] == [REASON_UNSUPPORTED_NUMERAL]
        assert body["question"] == "credit risk"
        assert body["model"] == "llama3.1:8b"
        assert body["answer_id"] == str(ANSWER_ID)

    def test_a_released_claim_carries_its_stored_spans(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        """ADR-009: the span text is read from the source, never written by the model."""
        body = post(client).json()

        citations = body["claims"][0]["citations"]
        assert citations == [
            {
                "passage_id": 1,
                "source_element_id": str(ELEMENT_ID),
                "locator": "p. 41",
                "text": SPAN,
            }
        ]

    def test_the_passage_labels_are_deduplicated(
        self, client: TestClient, app: FastAPI, service: FakeAskService
    ) -> None:
        """Measured against the real route: a passage over a table yields one citation per
        row, so a client marking each citation rendered ``[4][4][4]...`` eleven times. The
        contract supplies the distinct ids so it does not have to."""
        rows = tuple(
            ResolvedCitation(
                passage_id=4,
                source_element_id=uuid4(),
                locator="p. 228",
                text=f"table row {index}",
            )
            for index in range(11)
        )
        app.dependency_overrides[get_ask_service] = lambda: FakeAskService(
            an_answer(
                released=(
                    a_released_claim(
                        citations=(
                            ResolvedCitation(
                                passage_id=1,
                                source_element_id=ELEMENT_ID,
                                locator="p. 41",
                                text=SPAN,
                            ),
                            *rows,
                        )
                    ),
                )
            )
        )

        claim = post(client).json()["claims"][0]

        assert claim["cited_passage_ids"] == [1, 4]
        assert len(claim["citations"]) == 12

    def test_a_disclosure_travels_with_its_claim(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        """§27.8 requires the qualification beside what it qualifies, not in a footer."""
        body = post(client).json()

        disclosures = body["claims"][0]["disclosures"]
        assert disclosures[0]["code"] == REASON_MIXED_PERIOD
        assert disclosures[0]["severity"] == "disclose"
        assert "FY2023-24" in disclosures[0]["detail"]

    def test_withheld_claims_are_returned_with_their_reasons(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        """An invisible removal is indistinguishable from a model that never said it."""
        body = post(client).json()

        assert len(body["withheld"]) == 1
        assert body["withheld"][0]["text"] == "Revenue was 99,999 crore."
        assert body["withheld"][0]["findings"][0]["severity"] == "remove"

    def test_the_evidence_is_returned_in_rank_order(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        body = post(client).json()

        assert [passage["id"] for passage in body["passages"]] == [1, 2]
        first = body["passages"][0]
        assert first["page_numbers"] == [41, 42]
        assert first["heading_path"] == ["7. Risk factors", "7.2 Credit risk"]
        assert first["reporting_basis"] == "consolidated"
        assert first["rerank_score"] == pytest.approx(-2.5)

    def test_what_shaping_the_evidence_cost_is_reported(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        body = post(client).json()

        assert body["evidence_budget_chars"] == 22_000
        assert body["passages_considered"] == 3
        assert body["passages_dropped_for_budget"] == 1
        assert body["passages_expanded"] == 0

    def test_stage_timings_and_tokens_are_reported(
        self, client: TestClient, service: FakeAskService
    ) -> None:
        """One total cannot distinguish a slow index from a cold model."""
        body = post(client).json()

        assert body["timings_ms"]["generation_ms"] == 132_000
        assert body["prompt_tokens"] == 2_332
        assert body["completion_tokens"] == 60

    def test_an_expanded_passage_says_so(
        self, client: TestClient, app: FastAPI, service: FakeAskService
    ) -> None:
        """§20.8's expansion changes which text the model read, so it is reported."""
        app.dependency_overrides[get_ask_service] = lambda: FakeAskService(
            an_answer(evidence=an_evidence_set(a_passage(1, expanded=True)))
        )

        body = post(client).json()

        assert body["passages"][0]["expanded"] is True
        assert body["passages_expanded"] == 1


class TestAbstention:
    def test_an_abstention_is_two_hundred_with_the_reason_in_the_body(
        self, client: TestClient, app: FastAPI, service: FakeAskService
    ) -> None:
        """Declining to answer is §26.1's correct outcome; 4xx would blame the request."""
        app.dependency_overrides[get_ask_service] = lambda: FakeAskService(
            an_answer(
                decision=Decision.ABSTAINED,
                band=SupportBand.NONE,
                released=(),
                withheld=(),
                reason_codes=("model_reported_unanswerable",),
            )
        )

        response = post(client)

        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "abstained"
        assert body["support_band"] == "none"
        assert body["claims"] == []
        assert body["reason_codes"] == ["model_reported_unanswerable"]

    def test_the_evidence_survives_a_lost_model(
        self, client: TestClient, app: FastAPI, service: FakeAskService
    ) -> None:
        """§26.10: a caller given five cited passages has more than one given a 503."""
        app.dependency_overrides[get_ask_service] = lambda: FakeAskService(
            an_answer(
                decision=Decision.ABSTAINED,
                band=SupportBand.NONE,
                released=(),
                withheld=(),
                reason_codes=(),
                degraded=(DEGRADED_GENERATION_UNAVAILABLE,),
            )
        )

        response = post(client)

        assert response.status_code == 200
        body = response.json()
        assert body["degraded"] == [DEGRADED_GENERATION_UNAVAILABLE]
        assert len(body["passages"]) == 2

    def test_nothing_retrieved_is_an_empty_answer_not_an_error(
        self, client: TestClient, app: FastAPI, service: FakeAskService
    ) -> None:
        empty = EvidenceSet(
            passages=(),
            budget_chars=22_000,
            used_chars=0,
            considered=0,
            dropped_for_budget=0,
            expanded=0,
            merged=0,
        )
        app.dependency_overrides[get_ask_service] = lambda: FakeAskService(
            an_answer(
                decision=Decision.ABSTAINED,
                band=SupportBand.NONE,
                released=(),
                withheld=(),
                reason_codes=("no_evidence_retrieved",),
                evidence=empty,
            )
        )

        response = post(client)

        assert response.status_code == 200
        assert response.json()["passages"] == []


class TestConfigurationFailure:
    def test_a_shape_disagreement_is_a_service_error(
        self, client: TestClient, app: FastAPI, service: FakeAskService
    ) -> None:
        """Not survivable and not the caller's fault, so not a 4xx and not degraded."""
        from finsight.vector_index.port import VectorIndexShapeError

        app.dependency_overrides[get_ask_service] = lambda: FakeAskService(
            error=VectorIndexShapeError("the collection expects 1024 dimensions")
        )

        response = post(client)

        assert response.status_code == 503
        assert "inconsistent" in response.json()["detail"]

    def test_the_error_names_no_document_content(
        self, client: TestClient, app: FastAPI, service: FakeAskService
    ) -> None:
        from finsight.vector_index.port import VectorIndexShapeError

        app.dependency_overrides[get_ask_service] = lambda: FakeAskService(
            error=VectorIndexShapeError("the collection expects 1024 dimensions")
        )

        detail = post(client).json()["detail"]

        for forbidden in ("1,62,990", "Probe Limited", "credit risk"):
            assert forbidden not in detail
