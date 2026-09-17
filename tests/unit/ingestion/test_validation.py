"""Tests for upload validation."""

import io
import zipfile

import pytest

from finsight.config.settings import DEFAULT_ALLOWED_CONTENT_TYPES
from finsight.domain.errors import DocumentRejectedError, RejectionReason
from finsight.ingestion.validation.allow_list import ensure_content_type_allowed
from finsight.ingestion.validation.content_type import (
    detect_content_type,
    ensure_declared_type_agrees,
)
from finsight.ingestion.validation.filenames import MAX_FILENAME_LENGTH, sanitize_filename
from finsight.ingestion.validation.structural_limits import (
    ensure_not_empty,
    ensure_within_limit,
)

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"
HTML = b"<!DOCTYPE html><html><head><title>t</title></head><body>x</body></html>"
XML = b'<?xml version="1.0" encoding="UTF-8"?><root><a>1</a></root>'
UNRECOGNISABLE = b"just some plain text with no signature at all"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def build_xlsx() -> bytes:
    """A minimally structured OOXML spreadsheet package."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        archive.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook/>')
    return buffer.getvalue()


class TestDetectContentType:
    @pytest.mark.parametrize(
        ("sample", "expected"),
        [(PDF, "application/pdf"), (HTML, "text/html"), (XML, "application/xml")],
    )
    def test_identifies_formats_from_their_signature(
        self,
        sample: bytes,
        expected: str,
    ) -> None:
        assert detect_content_type(sample) == expected

    def test_a_filename_disambiguates_formats_sharing_a_signature(self) -> None:
        """XLSX and DOCX are both ZIP containers; without the hint XLSX reads as DOCX."""
        assert detect_content_type(build_xlsx(), filename="book.xlsx") == XLSX_MIME

    def test_a_lying_filename_cannot_change_the_detected_family(self) -> None:
        """The hint narrows within what the bytes prove; it never overrides them."""
        detected = detect_content_type(build_xlsx(), filename="evil.pdf")

        assert detected != "application/pdf"
        assert "openxmlformats" in detected

    def test_a_pdf_named_as_a_spreadsheet_is_still_a_pdf(self) -> None:
        assert detect_content_type(PDF, filename="accounts.xlsx") == "application/pdf"

    def test_unrecognisable_content_is_rejected(self) -> None:
        with pytest.raises(DocumentRejectedError) as error:
            detect_content_type(UNRECOGNISABLE)

        assert error.value.reason is RejectionReason.UNDETECTABLE_CONTENT_TYPE


class TestDeclaredTypeAgreement:
    def test_a_matching_declaration_is_accepted(self) -> None:
        ensure_declared_type_agrees("application/pdf", "application/pdf")

    def test_no_declaration_is_accepted(self) -> None:
        ensure_declared_type_agrees("application/pdf", None)
        ensure_declared_type_agrees("application/pdf", "   ")

    def test_parameters_are_ignored(self) -> None:
        ensure_declared_type_agrees("text/html", "text/html; charset=utf-8")

    def test_comparison_is_case_insensitive(self) -> None:
        ensure_declared_type_agrees("application/pdf", "APPLICATION/PDF")

    def test_a_contradicting_declaration_is_rejected(self) -> None:
        """Spoofing a type is refused (§30.5)."""
        with pytest.raises(DocumentRejectedError) as error:
            ensure_declared_type_agrees("application/pdf", XLSX_MIME)

        assert error.value.reason is RejectionReason.DECLARED_TYPE_MISMATCH


class TestAllowList:
    @pytest.mark.parametrize("content_type", DEFAULT_ALLOWED_CONTENT_TYPES)
    def test_supported_formats_pass(self, content_type: str) -> None:
        ensure_content_type_allowed(content_type, DEFAULT_ALLOWED_CONTENT_TYPES)

    def test_an_unsupported_format_is_rejected(self) -> None:
        with pytest.raises(DocumentRejectedError) as error:
            ensure_content_type_allowed(DOCX_MIME, DEFAULT_ALLOWED_CONTENT_TYPES)

        assert error.value.reason is RejectionReason.UNSUPPORTED_CONTENT_TYPE

    def test_an_executable_is_rejected(self) -> None:
        with pytest.raises(DocumentRejectedError):
            ensure_content_type_allowed(
                "application/x-dosexec", DEFAULT_ALLOWED_CONTENT_TYPES
            )


class TestStructuralLimits:
    def test_content_within_the_limit_passes(self) -> None:
        ensure_within_limit(10, max_bytes=10)

    def test_content_over_the_limit_is_rejected(self) -> None:
        with pytest.raises(DocumentRejectedError) as error:
            ensure_within_limit(11, max_bytes=10)

        assert error.value.reason is RejectionReason.TOO_LARGE

    def test_empty_content_is_rejected(self) -> None:
        with pytest.raises(DocumentRejectedError) as error:
            ensure_not_empty(0)

        assert error.value.reason is RejectionReason.EMPTY


class TestFilenameSanitization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("annual-report.pdf", "annual-report.pdf"),
            ("../../etc/passwd", "passwd"),
            (r"C:\Users\someone\report.pdf", "report.pdf"),
            ("/var/tmp/report.pdf", "report.pdf"),
            ("re\x00port\x1b[2J.pdf", "report[2J.pdf"),
            ("  spaced.pdf  ", "spaced.pdf"),
        ],
    )
    def test_reduces_a_name_to_a_safe_label(self, raw: str, expected: str) -> None:
        assert sanitize_filename(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "...", "/", "../.."])
    def test_returns_none_when_nothing_usable_remains(self, raw: str | None) -> None:
        assert sanitize_filename(raw) is None

    def test_caps_the_length(self) -> None:
        sanitized = sanitize_filename("a" * 1000)

        assert sanitized is not None
        assert len(sanitized) == MAX_FILENAME_LENGTH
