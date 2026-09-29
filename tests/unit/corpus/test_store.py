"""Tests for corpus storage and the held-out guard.

The class that matters here is :class:`TestHeldOutGuard`. Everything else checks
that a file is where it should be and hashes to what it should; that one checks
that frozen evidence cannot be read by a command that forgot to think about it.
"""

import datetime
import hashlib
from pathlib import Path

import pytest

from finsight.config.settings import DEFAULT_ALLOWED_CONTENT_TYPES
from finsight.corpus.manifest import (
    CorpusEntry,
    DocumentType,
    Redistribution,
    SourceRepository,
    Split,
)
from finsight.corpus.store import (
    MEDIA_TYPES,
    ChecksumMismatchError,
    CorpusStore,
    DocumentMissingError,
    HeldOutAccessError,
    digest_of,
    media_type_for,
)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

CONTENT = b"%PDF-1.7\nnot a real filing, but real bytes\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


def entry(**overrides: object) -> CorpusEntry:
    fields: dict[str, object] = {
        "document_id": "in-ar-probe-fy2025",
        "split": Split.DEVELOPMENT,
        "filename": "probe.pdf",
        "issuer_name": "Probe Limited",
        "issuer_identifier": "INE000A01000",
        "document_type": DocumentType.ANNUAL_REPORT,
        "jurisdiction": "IN",
        "fiscal_period": "FY2024-25",
        "period_end": datetime.date(2025, 3, 31),
        "reporting_basis": "both",
        "currency": "INR",
        "units_as_presented": "INR crore",
        "format": "application/pdf",
        "byte_size": len(CONTENT),
        "sha256": DIGEST,
        "source_repository": SourceRepository.ISSUER_IR,
        "source_url": "https://example.invalid/probe.pdf",
        "published_at": datetime.date(2025, 5, 1),
        "retrieved_at": datetime.date(2026, 9, 21),
        "redistribution": Redistribution.NOT_REDISTRIBUTABLE,
        "selection_rationale": "a probe",
        "expected_challenges": "none",
    }
    fields.update(overrides)
    return CorpusEntry(**fields)  # type: ignore[arg-type]


def held_out(**overrides: object) -> CorpusEntry:
    return entry(
        split=Split.HELD_OUT_PDF_CORE,
        frozen_at=datetime.date(2026, 9, 21),
        **overrides,
    )


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusStore:
    """A corpus rooted in a temporary directory, with every split present."""
    for split in Split:
        (tmp_path / split.value).mkdir(parents=True)
    return CorpusStore(root=tmp_path)


def place(store: CorpusStore, item: CorpusEntry, content: bytes = CONTENT) -> Path:
    path = store.path_for(item)
    path.write_bytes(content)
    return path


class TestPathResolution:
    def test_a_document_sits_under_its_split(self, corpus: CorpusStore) -> None:
        path = corpus.path_for(entry())

        assert path.parent.name == "development"
        assert path.name == "probe.pdf"

    def test_the_split_decides_the_directory(self, corpus: CorpusStore) -> None:
        assert corpus.path_for(held_out()).parent.name == "held_out_pdf_core"


class TestVerification:
    def test_an_intact_document_verifies(self, corpus: CorpusStore) -> None:
        place(corpus, entry())

        result = corpus.verify(entry())

        assert result.is_intact is True
        assert result.actual_byte_size == len(CONTENT)

    def test_a_missing_document_is_reported_rather_than_raised(
        self, corpus: CorpusStore
    ) -> None:
        """A fresh clone has every entry and no bytes; that is expected, not broken."""
        result = corpus.verify(entry())

        assert result.present is False
        assert result.is_intact is False
        assert result.actual_byte_size is None

    def test_changed_bytes_fail_verification(self, corpus: CorpusStore) -> None:
        place(corpus, entry(), content=CONTENT + b"appended")

        result = corpus.verify(entry())

        assert result.present is True
        assert result.digest_matches is False

    def test_require_intact_returns_the_path(self, corpus: CorpusStore) -> None:
        expected = place(corpus, entry())

        assert corpus.require_intact(entry()) == expected

    def test_require_intact_names_a_missing_document(self, corpus: CorpusStore) -> None:
        with pytest.raises(DocumentMissingError, match="not present locally"):
            corpus.require_intact(entry())

    def test_require_intact_names_a_changed_document(self, corpus: CorpusStore) -> None:
        """§32.7: the file is a different document, not a correction to this one."""
        place(corpus, entry(), content=b"%PDF-1.7\nsomething else\n")

        with pytest.raises(ChecksumMismatchError, match="new candidate version"):
            corpus.require_intact(entry())


