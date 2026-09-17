"""Contract tests for the health surfaces.

These run with no database configuration and no running container: the autouse
isolation fixture strips every ``FINSIGHT_*`` variable, so any code path that
reached for settings would fail loudly here. The database probe is replaced
through ``dependency_overrides``, which is what keeps these tests independent of
infrastructure.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finsight.api.app import create_app
from finsight.api.routes.health import get_database_probe, get_schema_probe
from finsight.persistence.database import dispose_engine, get_engine


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _override_probes(app: FastAPI, *, reachable: bool = True, schema_current: bool = True) -> None:
    app.dependency_overrides[get_database_probe] = lambda: (lambda: reachable)
    app.dependency_overrides[get_schema_probe] = lambda: (lambda: schema_current)


class TestLiveness:
    def test_reports_alive_without_any_database_configuration(
        self,
        client: TestClient,
    ) -> None:
        response = client.get("/health/live")

        assert response.status_code == 200
        assert response.json() == {"status": "alive"}


class TestReadiness:
    def test_reports_ready_when_every_essential_dependency_is_healthy(
        self,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        _override_probes(app, reachable=True, schema_current=True)

        response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {"ready": True}

    def test_reports_service_unavailable_when_the_database_does_not_answer(
        self,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        _override_probes(app, reachable=False)

        response = client.get("/health/ready")

        assert response.status_code == 503
        assert response.json() == {"ready": False}

    def test_reports_service_unavailable_when_the_schema_is_behind_head(
        self,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """A reachable database on an old schema is not ready (§31.2)."""
        _override_probes(app, reachable=True, schema_current=False)

        response = client.get("/health/ready")

        assert response.status_code == 503
        assert response.json() == {"ready": False}

    def test_schema_is_not_probed_when_the_database_is_unreachable(
        self,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """Avoid paying the connect timeout twice for an answer already decided."""
        schema_probe_calls = 0

        def counting_probe() -> bool:
            nonlocal schema_probe_calls
            schema_probe_calls += 1
            return True

        app.dependency_overrides[get_database_probe] = lambda: (lambda: False)
        app.dependency_overrides[get_schema_probe] = lambda: counting_probe

        assert client.get("/health/ready").status_code == 503
        assert schema_probe_calls == 0

    def test_response_discloses_nothing_about_the_dependency(
        self,
        app: FastAPI,
        client: TestClient,
    ) -> None:
        """Readiness is unauthenticated, so it must not name components or hosts."""
        _override_probes(app, reachable=False)

        body = client.get("/health/ready").text.lower()

        assert "postgres" not in body
        assert "localhost" not in body
        assert "schema" not in body


class TestApplicationConstruction:
    def test_creating_the_app_creates_no_database_engine(self) -> None:
        """Construction must not read settings or open a connection."""
        dispose_engine()

        create_app()

        assert get_engine.cache_info().currsize == 0

    def test_serving_liveness_creates_no_database_engine(self) -> None:
        """Liveness must stay answerable while the database is unreachable."""
        dispose_engine()

        with TestClient(create_app()) as client:
            assert client.get("/health/live").status_code == 200

        assert get_engine.cache_info().currsize == 0
