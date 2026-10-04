"""Document identity as the domain sees it.

These types deliberately know nothing about SQLAlchemy or the object store. They
are what intake returns and what later phases pass around, so a change of
persistence or storage backend cannot ripple into domain logic
(CLAUDE.md §11).
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from finsight.domain.identifiers import ContentAddress


class DocumentType(StrEnum):
    """The filing classes §5.1 and §32.2 admit.

    Here rather than in ``corpus/`` because two places must agree on it: the
    manifest validator that governs the corpus, and the CHECK constraint that
    governs what the database will store. Defined once, a manifest value the
    database would reject cannot exist; defined twice, the two drift and corpus
    ingestion fails on a document the manifest called valid.
    """

    ANNUAL_REPORT = "annual_report"
    DRHP = "drhp"
    RHP = "rhp"
    QUARTERLY_RESULT = "quarterly_result"
    FORM_10K = "form_10k"


class ReportingBasis(StrEnum):
    """Whether figures are consolidated, standalone, or both (§16.9).

    §16.9 makes basis a *hard context dimension*, not a conflict to resolve: a
    consolidated and a standalone revenue figure for the same issuer and period
    are both correct and are different numbers. §20.2 therefore filters on it.

    ``BOTH`` is a property of a *document* — an annual report usually carries both
    statements — and is distinct from ``UNDETERMINED``, which says the basis is
    unknown. Collapsing them would turn "this filing contains both" into "we have
    no idea", and §19 requires clarification in exactly the second case.
    """

    CONSOLIDATED = "consolidated"
    STANDALONE = "standalone"
    BOTH = "both"
    UNDETERMINED = "undetermined"


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
