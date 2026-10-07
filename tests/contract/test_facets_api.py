"""Contract tests for the filter-values route.

The session factory is substituted, so these run with no database. What they assert is the
contract the sidebar depends on: that the route is guarded, that every filter's values are
carried through, and that an empty corpus is an empty list rather than an error.

This route exists because a hard filter a reader has to spell is a trap. §7 forbids relaxing
a filter to find more results, so "Infosys" against a recorded "Infosys Limited" returns
nothing at all — which reads as an empty corpus rather than a typo.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from finsight.api.app import create_app
from finsight.api.dependencies import get_session_scope
from finsight.config.settings import Settings, get_settings

TOKEN = "a-token-long-enough-to-pass-the-length-floor"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

VALUES: dict[str, list[str]] = {
    "issuer_name": ["Alpha Industries Limited", "Beta Financial Services Limited"],
    "document_type": ["annual_report", "drhp"],
    "fiscal_period": ["FY2023-24", "FY2024-25"],
    "reporting_basis": ["consolidated", "standalone"],
}
SECTIONS = ["1. Overview", "7. Risk factors"]


class _Metadata:
    def __init__(self, _session: object) -> None:
        pass

    def filter_values(self) -> dict[str, list[str]]:
        return VALUES


class _Chunks:
    def __init__(self, _session: object) -> None:
        pass

    def top_level_sections(self, *, limit: int = 200) -> list[str]:
        return SECTIONS[:limit]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr("finsight.api.routes.facets.DocumentMetadataRepository", _Metadata)
    monkeypatch.setattr("finsight.api.routes.facets.ChunkRepository", _Chunks)
    return create_app()


@pytest.fixture
def configured(app: FastAPI) -> FastAPI:
    @contextmanager
    def scope() -> Iterator[None]:
        yield None

    app.dependency_overrides[get_session_scope] = lambda: scope
    app.dependency_overrides[get_settings] = lambda: Settings(
        postgres_password=SecretStr("not-a-real-password-just-a-fixture"),
        api_token=SecretStr(TOKEN),
    )
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def get(client: TestClient) -> httpx.Response:
    return client.get("/v1/facets", headers=AUTH)


class TestAuthentication:
    def test_the_route_is_guarded(self, client: TestClient, configured: FastAPI) -> None:
        assert client.get("/v1/facets").status_code == 401

    def test_an_unconfigured_api_refuses_rather_than_opening(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """Fail closed, as every other non-health route does (§28.2).

        Settings are still substituted — with no token rather than none at all — because
        the real ones would be read from the environment and this must test the route's
        behaviour, not whether a developer happens to have a password exported.
        """
        app.dependency_overrides[get_settings] = lambda: Settings(
            postgres_password=SecretStr("not-a-real-password-just-a-fixture"),
            api_token=None,
        )

        assert client.get("/v1/facets", headers=AUTH).status_code == 503


class TestResponse:
    def test_every_filter_carries_its_values(
        self, client: TestClient, configured: FastAPI
    ) -> None:
        body: dict[str, Any] = get(client).json()

        assert body["issuer_names"] == VALUES["issuer_name"]
        assert body["document_types"] == VALUES["document_type"]
        assert body["fiscal_periods"] == VALUES["fiscal_period"]
        assert body["reporting_bases"] == VALUES["reporting_basis"]
        assert body["sections"] == SECTIONS

    def test_an_empty_corpus_is_empty_lists_not_an_error(
        self,
        client: TestClient,
        configured: FastAPI,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Nothing ingested is a real state; a 404 would say the route was missing."""

        class _Empty:
            def __init__(self, _session: object) -> None:
                pass

            def filter_values(self) -> dict[str, list[str]]:
                return {}

            def top_level_sections(self, *, limit: int = 200) -> list[str]:
                return []

        monkeypatch.setattr(
            "finsight.api.routes.facets.DocumentMetadataRepository", _Empty
        )
        monkeypatch.setattr("finsight.api.routes.facets.ChunkRepository", _Empty)

        response = get(client)

        assert response.status_code == 200
        assert response.json() == {
            "issuer_names": [],
            "document_types": [],
            "fiscal_periods": [],
            "reporting_bases": [],
            "sections": [],
        }
