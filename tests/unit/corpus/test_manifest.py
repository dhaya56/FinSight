"""Tests for the corpus manifest.

The rules here are governance, not formatting. A permissive parser that ignored
an unknown key would silently drop a ``frozen_at`` and unfreeze a held-out
document; one that allowed an issuer in two splits would leak the answer into
the held-out measurement. Each test below pins one of those.
"""

import datetime
from pathlib import Path

import pytest

from finsight.corpus.manifest import (
    CORPUS_ROOT,
    MANIFEST_PATH,
    CorpusEntry,
    DocumentType,
    Manifest,
    ManifestError,
    Redistribution,
    SourceRepository,
    Split,
    load_manifest,
)

DIGEST = "ab" * 32

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
"""Resolved from this file, not from the working directory.

``tests/conftest.py`` moves unit tests into a temporary directory so a local
``.env`` cannot leak into settings, which means a repository-relative path does
not resolve during a unit test.
"""


def entry(**overrides: object) -> CorpusEntry:
    """A minimal valid entry, so each test states only what it is about."""
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
        "byte_size": 1024,
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


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "manifest.toml"
    path.write_text(body, encoding="utf-8")
    return path


ONE_DOCUMENT = """
manifest_version = 1

[[document]]
document_id = "in-ar-probe-fy2025"
split = "development"
filename = "probe.pdf"
issuer_name = "Probe Limited"
issuer_identifier = "INE000A01000"
document_type = "annual_report"
jurisdiction = "IN"
fiscal_period = "FY2024-25"
period_end = 2025-03-31
reporting_basis = "both"
currency = "INR"
units_as_presented = "INR crore"
format = "application/pdf"
byte_size = 1024
sha256 = "{digest}"
source_repository = "issuer_ir"
source_url = "https://example.invalid/probe.pdf"
published_at = 2025-05-01
retrieved_at = 2026-09-21
redistribution = "not_redistributable"
selection_rationale = "a probe"
expected_challenges = "none"
"""


class TestCommittedManifest:
    def test_the_repository_manifest_is_valid(self) -> None:
        """The file actually committed must parse, not just the fixtures."""
        assert load_manifest(REPOSITORY_ROOT / MANIFEST_PATH).manifest_version == 1

    def test_a_missing_manifest_names_the_path_it_looked_for(
        self, tmp_path: Path
    ) -> None:
        """Otherwise a wrong working directory reads as a missing file."""
        with pytest.raises(ManifestError, match="repository root"):
            load_manifest(tmp_path / "manifest.toml")

    def test_an_empty_corpus_is_valid(self, tmp_path: Path) -> None:
        """A corpus with nothing acquired yet is a legitimate state."""
        manifest = load_manifest(write(tmp_path, "manifest_version = 1\n"))

        assert manifest.entries == ()


class TestParsing:
    def test_reads_one_document(self, tmp_path: Path) -> None:
        manifest = load_manifest(write(tmp_path, ONE_DOCUMENT.format(digest=DIGEST)))

        assert len(manifest.entries) == 1
        assert manifest.entries[0].issuer_name == "Probe Limited"
        assert manifest.entries[0].split is Split.DEVELOPMENT

    def test_a_missing_manifest_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestError, match="no corpus manifest"):
            load_manifest(tmp_path / "absent.toml")

    def test_malformed_toml_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestError, match="not valid TOML"):
            load_manifest(write(tmp_path, "manifest_version = "))

    def test_a_wrong_version_is_refused(self, tmp_path: Path) -> None:
        """A future layout must fail loudly rather than be half-parsed."""
        with pytest.raises(ManifestError, match="manifest_version"):
            load_manifest(write(tmp_path, "manifest_version = 2\n"))

    def test_unknown_top_level_keys_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestError, match="unknown top-level"):
            load_manifest(write(tmp_path, "manifest_version = 1\nextra = true\n"))

    def test_unknown_entry_keys_are_refused(self, tmp_path: Path) -> None:
        """The rule that stops a mistyped frozen_at unfreezing a document.

        ``frozen_ad`` is the realistic typo: a permissive parser would ignore it,
        and the held-out entry it was meant to freeze would parse as unfrozen.
        """
        body = ONE_DOCUMENT.format(digest=DIGEST) + 'frozen_ad = 2026-09-21\n'

        with pytest.raises(ManifestError, match="unknown manifest keys"):
            load_manifest(write(tmp_path, body))

    def test_missing_entry_keys_are_reported(self, tmp_path: Path) -> None:
        body = ONE_DOCUMENT.format(digest=DIGEST).replace(
            'currency = "INR"\n', ""
        )

        with pytest.raises(ManifestError, match="missing manifest keys"):
            load_manifest(write(tmp_path, body))

    @pytest.mark.parametrize(
        ("field", "bad"),
        [
            ("split", "holdout"),
            ("document_type", "press_release"),
            ("source_repository", "some_mirror"),
            ("redistribution", "probably_fine"),
        ],
    )
    def test_unknown_enum_values_are_refused(
        self, tmp_path: Path, field: str, bad: str
    ) -> None:
        """§32.3 admits official repositories only; a mirror has no value here."""
        body = ONE_DOCUMENT.format(digest=DIGEST)
        original = next(
            line for line in body.splitlines() if line.startswith(f"{field} = ")
        )
        body = body.replace(original, f'{field} = "{bad}"')

        with pytest.raises(ManifestError, match=field):
            load_manifest(write(tmp_path, body))

    def test_a_non_string_controlled_value_is_refused(self, tmp_path: Path) -> None:
        """TOML yields integers and booleans happily; the enum would raise TypeError."""
        body = ONE_DOCUMENT.format(digest=DIGEST).replace(
            'split = "development"', "split = 1"
        )

        with pytest.raises(ManifestError, match="split"):
            load_manifest(write(tmp_path, body))


