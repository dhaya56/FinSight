"""Document identity persistence.

Recording a version is idempotent, as PROJECT_BLUEPRINT.md §11.5 requires:
re-uploading identical bytes must not create duplicate rows. The uniqueness
constraint on ``(hash_algorithm, content_hash)`` enforces that in the database
rather than trusting a prior read, so two callers racing on the same bytes still
converge on one row.

There is no delete method. Removal arrives later as tombstoning (§29.12), and a
destructive delete written now would be the wrong thing to retrofit.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finsight.persistence.tables.documents import (
    STATE_RECEIVED,
    Document,
    DocumentVersion,
)


class DocumentRepository:
    """Read and record document identity within a caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def version_by_content_hash(
        self,
        *,
        hash_algorithm: str,
        content_hash: str,
    ) -> DocumentVersion | None:
        """Return the stored version for these bytes, or None."""
        statement = select(DocumentVersion).where(
            DocumentVersion.hash_algorithm == hash_algorithm,
            DocumentVersion.content_hash == content_hash,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def record_version(
        self,
        *,
        hash_algorithm: str,
        content_hash: str,
        byte_size: int,
        detected_content_type: str,
        object_key: str,
        declared_content_type: str | None = None,
        original_filename: str | None = None,
    ) -> DocumentVersion:
        """Record a document and its first version, or return the existing version.

        The insert runs inside a savepoint so that losing a race on the uniqueness
        constraint rolls back only this attempt, leaving the caller's transaction
        usable.
        """
        existing = self.version_by_content_hash(
            hash_algorithm=hash_algorithm, content_hash=content_hash
        )
        if existing is not None:
            return existing

        version = DocumentVersion(
            hash_algorithm=hash_algorithm,
            content_hash=content_hash,
            byte_size=byte_size,
            detected_content_type=detected_content_type,
            declared_content_type=declared_content_type,
            original_filename=original_filename,
            object_key=object_key,
            state=STATE_RECEIVED,
        )
        try:
            with self._session.begin_nested():
                document = Document()
                self._session.add(document)
                self._session.flush()

                version.document_id = document.id
                self._session.add(version)
                self._session.flush()
        except IntegrityError:
            raced = self.version_by_content_hash(
                hash_algorithm=hash_algorithm, content_hash=content_hash
            )
            if raced is None:
                raise
            return raced

        return version
