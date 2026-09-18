"""Format allow-listing.

An allow-list, never a deny-list: anything not explicitly supported is refused,
so a format arriving before its parser exists cannot reach the system
(PROJECT_BLUEPRINT.md §11.3).
"""

from collections.abc import Iterable

from finsight.domain.errors import DocumentRejectedError, RejectionReason


def ensure_content_type_allowed(detected: str, allowed: Iterable[str]) -> None:
    """Require the detected type to be one FinSight supports.

    Raises:
        DocumentRejectedError: the format is not on the allow-list.
    """
    permitted = {entry.strip().lower() for entry in allowed}
    if detected.lower() not in permitted:
        raise DocumentRejectedError(
            RejectionReason.UNSUPPORTED_CONTENT_TYPE,
            f"detected {detected.lower()}",
        )
