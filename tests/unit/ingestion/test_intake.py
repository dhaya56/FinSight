"""Tests for the intake service.

These run with no database and no container: the object store is the filesystem
adapter and the recorder is a fake, so the orchestration is tested on its own.
The same flow is exercised against real infrastructure in
``tests/integration/test_ingestion_pipeline.py``.
"""

import hashlib
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from finsight.config.settings import Settings
from finsight.domain.documents import RecordedVersion
from finsight.domain.errors import DocumentRejectedError, RejectionReason
from finsight.domain.identifiers import ContentAddress
from finsight.ingestion.intake import IntakeService
from finsight.object_store.filesystem_store import FilesystemObjectStore
from finsight.object_store.keys import original_object_key

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"
PDF_DIGEST = hashlib.sha256(PDF).hexdigest()


class FakeRecorder:
    """Stands in for the database, remembering what it has been told."""

    def __init__(self) -> None:
        self.known: dict[str, tuple[UUID, UUID]] = {}
        self.calls: list[dict[str, object]] = []

    def record(
        self,
        *,
        address: ContentAddress,
        byte_size: int,
        detected_content_type: str,
        object_key: str,
        declared_content_type: str | None,
        original_filename: str | None,
    ) -> RecordedVersion:
        self.calls.append(
            {
                "content_hash": address.hex_digest,
                "byte_size": byte_size,
                "detected_content_type": detected_content_type,
                "object_key": object_key,
                "declared_content_type": declared_content_type,
                "original_filename": original_filename,
            }
        )
        already_existed = address.hex_digest in self.known
        if not already_existed:
            self.known[address.hex_digest] = (uuid4(), uuid4())
        document_id, version_id = self.known[address.hex_digest]
        return RecordedVersion(
            document_id=document_id,
            version_id=version_id,
            already_existed=already_existed,
        )


def make_settings(**overrides: object) -> Settings:
    """Settings for a test, with the one required secret supplied."""
    return Settings(
        postgres_password=SecretStr("test-only-value-not-a-real-credential"),
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemObjectStore:
    return FilesystemObjectStore(root=tmp_path / "objects")


@pytest.fixture
def recorder() -> FakeRecorder:
    return FakeRecorder()


@pytest.fixture
def intake(store: FilesystemObjectStore, recorder: FakeRecorder) -> IntakeService:
    return IntakeService(object_store=store, recorder=recorder, settings=make_settings())


class TestAcceptedDocuments:
    def test_stores_the_original_under_its_content_address(
        self,
        intake: IntakeService,
        store: FilesystemObjectStore,
    ) -> None:
        received = intake.receive(BytesIO(PDF), filename="report.pdf")

        assert received.object_key == original_object_key(PDF_DIGEST)
        assert store.exists(received.object_key) is True
        with store.open_stream(received.object_key) as stream:
            assert stream.read() == PDF

    def test_reports_identity_and_context(self, intake: IntakeService) -> None:
        received = intake.receive(
            BytesIO(PDF), declared_content_type="application/pdf", filename="report.pdf"
        )

        assert received.address.hex_digest == PDF_DIGEST
        assert received.byte_size == len(PDF)
        assert received.detected_content_type == "application/pdf"
        assert received.declared_content_type == "application/pdf"
        assert received.original_filename == "report.pdf"
        assert received.already_existed is False

    def test_sanitizes_the_filename_before_recording_it(
        self,
        intake: IntakeService,
        recorder: FakeRecorder,
    ) -> None:
        received = intake.receive(BytesIO(PDF), filename="../../etc/report.pdf")

        assert received.original_filename == "report.pdf"
        assert recorder.calls[0]["original_filename"] == "report.pdf"

    def test_accepts_a_document_with_no_filename(self, intake: IntakeService) -> None:
        assert intake.receive(BytesIO(PDF)).detected_content_type == "application/pdf"


class TestDuplicates:
    def test_identical_bytes_are_recorded_once(
        self,
        intake: IntakeService,
        recorder: FakeRecorder,
    ) -> None:
        first = intake.receive(BytesIO(PDF), filename="report.pdf")
        second = intake.receive(BytesIO(PDF), filename="another-name.pdf")

        assert second.already_existed is True
        assert second.document_id == first.document_id
        assert second.version_id == first.version_id

    def test_identical_bytes_resolve_to_one_object(
        self,
        intake: IntakeService,
        store: FilesystemObjectStore,
    ) -> None:
        first = intake.receive(BytesIO(PDF))
        second = intake.receive(BytesIO(PDF))

        assert first.object_key == second.object_key
        assert store.stat(second.object_key).size_bytes == len(PDF)


class TestRejections:
    def _assert_nothing_stored(
        self,
        store: FilesystemObjectStore,
        recorder: FakeRecorder,
    ) -> None:
        assert store.exists(original_object_key(PDF_DIGEST)) is False
        assert recorder.calls == []

    def test_empty_content_is_rejected(
        self,
        intake: IntakeService,
        store: FilesystemObjectStore,
        recorder: FakeRecorder,
    ) -> None:
        with pytest.raises(DocumentRejectedError) as error:
            intake.receive(BytesIO(b""))

        assert error.value.reason is RejectionReason.EMPTY
        self._assert_nothing_stored(store, recorder)

    def test_oversized_content_is_rejected(
        self,
        store: FilesystemObjectStore,
        recorder: FakeRecorder,
    ) -> None:
        service = IntakeService(
            object_store=store,
            recorder=recorder,
            settings=make_settings(upload_max_bytes=8),
        )

        with pytest.raises(DocumentRejectedError) as error:
            service.receive(BytesIO(PDF))

        assert error.value.reason is RejectionReason.TOO_LARGE
        self._assert_nothing_stored(store, recorder)

    def test_an_unsupported_format_is_rejected(
        self,
        intake: IntakeService,
        store: FilesystemObjectStore,
        recorder: FakeRecorder,
    ) -> None:
        with pytest.raises(DocumentRejectedError) as error:
            intake.receive(BytesIO(b"GIF89a" + b"\x00" * 32), filename="picture.gif")

        assert error.value.reason is RejectionReason.UNSUPPORTED_CONTENT_TYPE
        self._assert_nothing_stored(store, recorder)

    def test_a_spoofed_declared_type_is_rejected(
        self,
        intake: IntakeService,
        store: FilesystemObjectStore,
        recorder: FakeRecorder,
    ) -> None:
        with pytest.raises(DocumentRejectedError) as error:
            intake.receive(BytesIO(PDF), declared_content_type="text/html")

        assert error.value.reason is RejectionReason.DECLARED_TYPE_MISMATCH
        self._assert_nothing_stored(store, recorder)

    def test_unrecognisable_content_is_rejected(
        self,
        intake: IntakeService,
        store: FilesystemObjectStore,
        recorder: FakeRecorder,
    ) -> None:
        with pytest.raises(DocumentRejectedError) as error:
            intake.receive(BytesIO(b"no signature here at all, just prose"))

        assert error.value.reason is RejectionReason.UNDETECTABLE_CONTENT_TYPE
        self._assert_nothing_stored(store, recorder)