class TestEntryRules:
    @pytest.mark.parametrize(
        "document_id", ["Has-Capitals", "under_scores", "trailing-", "spaced id", ""]
    )
    def test_document_ids_are_restricted(self, document_id: str) -> None:
        """The id reaches file names and records, so it must never need escaping."""
        with pytest.raises(ManifestError, match="document_id"):
            entry(document_id=document_id)

    @pytest.mark.parametrize(
        "filename", ["../escape.pdf", "nested/probe.pdf", "back\\slash.pdf", "..", "  "]
    )
    def test_filenames_must_be_bare(self, filename: str) -> None:
        """Refused, not sanitised: this becomes a real path under data/corpus/."""
        with pytest.raises(ManifestError, match="filename"):
            entry(filename=filename)

    def test_the_offending_filename_is_not_echoed(self) -> None:
        with pytest.raises(ManifestError) as caught:
            entry(filename="../../etc/passwd")

        assert "passwd" not in str(caught.value)

    @pytest.mark.parametrize("digest", ["", "abc", "A" * 64, "g" * 64, DIGEST + "0"])
    def test_the_checksum_must_be_a_sha256_digest(self, digest: str) -> None:
        with pytest.raises(ManifestError, match="sha256"):
            entry(sha256=digest)

    @pytest.mark.parametrize("size", [0, -1])
    def test_byte_size_must_be_positive(self, size: int) -> None:
        with pytest.raises(ManifestError, match="byte_size"):
            entry(byte_size=size)

    @pytest.mark.parametrize(
        "field", ["issuer_name", "selection_rationale", "expected_challenges", "source_url"]
    )
    def test_descriptive_fields_are_required(self, field: str) -> None:
        """An entry without a rationale records nothing anyone can review."""
        with pytest.raises(ManifestError, match=field):
            entry(**{field: "   "})

    def test_the_relative_path_follows_the_split(self) -> None:
        held_out = entry(
            split=Split.HELD_OUT_PDF_CORE, frozen_at=datetime.date(2026, 9, 21)
        )

        assert held_out.relative_path == CORPUS_ROOT / "held_out_pdf_core" / "probe.pdf"


class TestFreezing:
    @pytest.mark.parametrize(
        "split", [Split.HELD_OUT_PDF_CORE, Split.HELD_OUT_FORMAT_SUPPLEMENT]
    )
    def test_a_held_out_entry_must_record_when_it_was_frozen(self, split: Split) -> None:
        with pytest.raises(ManifestError, match="frozen_at"):
            entry(split=split)

    def test_a_development_entry_may_not_be_frozen(self) -> None:
        """A freeze date on a development entry is a mis-assignment, not a note."""
        with pytest.raises(ManifestError, match="frozen_at"):
            entry(frozen_at=datetime.date(2026, 9, 21))

    @pytest.mark.parametrize(
        ("split", "expected"),
        [
            (Split.DEVELOPMENT, False),
            (Split.HELD_OUT_PDF_CORE, True),
            (Split.HELD_OUT_FORMAT_SUPPLEMENT, True),
        ],
    )
    def test_held_out_splits_are_identified(self, split: Split, expected: bool) -> None:
        assert split.is_held_out is expected


