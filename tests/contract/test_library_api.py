"""Contract tests for the corpus route.

The repository is substituted, so these run with no database. What they assert is the
contract the Library page depends on — above all the §11.12 distinction: a document version
records what *exists*, a generation records what is *queryable*, and a null generation state
is a definite fact rather than missing data.
"""

import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from finsight.api.app import create_app
from finsight.api.dependencies import get_session_scope
from finsight.config.settings import Settings, get_settings
from finsight.persistence.repositories.library import LibraryEntry

TOKEN = "a-token-long-enough-to-pass-the-length-floor"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

ACTIVE = LibraryEntry(
    document_version_id=UUID("9f1d8c4e-0000-4000-8000-00000000000a"),
    issuer_name="Alpha Industries Limited",
    document_type="annual_report",
    fiscal_period="FY2024-25",
    reporting_basis="consolidated",
    generation_state="active",
    chunking_config_version="4",
    activated_at=datetime.datetime(2026, 10, 1, 10, tzinfo=datetime.UTC),
    ingested_at=datetime.datetime(2026, 9, 30, 9, tzinfo=datetime.UTC),
    pages=369,
    blocks=12_000,
    tables=400,
    footnotes=20,
    chunks=1_800,
    byte_size=9_400_000,
    extraction_state="partial",
    extraction_config_version="2",
    extraction_seconds=3.3,
    unreadable_regions=10,
    tables_accepted=45,
    tables_rejected=42,
    child_chunks=1_530,
    parent_chunks=270,
    median_child_tokens=199,
    sections=(("7. Risk factors", 259),),
)

UNBUILT = LibraryEntry(
    document_version_id=UUID("9f1d8c4e-0000-4000-8000-00000000000b"),
    issuer_name="Beta Financial Services Limited",
    document_type="drhp",
    fiscal_period="FY2024-25",
    reporting_basis="standalone",
    generation_state=None,
    chunking_config_version=None,
    activated_at=None,
    ingested_at=datetime.datetime(2026, 9, 29, 9, tzinfo=datetime.UTC),
    pages=444,
    blocks=15_000,
    tables=300,
    footnotes=11,
    chunks=0,
    byte_size=12_100_000,
    extraction_state="succeeded",
    extraction_config_version="2",
    extraction_seconds=11.5,
    unreadable_regions=0,
    tables_accepted=269,
    tables_rejected=20,
    child_chunks=0,
    parent_chunks=0,
    median_child_tokens=0,
)

ENTRIES: list[LibraryEntry] = [ACTIVE, UNBUILT]


class _Repository:
    def __init__(self, _session: object) -> None:
        pass

    def entries(self, *, limit: int = 500) -> list[LibraryEntry]:
        return ENTRIES[:limit]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr("finsight.api.routes.library.LibraryRepository", _Repository)
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
        """The list of what an organisation holds is not public."""
        assert client.get("/v1/library").status_code == 401


class TestResponse:
    def test_every_document_is_listed(self, client: TestClient) -> None:
        body = client.get("/v1/library", headers=AUTH).json()

        assert [d["issuer_name"] for d in body["documents"]] == [
            "Alpha Industries Limited",
            "Beta Financial Services Limited",
        ]

    def test_what_exists_is_distinguished_from_what_is_queryable(
        self, client: TestClient
    ) -> None:
        """§11.12. Both documents are ingested; only one can be retrieved."""
        body = client.get("/v1/library", headers=AUTH).json()
        active, unbuilt = body["documents"]

        assert active["generation_state"] == "active"
        assert active["chunks"] == 1_800
        assert unbuilt["generation_state"] is None, "null is the state, not missing data"
        assert unbuilt["chunks"] == 0
        assert unbuilt["pages"] == 444, "extraction still happened"

    def test_extraction_counts_are_carried(self, client: TestClient) -> None:
        body = client.get("/v1/library", headers=AUTH).json()
        first = body["documents"][0]

        assert (first["pages"], first["blocks"], first["tables"]) == (369, 12_000, 400)
        assert first["chunking_config_version"] == "4"

    def test_no_storage_location_is_exposed(self, client: TestClient) -> None:
        """§10 keeps object keys, filenames and hashes out of anything a reader sees."""
        body = client.get("/v1/library", headers=AUTH).json()
        first = body["documents"][0]

        for forbidden in ("object_key", "original_filename", "content_hash"):
            assert forbidden not in first

    def test_an_empty_corpus_is_an_empty_list_not_an_error(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Empty:
            def __init__(self, _session: object) -> None:
                pass

            def entries(self, *, limit: int = 500) -> list[LibraryEntry]:
                return []

        monkeypatch.setattr("finsight.api.routes.library.LibraryRepository", _Empty)

        response = client.get("/v1/library", headers=AUTH)

        assert response.status_code == 200
        assert response.json()["documents"] == []
