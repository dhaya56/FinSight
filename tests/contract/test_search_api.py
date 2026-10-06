"""Contract tests for the retrieval route.

The pipeline is substituted through ``dependency_overrides``, so these run with no
database, no index, no embedding model and no reranker. What they assert is the
contract: that the route is guarded, that filters reach the pipeline unaltered, that
every score and citation survives serialization, and that degradation is reported
rather than hidden.

The fake records what it was asked, because the properties that matter most are
invisible in the response body — a filter silently dropped and a filter correctly
applied produce output of the same shape.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from finsight.api.app import create_app
from finsight.api.routes.search import get_pipeline
from finsight.config.settings import Settings, get_settings
from finsight.persistence.repositories.chunks import Citation
from finsight.retrieval.contracts import DEGRADED_DENSE_UNAVAILABLE, RetrievalFilters
from finsight.retrieval.pipeline import (
    DEGRADED_RERANKER_UNAVAILABLE,
    Result,
    RetrievedChunk,
)

TOKEN = "a-token-long-enough-to-pass-the-length-floor"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

ELEMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def a_chunk(rank: int = 1, *, rerank_score: float | None = -2.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        rank=rank,
        text="The Company monitors credit risk through counterparty limits.",
        heading_path=("7. Risk factors", "7.2 Credit risk"),
        page_numbers=(41, 42),
        evidence_type="narrative",
        issuer_name="Probe Limited",
        fiscal_period="FY2024-25",
        fused_score=0.0328,
        contributions={"bm25": 1, "dense": 3},
        rerank_score=rerank_score,
        citations=(Citation(source_element_id=ELEMENT_ID, locator="p. 41", position=0),),
    )


def a_result() -> Result:
    return Result(
        candidates=(a_chunk(1), a_chunk(2)),
        depth=25,
        reranked=True,
        reranker_model="cross-encoder/ms-marco-MiniLM-L-6-v2",
        lexical_retriever="bm25",
        dense_used=True,
        fusion_version="1",
    )


@dataclass
class FakePipeline:
    """Records every call, and returns whatever it was constructed with.

    **A dataclass with a ``reranker`` field, because the route is typed on the
    concrete** :class:`RetrievalPipeline` **and turns reranking off with**
    ``dataclasses.replace``. A duck-typed stand-in passes a happy-path test and then
    fails only on the ``rerank: false`` path, which is how this was found. ``calls``
    is shared by reference with any replaced copy, so a call made through the copy is
    still recorded here — which is what lets the test see *which* configuration ran.
    """

    result: Result = field(default_factory=a_result)
    reranker: object | None = "a-reranker"
    calls: list[tuple[str, RetrievalFilters | None, int, object | None]] = field(
        default_factory=list
    )

    def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 5,
    ) -> Result:
        self.calls.append((query, filters, limit, self.reranker))
        return self.result


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def pipeline(app: FastAPI) -> FakePipeline:
    fake = FakePipeline()
    app.dependency_overrides[get_pipeline] = lambda: fake
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


class TestAuthentication:
    def test_the_route_is_guarded(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        response = client.post("/v1/search", json={"query": "credit risk"})

        assert response.status_code == 401
        assert pipeline.calls == []

    def test_retrieval_does_not_run_for_an_unauthenticated_request(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        """Authentication must precede the work, not merely hide its result."""
        client.post(
            "/v1/search",
            json={"query": "credit risk"},
            headers={"Authorization": "Bearer wrong-but-equally-long-token-value!!"},
        )

        assert pipeline.calls == []

    def test_health_remains_unauthenticated(self, client: TestClient) -> None:
        """§28.2 exempts health, and an orchestrator holds no token."""
        assert client.get("/health/live").status_code == 200


class TestRequest:
    def test_a_question_returns_ranked_passages(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        response = client.post(
            "/v1/search", json={"query": "credit risk"}, headers=AUTH
        )

        assert response.status_code == 200
        body = response.json()
        assert [c["rank"] for c in body["candidates"]] == [1, 2]
        assert body["query"] == "credit risk"

    def test_filters_reach_the_pipeline_unaltered(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        client.post(
            "/v1/search",
            json={
                "query": "credit risk",
                "limit": 3,
                "issuer_name": "Probe Limited",
                "fiscal_year": 2025,
                "evidence_type": "narrative",
                "section": "7. Risk factors",
            },
            headers=AUTH,
        )

        _query, filters, limit, _reranker = pipeline.calls[0]
        assert filters == RetrievalFilters(
            issuer_name="Probe Limited",
            fiscal_year=2025,
            evidence_type="narrative",
            section="7. Risk factors",
        )
        assert limit == 3

    def test_an_unknown_filter_is_refused_rather_than_ignored(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        """A silently dropped filter widens the search and looks like success."""
        response = client.post(
            "/v1/search",
            json={"query": "credit risk", "issuer": "Probe Limited"},
            headers=AUTH,
        )

        assert response.status_code == 422
        assert pipeline.calls == []

    def test_an_empty_query_is_refused(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        response = client.post("/v1/search", json={"query": "   "}, headers=AUTH)

        # Whitespace passes min_length but retrieves nothing meaningful; the
        # pipeline is still the thing that decides, so this asserts only that a
        # genuinely empty string is refused.
        assert response.status_code in (200, 422)
        empty = client.post("/v1/search", json={"query": ""}, headers=AUTH)
        assert empty.status_code == 422

    def test_a_limit_beyond_the_bound_is_refused(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        response = client.post(
            "/v1/search", json={"query": "x", "limit": 500}, headers=AUTH
        )

        assert response.status_code == 422

    def test_rerank_false_runs_without_the_reranker(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        client.post("/v1/search", json={"query": "x", "rerank": False}, headers=AUTH)

        assert pipeline.calls[0][3] is None

    def test_rerank_true_keeps_the_reranker(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        client.post("/v1/search", json={"query": "x"}, headers=AUTH)

        assert pipeline.calls[0][3] is not None

    def test_disabling_reranking_does_not_mutate_the_shared_pipeline(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        """The pipeline is cached per process; one request must not reconfigure it."""
        client.post("/v1/search", json={"query": "x", "rerank": False}, headers=AUTH)
        client.post("/v1/search", json={"query": "y"}, headers=AUTH)

        assert pipeline.calls[0][3] is None
        assert pipeline.calls[1][3] is not None
        assert pipeline.reranker is not None


class TestResponse:
    def test_every_score_survives_serialization(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        """§20.13: the final order alone cannot explain itself."""
        body = client.post("/v1/search", json={"query": "x"}, headers=AUTH).json()

        first = body["candidates"][0]
        assert first["fused_score"] == pytest.approx(0.0328)
        assert first["rerank_score"] == pytest.approx(-2.5)
        assert first["contributions"] == {"bm25": 1, "dense": 3}

    def test_citations_are_carried_with_the_passage(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        """A result that cannot name its source elements is not evidence (§14.1)."""
        body = client.post("/v1/search", json={"query": "x"}, headers=AUTH).json()

        citations = body["candidates"][0]["citations"]
        assert citations == [
            {"source_element_id": str(ELEMENT_ID), "locator": "p. 41", "position": 0}
        ]

    def test_provenance_fields_are_present(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        body = client.post("/v1/search", json={"query": "x"}, headers=AUTH).json()

        first = body["candidates"][0]
        assert first["heading_path"] == ["7. Risk factors", "7.2 Credit risk"]
        assert first["page_numbers"] == [41, 42]
        assert first["issuer_name"] == "Probe Limited"
        assert first["fiscal_period"] == "FY2024-25"

    def test_the_run_reports_how_it_was_produced(
        self, client: TestClient, pipeline: FakePipeline
    ) -> None:
        body = client.post("/v1/search", json={"query": "x"}, headers=AUTH).json()

        assert body["depth"] == 25
        assert body["reranked"] is True
        assert body["lexical_retriever"] == "bm25"
        assert body["dense_used"] is True
        assert body["fusion_version"] == "1"
        assert body["elapsed_ms"] >= 0

    def test_nothing_retrieved_is_an_empty_result_not_an_error(
        self, client: TestClient, app: FastAPI, pipeline: FakePipeline
    ) -> None:
        """A question the corpus cannot answer is a real outcome; 404 would lie."""
        app.dependency_overrides[get_pipeline] = lambda: FakePipeline(
            Result(candidates=(), depth=25, lexical_retriever="bm25")
        )

        response = client.post(
            "/v1/search", json={"query": "photosynthesis"}, headers=AUTH
        )

        assert response.status_code == 200
        assert response.json()["candidates"] == []


class TestDegradation:
    def test_degradation_flags_are_reported(
        self, client: TestClient, app: FastAPI, pipeline: FakePipeline
    ) -> None:
        """§20.12 and §23.8: flagged, never silent."""
        app.dependency_overrides[get_pipeline] = lambda: FakePipeline(
            Result(
                candidates=(a_chunk(1, rerank_score=None),),
                degraded=(DEGRADED_DENSE_UNAVAILABLE, DEGRADED_RERANKER_UNAVAILABLE),
                depth=25,
                lexical_retriever="bm25",
                dense_used=False,
            )
        )

        body = client.post("/v1/search", json={"query": "x"}, headers=AUTH).json()

        assert set(body["degraded"]) == {
            DEGRADED_DENSE_UNAVAILABLE,
            DEGRADED_RERANKER_UNAVAILABLE,
        }
        assert body["dense_used"] is False
        assert body["candidates"][0]["rerank_score"] is None
        assert body["reranked"] is False
