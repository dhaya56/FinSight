"""Document intake: validate, preserve the original, record identity.

This is the first arrow of the PROJECT_BLUEPRINT.md §10.6 flow. Order matters in
two places, and both are deliberate.

**Validation precedes storage.** A rejected document leaves nothing behind: no
object, no row, nothing to reconcile or tombstone.

**The object is written before the row.** A crash between the two leaves an
orphan object, which is harmless — the key is the content hash, so a retry writes
the identical object — and is exactly what reconciliation (§29.11) exists to
sweep. The reverse order would leave a row pointing at evidence that does not
exist, which is a far worse failure for a system whose claims rest on retrievable
sources.

The object-store upload happens outside any database transaction, keeping
transactions bounded as §29.7 requires.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import BinaryIO, Protocol

from sqlalchemy.orm import Session

from finsight.config.settings import Settings, get_settings
from finsight.domain.documents import ReceivedDocument, RecordedVersion
from finsight.domain.identifiers import ContentAddress
from finsight.ingestion.identity import spool_and_hash
from finsight.ingestion.validation.allow_list import ensure_content_type_allowed
from finsight.ingestion.validation.content_type import (
    DETECTION_SAMPLE_BYTES,
    detect_content_type,
    ensure_declared_type_agrees,
)
from finsight.ingestion.validation.filenames import sanitize_filename
from finsight.object_store.keys import original_object_key
from finsight.object_store.port import ObjectStore
from finsight.object_store.s3_store import build_s3_object_store
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.documents import DocumentRepository


class VersionRecorder(Protocol):
    """Records document identity in the authoritative store."""

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
        """Record a version, or return the existing one for identical bytes."""
        ...


class TransactionalVersionRecorder:
    """Record a version inside its own short transaction.

    Intake opens the transaction here, after the object is stored, so that no
    network call to the object store happens while a transaction is held open.
    """

    def __init__(
        self,
        session_scope_factory: Callable[[], AbstractContextManager[Session]] = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

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
        """Record the version and report whether these bytes were already known."""
        with self._session_scope() as session:
            repository = DocumentRepository(session)
            existing = repository.version_by_content_hash(
                hash_algorithm=address.algorithm, content_hash=address.hex_digest
            )
            version = repository.record_version(
                hash_algorithm=address.algorithm,
                content_hash=address.hex_digest,
                byte_size=byte_size,
                detected_content_type=detected_content_type,
                object_key=object_key,
                declared_content_type=declared_content_type,
                original_filename=original_filename,
            )
            return RecordedVersion(
                document_id=version.document_id,
                version_id=version.id,
                already_existed=existing is not None,
            )


class IntakeService:
    """Accept a document: validate it, preserve the original, record its identity."""

    def __init__(
        self,
        object_store: ObjectStore,
        recorder: VersionRecorder,
        settings: Settings | None = None,
    ) -> None:
        self._object_store = object_store
        self._recorder = recorder
        self._settings = settings

    @property
    def _resolved_settings(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    def receive(
        self,
        source: BinaryIO,
        *,
        declared_content_type: str | None = None,
        filename: str | None = None,
    ) -> ReceivedDocument:
        """Take in one document.

        Raises:
            DocumentRejectedError: validation refused the content; nothing was
                stored and nothing was recorded.
        """
        settings = self._resolved_settings
        safe_filename = sanitize_filename(filename)

        with spool_and_hash(source, max_bytes=settings.upload_max_bytes) as content:
            head = content.handle.read(DETECTION_SAMPLE_BYTES)
            content.handle.seek(0)

            detected = detect_content_type(head, filename=safe_filename)
            ensure_declared_type_agrees(detected, declared_content_type)
            ensure_content_type_allowed(detected, settings.allowed_content_types)

            object_key = original_object_key(content.address.hex_digest)
            self._object_store.put_if_absent(
                object_key,
                content.handle,
                size_bytes=content.byte_size,
                sha256_hex=content.address.hex_digest,
            )

            byte_size = content.byte_size
            address = content.address

        recorded = self._recorder.record(
            address=address,
            byte_size=byte_size,
            detected_content_type=detected,
            object_key=object_key,
            declared_content_type=declared_content_type,
            original_filename=safe_filename,
        )

        return ReceivedDocument(
            document_id=recorded.document_id,
            version_id=recorded.version_id,
            address=address,
            byte_size=byte_size,
            detected_content_type=detected,
            object_key=object_key,
            declared_content_type=declared_content_type,
            original_filename=safe_filename,
            already_existed=recorded.already_existed,
        )


def build_intake_service() -> IntakeService:
    """Wire intake to the configured object store and database.

    The bucket is created here on first use, idempotently, rather than by a
    separate provisioning step, so the same code path works against a local
    container and a cloud bucket.
    """
    store = build_s3_object_store()
    store.ensure_bucket()
    return IntakeService(object_store=store, recorder=TransactionalVersionRecorder())
