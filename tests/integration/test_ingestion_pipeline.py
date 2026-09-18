"""End-to-end intake against real infrastructure.

Exercises the first arrow of the PROJECT_BLUEPRINT.md §10.6 flow — validate,
preserve the original, record identity — against the running object store and
database, with no fakes anywhere.

Database rows created here are removed afterwards. The stored objects are not:
the port has no delete by design (§29.12 tombstoning arrives later), and objects
are content-addressed, so repeated runs converge rather than accumulate. Sweeping
objects whose rows are gone is what reconciliation is for (§29.11).
"""

import hashlib
from collections.abc import Iterator
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import text

from finsight.domain.errors import DocumentRejectedError, RejectionReason
from finsight.ingestion.intake import IntakeService, build_intake_service
from finsight.object_store.keys import original_object_key
from finsight.object_store.s3_store import build_s3_object_store, dispose_s3_client
from finsight.persistence.database import dispose_engine, get_engine, session_scope
from finsight.persistence.repositories.documents import DocumentRepository

pytestmark = pytest.mark.integration


def unique_pdf() -> bytes:
    """A distinct but validly signed PDF for each test."""
    return b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n" + uuid4().hex.encode() + b"\ntrailer\n%%EOF\n"


@pytest.fixture(scope="module")
def intake() -> Iterator[IntakeService]:
    service = build_intake_service()
    yield service
    dispose_s3_client()
    dispose_engine()


@pytest.fixture
def recorded_hashes() -> Iterator[list[str]]:
    """Remove the identity rows this test created, leaving the store untouched."""
    hashes: list[str] = []
    yield hashes
    if not hashes:
        return
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "DELETE FROM document_versions WHERE content_hash = ANY(:hashes)"
            ).bindparams(hashes=hashes),
        )
        connection.execute(
            text(
                "DELETE FROM documents WHERE id NOT IN "
                "(SELECT document_id FROM document_versions)"
            )
        )


def test_accepts_stores_and_records_a_document(
    intake: IntakeService,
    recorded_hashes: list[str],
) -> None:
    content = unique_pdf()
    digest = hashlib.sha256(content).hexdigest()
    recorded_hashes.append(digest)

    received = intake.receive(
        BytesIO(content), declared_content_type="application/pdf", filename="report.pdf"
    )

    assert received.address.hex_digest == digest
    assert received.byte_size == len(content)
    assert received.detected_content_type == "application/pdf"
    assert received.object_key == original_object_key(digest)
    assert received.already_existed is False


def test_the_stored_object_still_hashes_to_its_key(
    intake: IntakeService,
    recorded_hashes: list[str],
) -> None:
    """Verify on read: content addressing is only worth having if it is checked."""
    content = unique_pdf()
    digest = hashlib.sha256(content).hexdigest()
    recorded_hashes.append(digest)

    received = intake.receive(BytesIO(content))

    store = build_s3_object_store()
    with store.open_stream(received.object_key) as stream:
        retrieved = stream.read()

    assert hashlib.sha256(retrieved).hexdigest() == digest


def test_the_row_records_the_key_alone(
    intake: IntakeService,
    recorded_hashes: list[str],
) -> None:
    """No endpoint, bucket, or URL, so a backend move never rewrites rows."""
    content = unique_pdf()
    digest = hashlib.sha256(content).hexdigest()
    recorded_hashes.append(digest)

    intake.receive(BytesIO(content))

    with session_scope() as session:
        version = DocumentRepository(session).version_by_content_hash(
            hash_algorithm="sha256", content_hash=digest
        )
        assert version is not None
        assert version.object_key == original_object_key(digest)
        assert "http" not in version.object_key
        assert version.byte_size == len(content)
        assert version.detected_content_type == "application/pdf"


def test_re_uploading_identical_bytes_is_idempotent(
    intake: IntakeService,
    recorded_hashes: list[str],
) -> None:
    content = unique_pdf()
    recorded_hashes.append(hashlib.sha256(content).hexdigest())

    first = intake.receive(BytesIO(content), filename="first.pdf")
    second = intake.receive(BytesIO(content), filename="second.pdf")

    assert second.already_existed is True
    assert second.document_id == first.document_id
    assert second.version_id == first.version_id


def test_a_rejected_document_leaves_no_object_and_no_row(
    intake: IntakeService,
) -> None:
    content = unique_pdf()
    digest = hashlib.sha256(content).hexdigest()

    with pytest.raises(DocumentRejectedError) as error:
        intake.receive(BytesIO(content), declared_content_type="text/html")

    assert error.value.reason is RejectionReason.DECLARED_TYPE_MISMATCH

    store = build_s3_object_store()
    assert store.exists(original_object_key(digest)) is False

    with session_scope() as session:
        assert (
            DocumentRepository(session).version_by_content_hash(
                hash_algorithm="sha256", content_hash=digest
            )
            is None
        )
