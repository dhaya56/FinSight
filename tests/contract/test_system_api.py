"""Contract tests for the operations route.

The probes and the repository are substituted, so these run with no database and no index.
Two properties carry the weight:

* **a probe that raises is a probe that failed** — one unreachable dependency must not take
  down the page that exists to report on it;
* **unknown is not the same as inconsistent** — when Qdrant cannot be asked, consistency is
  null, because reporting an unreachable index as drifted would send an operator to rebuild
  something that is fine.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from finsight.api.app import create_app
from finsight.api.dependencies import get_session_scope
from finsight.config.settings import Settings, get_settings
from finsight.observability.dependency_health import DependencyClass
from finsight.persistence.repositories.operations import AnswerActivity, IndexState

TOKEN = "a-token-long-enough-to-pass-the-length-floor"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

INDEX = IndexState(
    indexed_chunks=4_867,
    context_chunks=770,
    pending_events=0,
    failed_events=0,
    completed_events=4_867,
)

ACTIVITY = AnswerActivity(
    total=17,
    by_decision={"answered": 13, "abstained": 4},
    by_support_band={"strong": 13, "none": 4},
    by_reason={"no_evidence_retrieved": 3, "model_reported_unanswerable": 1},
    median_elapsed_ms=281_421,
    slowest_elapsed_ms=417_372,
)


class _Repository:
    def __init__(self, _session: object) -> None:
        pass

    def index_state(self) -> IndexState:
        return INDEX

    def answer_activity(self) -> AnswerActivity:
        return ACTIVITY


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr("finsight.api.routes.system.OperationsRepository", _Repository)
    monkeypatch.setattr("finsight.api.routes.system._points", lambda: 4_867)
    built = create_app()

    @contextmanager
    def scope() -> Iterator[None]:
        yield None

    built.dependency_overrides[get_session_scope] = lambda: scope
    built.dependency_overrides[get_settings] = lambda: Settings(
        postgres_password=SecretStr("not-a-real-password-just-a-fixture"),
        api_token=SecretStr(TOKEN),
    )
    return built


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


class TestAuthentication:
    def test_the_route_is_guarded(self, client: TestClient) -> None:
        """§28.10: readiness is public, per-dependency detail is not."""
        assert client.get("/v1/system").status_code == 401


class TestIndexConsistency:
    def test_agreement_is_reported_when_the_counts_match(self, client: TestClient) -> None:
        body = client.get("/v1/system", headers=AUTH).json()["index"]

        assert body["indexed_chunks"] == 4_867
        assert body["index_points"] == 4_867
        assert body["index_consistent"] is True

    def test_only_indexed_children_are_counted(self, client: TestClient) -> None:
        """Parents carry no outbox event at all.

        Measured against the live stack: comparing every chunk against the collection
        reported a healthy index as drifted by exactly the parent count, which is the
        worst kind of false alarm on a status page.
        """
        body = client.get("/v1/system", headers=AUTH).json()["index"]

        assert body["context_chunks"] == 770
        assert body["indexed_chunks"] + body["context_chunks"] == 5_637

    def test_an_unreachable_index_is_unknown_not_inconsistent(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reporting it as drifted would send an operator to rebuild something fine."""
        monkeypatch.setattr("finsight.api.routes.system._points", lambda: None)

        body = client.get("/v1/system", headers=AUTH).json()["index"]

        assert body["index_points"] is None
        assert body["index_consistent"] is None

    def test_a_genuine_drift_is_reported(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("finsight.api.routes.system._points", lambda: 4_000)

        assert client.get("/v1/system", headers=AUTH).json()["index"]["index_consistent"] is False


class TestDependencies:
    def test_each_probe_is_classified(self, client: TestClient) -> None:
        body = client.get("/v1/system", headers=AUTH).json()

        classes = {d["name"]: d["classification"] for d in body["dependencies"]}
        assert classes["PostgreSQL"] == "essential"
        assert classes["Qdrant"] == "degradable", "losing it costs dense retrieval only"

    def test_a_probe_that_raises_is_unhealthy_rather_than_a_crash(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One unreachable dependency must not take down the page reporting on it."""

        def explode() -> bool:
            raise RuntimeError("connection refused")

        monkeypatch.setattr(
            "finsight.api.routes.system._PROBES",
            (("Qdrant", DependencyClass.DEGRADABLE, explode),),
        )
        response = client.get("/v1/system", headers=AUTH)

        assert response.status_code == 200
        assert response.json()["dependencies"][0]["healthy"] is False


class TestAnswerActivity:
    def test_decisions_and_reasons_are_carried(self, client: TestClient) -> None:
        body = client.get("/v1/system", headers=AUTH).json()["answers"]

        assert body["total"] == 17
        assert body["by_decision"] == {"answered": 13, "abstained": 4}
        assert body["by_reason"]["no_evidence_retrieved"] == 3
        assert body["median_elapsed_ms"] == 281_421
