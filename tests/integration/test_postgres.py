"""Integration tests for PostgreSQL connectivity and transaction behavior.

These require the container from ``compose.yaml`` to be running and resolve the
real configuration from ``.env``. They are excluded from the default test run and
requested explicitly with ``python -m pytest -m integration``. When the database
is unreachable they fail rather than skip: a silent skip would let a broken
connection look like success.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from finsight.api.app import create_app
from finsight.persistence.database import (
    dispose_engine,
    get_engine,
    is_database_reachable,
    session_scope,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True, scope="module")
def _release_pool() -> Iterator[None]:
    """Return pooled connections once this module is done with them."""
    yield
    dispose_engine()


@pytest.fixture
def scratch_table() -> Iterator[str]:
    """Create a uniquely named table for transaction tests and drop it afterwards.

    A real table rather than a temporary one: a temporary table lives only as long
    as its connection, and pooled connections are reused, which would make the
    commit and rollback assertions depend on pool behavior instead of transaction
    behavior. The name is generated here and never derived from input.
    """
    name = f"finsight_txn_probe_{uuid4().hex}"
    with get_engine().begin() as connection:
        connection.execute(text(f'CREATE TABLE "{name}" (value integer NOT NULL)'))
    try:
        yield name
    finally:
        with get_engine().begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{name}"'))


def _row_count(table: str) -> int:
    with get_engine().connect() as connection:
        result = connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
    return int(result)


def test_database_is_reachable() -> None:
    assert is_database_reachable() is True


def test_server_reports_expected_major_version() -> None:
    with get_engine().connect() as connection:
        version = connection.execute(text("SHOW server_version")).scalar_one()

    assert str(version).startswith("18.")


def test_session_scope_commits_on_clean_exit(scratch_table: str) -> None:
    with session_scope() as session:
        session.execute(text(f'INSERT INTO "{scratch_table}" (value) VALUES (1)'))

    assert _row_count(scratch_table) == 1


def test_session_scope_rolls_back_on_exception(scratch_table: str) -> None:
    class ProbeError(RuntimeError):
        """Raised only by this test."""

    with pytest.raises(ProbeError), session_scope() as session:
        session.execute(text(f'INSERT INTO "{scratch_table}" (value) VALUES (2)'))
        raise ProbeError("rollback probe")

    assert _row_count(scratch_table) == 0


def test_session_scope_reraises_the_original_exception(scratch_table: str) -> None:
    class ProbeError(RuntimeError):
        """Raised only by this test."""

    with pytest.raises(ProbeError, match="original message"), session_scope() as session:
        session.execute(text(f'INSERT INTO "{scratch_table}" (value) VALUES (3)'))
        raise ProbeError("original message")


def test_dispose_engine_is_idempotent() -> None:
    assert is_database_reachable() is True

    dispose_engine()
    dispose_engine()

    assert is_database_reachable() is True


def test_readiness_reports_ready_against_the_live_database() -> None:
    """Readiness with the real probe, not an override, against the running container."""
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"ready": True}