class TestHeldOutGuard:
    def test_verification_is_permitted_on_held_out_documents(
        self, corpus: CorpusStore
    ) -> None:
        """Hashing discloses nothing, and unverifiable frozen evidence is useless."""
        place(corpus, held_out())

        assert corpus.verify(held_out()).is_intact is True

    def test_content_access_is_refused_for_held_out_documents(
        self, corpus: CorpusStore
    ) -> None:
        place(corpus, held_out())

        with pytest.raises(HeldOutAccessError), corpus.open_content(held_out()):
            pass

    def test_the_refusal_explains_that_verification_is_still_allowed(
        self, corpus: CorpusStore
    ) -> None:
        """Otherwise the obvious workaround is to weaken the guard."""
        with pytest.raises(HeldOutAccessError, match="verification is"):
            corpus.ensure_readable(held_out())

    @pytest.mark.parametrize(
        "split", [Split.HELD_OUT_PDF_CORE, Split.HELD_OUT_FORMAT_SUPPLEMENT]
    )
    def test_every_frozen_split_is_guarded(
        self, corpus: CorpusStore, split: Split
    ) -> None:
        item = entry(split=split, frozen_at=datetime.date(2026, 9, 21))

        with pytest.raises(HeldOutAccessError):
            corpus.ensure_readable(item)

    def test_development_content_is_readable(self, corpus: CorpusStore) -> None:
        place(corpus, entry())

        with corpus.open_content(entry()) as handle:
            assert handle.read() == CONTENT

    def test_the_guard_precedes_the_integrity_check(self, corpus: CorpusStore) -> None:
        """A held-out entry is refused even when its bytes are absent.

        Order matters: if integrity were checked first, a missing held-out file
        would report as missing and invite someone to go and fetch it.
        """
        with pytest.raises(HeldOutAccessError), corpus.open_content(held_out()):
            pass


class TestMediaTypeFor:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("report.pdf", "application/pdf"),
            ("filing.htm", "text/html"),
            ("filing.html", "text/html"),
            ("book.xlsx", XLSX_MEDIA_TYPE),
            ("facts.xml", "application/xml"),
        ],
    )
    def test_recognises_the_formats_intake_admits(
        self, tmp_path: Path, name: str, expected: str
    ) -> None:
        assert media_type_for(tmp_path / name) == expected

    @pytest.mark.parametrize("name", ["REPORT.PDF", "Filing.HtM"])
    def test_the_extension_is_matched_case_insensitively(
        self, tmp_path: Path, name: str
    ) -> None:
        assert media_type_for(tmp_path / name) is not None

    @pytest.mark.parametrize("name", ["scan.png", "notes.docx", "archive", "data.csv"])
    def test_an_unrecognised_extension_admits_it_rather_than_guessing(
        self, tmp_path: Path, name: str
    ) -> None:
        """Defaulting to PDF is how a wrong media type reaches the manifest."""
        assert media_type_for(tmp_path / name) is None

    def test_every_mapped_type_is_one_intake_allows(self) -> None:
        """A suggestion intake would refuse is worse than no suggestion."""
        assert set(MEDIA_TYPES.values()) <= set(DEFAULT_ALLOWED_CONTENT_TYPES)


class TestDigestOf:
    def test_reports_the_digest_and_size(self, tmp_path: Path) -> None:
        path = tmp_path / "acquired.pdf"
        path.write_bytes(CONTENT)

        assert digest_of(path) == (DIGEST, len(CONTENT))

    def test_the_digest_matches_a_manifest_entry(self, tmp_path: Path) -> None:
        """What the checksum command prints must satisfy manifest validation."""
        path = tmp_path / "acquired.pdf"
        path.write_bytes(CONTENT)
        digest, byte_size = digest_of(path)

        recorded = entry(sha256=digest, byte_size=byte_size)

        assert recorded.sha256 == DIGEST
