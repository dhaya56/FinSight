"""Content-type detection and declared-type agreement.

PROJECT_BLUEPRINT.md §11.2 requires routing on detected content type and
structural validation rather than "filename extension alone", and §30.5 requires
the detected signature and the declared type to agree.

**On the filename hint.** Detection passes the sanitized filename to PureMagic,
and that choice was made against measured behaviour rather than assumption:

* a real XLSX with no hint is detected as ``wordprocessingml.document`` (DOCX),
  because both formats are ZIP containers with the same signature;
* with ``book.xlsx`` as the hint it is correctly detected as
  ``spreadsheetml.sheet``;
* with a **lying** hint of ``evil.pdf`` it is still reported as an OOXML type,
  never ``application/pdf``.

So the hint disambiguates *within* the family the bytes already prove, and cannot
promote one format to another. Without it every spreadsheet upload would be
misdetected and refused, breaking a format §5.2 supports. The property that the
hint cannot override the content signature is pinned by a test.

True disambiguation of OOXML requires opening the archive and reading its content
types, which belongs to the spreadsheet parser (§13.2), not the router.
"""

import puremagic

from finsight.domain.errors import DocumentRejectedError, RejectionReason

DETECTION_SAMPLE_BYTES = 8192
"""How much of the head is needed to identify a signature.

Signatures live at the start of a file; reading more would cost memory without
improving detection.
"""


def detect_content_type(sample: bytes, *, filename: str | None = None) -> str:
    """Identify the content type of a byte sample.

    Args:
        sample: the first bytes of the content.
        filename: a sanitized filename used only to disambiguate formats that
            share a signature. It cannot change the detected family.

    Raises:
        DocumentRejectedError: the content matches no known signature.
    """
    try:
        return str(puremagic.from_string(sample, mime=True, filename=filename))
    except (puremagic.PureError, ValueError) as error:
        raise DocumentRejectedError(RejectionReason.UNDETECTABLE_CONTENT_TYPE) from error


def ensure_declared_type_agrees(detected: str, declared: str | None) -> None:
    """Require a declared content type to match what the bytes say (§30.5).

    A declared type is optional — a client need not send one — but a declared type
    that contradicts the signature is a spoofing attempt and is refused. Any
    parameters such as ``; charset=utf-8`` are ignored in the comparison.

    Raises:
        DocumentRejectedError: the declared type contradicts the detected one.
    """
    if declared is None:
        return

    normalized = declared.split(";", maxsplit=1)[0].strip().lower()
    if not normalized:
        return

    if normalized != detected.lower():
        raise DocumentRejectedError(
            RejectionReason.DECLARED_TYPE_MISMATCH,
            f"declared {normalized}, detected {detected.lower()}",
        )
