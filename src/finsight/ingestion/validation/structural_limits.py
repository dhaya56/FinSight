"""Size limits, enforced while reading rather than afterwards.

A limit checked after the fact is not a limit: by then the bytes are already in
memory or on disk. The caller streams through :func:`ensure_within_limit` as each
chunk arrives, so an oversized upload is refused as soon as it crosses the bound.

The bound itself is a safe operational constraint rather than a measured value
(PROJECT_BLUEPRINT.md §36.12) and is configurable.
"""

from finsight.domain.errors import DocumentRejectedError, RejectionReason


def ensure_within_limit(byte_count: int, *, max_bytes: int) -> None:
    """Reject as soon as the running total exceeds the limit.

    Raises:
        DocumentRejectedError: the content is larger than permitted.
    """
    if byte_count > max_bytes:
        raise DocumentRejectedError(
            RejectionReason.TOO_LARGE,
            f"exceeds {max_bytes} bytes",
        )


def ensure_not_empty(byte_count: int) -> None:
    """Reject empty content.

    Raises:
        DocumentRejectedError: there were no bytes to store.
    """
    if byte_count == 0:
        raise DocumentRejectedError(RejectionReason.EMPTY)
