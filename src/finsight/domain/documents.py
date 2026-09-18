"""Document identity as the domain sees it.

These types deliberately know nothing about SQLAlchemy or the object store. They
are what intake returns and what later phases pass around, so a change of
persistence or storage backend cannot ripple into domain logic
(CLAUDE.md §11).
"""

from dataclasses import dataclass
from uuid import UUID

from finsight.domain.identifiers import ContentAddress


@dataclass(frozen=True, slots=True)
class RecordedVersion:
    """The outcome of recording a version in the authoritative store."""

    document_id: UUID
    version_id: UUID
    already_existed: bool
    """True when these exact bytes were already known.

    Re-uploading identical bytes is idempotent (§11.5): no second document, no
    second version, and no second object.
    """


@dataclass(frozen=True, slots=True)
class ReceivedDocument:
    """A document that passed validation and is now stored and recorded."""

    document_id: UUID
    version_id: UUID
    address: ContentAddress
    byte_size: int
    detected_content_type: str
    object_key: str
    declared_content_type: str | None = None
    original_filename: str | None = None
    already_existed: bool = False
