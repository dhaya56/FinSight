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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finsight.api.app import create_app
from finsight.persistence.database import (
    dispose_engine,
    get_engine,
    is_database_reachable,
    is_schema_current,
    session_scope,
)
from finsight.persistence.repositories.documents import DocumentRepository
from finsight.persistence.tables.documents import STATE_RECEIVED, DocumentVersion

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
    """Readiness with the real probes, not overrides, against the running container."""
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_schema_is_migrated_to_head() -> None:
    """The applied revision matches the head of the migrations directory."""
    assert is_schema_current() is True


@pytest.fixture
def rolled_back_session() -> Iterator[Session]:
    """A session whose work is discarded, so tests leave no rows behind.

    The repository opens savepoints, so the session joins the outer transaction
    with ``create_savepoint`` rather than starting its own.
    """
    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _record(session: Session, content_hash: str) -> DocumentVersion:
    return DocumentRepository(session).record_version(
        hash_algorithm="sha256",
        content_hash=content_hash,
        byte_size=1024,
        detected_content_type="application/pdf",
        object_key=f"originals/sha256/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}",
        original_filename="probe.pdf",
    )


class TestDocumentRepository:
    def test_records_a_document_and_its_first_version(
        self,
        rolled_back_session: Session,
    ) -> None:
        version = _record(rolled_back_session, f"{uuid4().hex}{uuid4().hex}")

        assert version.id is not None
        assert version.document_id is not None
        assert version.state == STATE_RECEIVED
        assert version.created_at is not None

    def test_identical_bytes_do_not_create_a_second_version(
        self,
        rolled_back_session: Session,
    ) -> None:
        """Re-upload is idempotent by construction (§11.5)."""
        content_hash = f"{uuid4().hex}{uuid4().hex}"

        first = _record(rolled_back_session, content_hash)
        second = _record(rolled_back_session, content_hash)

        assert first.id == second.id
        assert first.document_id == second.document_id

    def test_lookup_by_content_hash(self, rolled_back_session: Session) -> None:
        content_hash = f"{uuid4().hex}{uuid4().hex}"
        recorded = _record(rolled_back_session, content_hash)

        found = DocumentRepository(rolled_back_session).version_by_content_hash(
            hash_algorithm="sha256", content_hash=content_hash
        )

        assert found is not None
        assert found.id == recorded.id

    def test_lookup_of_unknown_bytes_returns_none(
        self,
        rolled_back_session: Session,
    ) -> None:
        found = DocumentRepository(rolled_back_session).version_by_content_hash(
            hash_algorithm="sha256", content_hash=f"{uuid4().hex}{uuid4().hex}"
        )

        assert found is None

    def test_the_database_rejects_a_duplicate_content_address(
        self,
        rolled_back_session: Session,
    ) -> None:
        """Idempotency rests on the constraint, not on a prior read."""
        content_hash = f"{uuid4().hex}{uuid4().hex}"
        recorded = _record(rolled_back_session, content_hash)

        duplicate = DocumentVersion(
            document_id=recorded.document_id,
            hash_algorithm="sha256",
            content_hash=content_hash,
            byte_size=1024,
            detected_content_type="application/pdf",
            object_key="originals/sha256/aa/bb/duplicate",
            state=STATE_RECEIVED,
        )
        rolled_back_session.add(duplicate)

        with pytest.raises(IntegrityError):
            rolled_back_session.flush()
