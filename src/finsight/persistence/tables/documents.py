"""Document identity tables.

The identity model follows PROJECT_BLUEPRINT.md §11.5 and §14.8: the source-byte
hash identifies a *document version*, and provenance resolves through the version
to the original object. A logical ``documents`` row groups versions.

Primary keys are ``uuidv7`` rather than the content hash. The hash is unique, so
it could have served as the key, but it would then propagate as a 64-character
foreign key into chunks, spans, facts and citations — tables that reach millions
of rows — and would weld the hash algorithm into the schema. A time-ordered
surrogate keeps foreign keys at 16 bytes, gives good index locality, and leaves
the algorithm as data.

Re-uploading identical bytes is idempotent by construction: the uniqueness
constraint on ``(hash_algorithm, content_hash)`` is what enforces §11.5.
"""

import datetime
import uuid
from typing import Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from finsight.persistence.tables.base import Base

STATE_RECEIVED: Final = "received"

DOCUMENT_VERSION_STATES: Final[tuple[str, ...]] = (STATE_RECEIVED,)
"""States a version can currently hold.

Only one state is reachable before processing exists. Later phases widen this by
migration, which is why ``state`` is text with a CHECK constraint rather than a
native enum: PostgreSQL enums cannot drop values and are awkward to extend.
"""

_STATE_LIST: Final = ", ".join(f"'{state}'" for state in DOCUMENT_VERSION_STATES)


class Document(Base):
    """A logical document, grouping one or more byte-identical versions.

    Deliberately thin. Issuer, document type and period are discovered during
    extraction, so at intake a document carries only its identity: two uploads of
    different bytes cannot be known to be the same filing until their content is
    read.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentVersion(Base):
    """One exact byte sequence of a document, identified by its content hash."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("hash_algorithm", "content_hash"),
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        CheckConstraint(f"state IN ({_STATE_LIST})", name="state_known"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id"), nullable=False, index=True
    )

    hash_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    detected_content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)

    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    """Metadata only. Never used to build an object key (§30.9)."""

    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    """The object-store key alone.

    Never a bucket, endpoint, or URL: storing a resolved location would turn a
    backend migration into a rewrite of every row.
    """

    state: Mapped[str] = mapped_column(String(32), nullable=False, default=STATE_RECEIVED)
    """Intake state only, and deliberately never widened for processing progress.

    Extraction, chunking and indexing each record their own run and outcome. A
    column that tracked both intake and processing would be two facts in one
    place, and the two would eventually disagree.
    """

    current_extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extraction_runs.id", use_alter=True), nullable=True
    )
    """The extraction run whose elements are the current output of that stage.

    Not named "active": §11.12 reserves activation for a *generation*, which
    spans extraction, chunking and indexing and is what §20.2 filters retrieval
    on. This pointer records what exists; it does not make anything queryable.
    """

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