class TestCorpusRules:
    def test_document_ids_are_unique(self, tmp_path: Path) -> None:
        body = ONE_DOCUMENT.format(digest=DIGEST) * 2

        with pytest.raises(ManifestError, match="not unique"):
            load_manifest(write(tmp_path, "manifest_version = 1\n" + body.replace(
                "manifest_version = 1\n", ""
            )))

    def test_an_issuer_may_not_span_two_splits(self, tmp_path: Path) -> None:
        """§34.12: two filings from one issuer restate the same facts."""
        second = (
            ONE_DOCUMENT.format(digest=DIGEST)
            .replace("manifest_version = 1\n", "")
            .replace("in-ar-probe-fy2025", "in-ar-probe-fy2024")
            .replace('split = "development"', 'split = "held_out_pdf_core"')
            .replace('filename = "probe.pdf"', 'filename = "probe-2024.pdf"')
            + "frozen_at = 2026-09-21\n"
        )

        with pytest.raises(ManifestError, match="issuer-disjoint"):
            load_manifest(write(tmp_path, ONE_DOCUMENT.format(digest=DIGEST) + second))

    def test_two_issuers_in_one_split_are_fine(self, tmp_path: Path) -> None:
        second = (
            ONE_DOCUMENT.format(digest=DIGEST)
            .replace("manifest_version = 1\n", "")
            .replace("in-ar-probe-fy2025", "in-ar-other-fy2025")
            .replace("Probe Limited", "Other Limited")
            .replace('filename = "probe.pdf"', 'filename = "other.pdf"')
        )

        manifest = load_manifest(write(tmp_path, ONE_DOCUMENT.format(digest=DIGEST) + second))

        assert len(manifest.entries) == 2

    def test_superseded_by_must_name_a_real_entry(self, tmp_path: Path) -> None:
        """§32.7: a changed download is a new entry pointing at the old one."""
        body = ONE_DOCUMENT.format(digest=DIGEST) + 'superseded_by = "does-not-exist"\n'

        with pytest.raises(ManifestError, match="superseded_by"):
            load_manifest(write(tmp_path, body))


def without(field: str) -> str:
    """The one-document manifest with one key removed."""
    body = ONE_DOCUMENT.format(digest=DIGEST)
    original = next(
        line for line in body.splitlines() if line.startswith(f"{field} = ")
    )
    return body.replace(original + "\n", "")


class TestHonestAbsence:
    """Absence is permitted where an estimate would be worse, and never silent."""

    @pytest.mark.parametrize(
        "field", ["period_end", "published_at", "retrieved_at"]
    )
    def test_every_date_is_mandatory(self, tmp_path: Path, field: str) -> None:
        """Both were briefly optional; exhausting official sources removed the case.

        Exchange submission letters carry publication dates and SEBI-hosted
        filing material states restated periods, so every entry can record both.
        A field left optional for a situation that never arose is the
        speculative flexibility CLAUDE.md §11 warns against — and it would let a
        future entry omit a date that is in fact obtainable.
        """
        with pytest.raises(ManifestError, match=field):
            load_manifest(write(tmp_path, without(field)))

    @pytest.mark.parametrize(
        "field",
        ["issuer_identifier", "fiscal_period", "reporting_basis", "units_as_presented"],
    )
    def test_the_unknown_marker_counts_as_unrecorded(self, field: str) -> None:
        assert field in entry(**{field: "unknown"}).unrecorded_fields

    @pytest.mark.parametrize(
        "field",
        ["issuer_identifier", "fiscal_period", "reporting_basis", "units_as_presented"],
    )
    def test_an_empty_descriptive_field_counts_as_unrecorded(self, field: str) -> None:
        assert field in entry(**{field: "  "}).unrecorded_fields

    def test_a_complete_entry_reports_nothing_unrecorded(self) -> None:
        assert entry().unrecorded_fields == ()

    def test_a_descriptive_key_may_be_empty_but_not_absent(
        self, tmp_path: Path
    ) -> None:
        """Omission stays a deliberate act; a forgotten line is still an error."""
        body = ONE_DOCUMENT.format(digest=DIGEST).replace('currency = "INR"\n', "")

        with pytest.raises(ManifestError, match="missing manifest keys"):
            load_manifest(write(tmp_path, body))

    def test_the_four_load_bearing_fields_stay_mandatory(self) -> None:
        """Always determinable, so relaxing them would weaken the record."""
        for field in (
            "issuer_name",
            "source_url",
            "selection_rationale",
            "expected_challenges",
        ):
            with pytest.raises(ManifestError, match=field):
                entry(**{field: "   "})


class TestLookups:
    def test_entries_can_be_filtered_by_split(self) -> None:
        manifest = Manifest(
            manifest_version=1,
            entries=(
                entry(),
                entry(
                    document_id="in-ar-other-fy2025",
                    issuer_name="Other Limited",
                    split=Split.HELD_OUT_PDF_CORE,
                    frozen_at=datetime.date(2026, 9, 21),
                ),
            ),
        )

        assert len(manifest.for_split(Split.DEVELOPMENT)) == 1
        assert len(manifest.for_split(Split.HELD_OUT_PDF_CORE)) == 1
        assert manifest.for_split(Split.HELD_OUT_FORMAT_SUPPLEMENT) == ()

    def test_an_entry_can_be_found_by_id(self) -> None:
        manifest = Manifest(manifest_version=1, entries=(entry(),))

        assert manifest.by_id("in-ar-probe-fy2025") is not None
        assert manifest.by_id("absent") is None
