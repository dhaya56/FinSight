"""Domain errors.

Rejections carry a reason code rather than only a message. PROJECT_BLUEPRINT.md
§11.10 requires validation failures to reach controlled terminal states with
recorded reasons, and a code is what later phases record, count, and surface —
a prose message is not a stable contract.

Reason codes never contain the rejected content, a filename, or a path.
"""

from enum import StrEnum


class DomainError(RuntimeError):
    """Base class for domain rule violations."""


class RejectionReason(StrEnum):
    """Why a document was refused at intake."""

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    UNDETECTABLE_CONTENT_TYPE = "undetectable_content_type"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    DECLARED_TYPE_MISMATCH = "declared_type_mismatch"


class DocumentRejectedError(DomainError):
    """A document failed validation and was not stored.

    Nothing is written when this is raised: no object, no row. The document never
    entered the system, so there is nothing to tombstone or reconcile.
    """

    def __init__(self, reason: RejectionReason, detail: str = "") -> None:
        self.reason = reason
        message = f"document rejected: {reason.value}"
        super().__init__(f"{message}: {detail}" if detail else message)
