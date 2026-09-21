"""Tests for the corpus command group.

The behaviour worth protecting is the asymmetry: ``verify`` reaches held-out
documents and ``ingest`` will not. A test suite that only exercised the
development split would let that distinction rot.
"""

import hashlib
from pathlib import Path

import pytest

from finsight.cli import main as cli
from finsight.corpus.manifest import Split

CONTENT = b"%PDF-1.7\nnot a real filing, but real bytes\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()

ENTRY = """
[[document]]
document_id = "{document_id}"
split = "{split}"
filename = "{filename}"
issuer_name = "{issuer}"
issuer_identifier = "INE000A01000"
document_type = "annual_report"
jurisdiction = "IN"
fiscal_period = "FY2024-25"
period_end = 2025-03-31
reporting_basis = "both"
currency = "INR"
units_as_presented = "INR crore"
format = "application/pdf"
byte_size = {byte_size}
sha256 = "{digest}"
source_repository = "issuer_ir"
source_url = "https://example.invalid/probe.pdf"
published_at = 2025-05-01
retrieved_at = 2026-09-21
redistribution = "not_redistributable"
selection_rationale = "a probe"
expected_challenges = "none"
"""


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary corpus the CLI resolves to, with every split directory present."""
    root = tmp_path / "corpus"
    for split in Split:
        (root / split.value).mkdir(parents=True)
    manifest = root / "manifest.toml"
    manifest.write_text("manifest_version = 1\n", encoding="utf-8")
    monkeypatch.setattr(cli, "MANIFEST_PATH", manifest)
    return root


def record(
    corpus: Path,
    *,
    document_id: str = "in-ar-probe-fy2025",
    split: Split = Split.DEVELOPMENT,
    issuer: str = "Probe Limited",
    filename: str = "probe.pdf",
    place: bytes | None = CONTENT,
    frozen: bool = False,
) -> None:
    """Add a manifest entry, and optionally the bytes it describes."""
    body = ENTRY.format(
        document_id=document_id,
        split=split.value,
        filename=filename,
        issuer=issuer,
        byte_size=len(CONTENT),
        digest=DIGEST,
    )
    if frozen:
        body += "frozen_at = 2026-09-21\n"
    manifest = corpus / "manifest.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + body, encoding="utf-8")
    if place is not None:
        (corpus / split.value / filename).write_bytes(place)


class TestValidate:
    def test_an_empty_manifest_validates(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        assert cli.main(["corpus", "validate"]) == 0
        assert "0 document(s)" in capsys.readouterr().out

    def test_counts_are_reported_per_split(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        record(corpus)

        cli.main(["corpus", "validate"])
        out = capsys.readouterr().out

        assert "1 document(s)" in out
        assert "development" in out

    def test_a_rule_violation_exits_non_zero(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        """§34.12 — the same issuer either side of a split boundary."""
        record(corpus)
        record(
            corpus,
            document_id="in-ar-probe-fy2024",
            split=Split.HELD_OUT_PDF_CORE,
            filename="probe-2024.pdf",
            frozen=True,
        )

        assert cli.main(["corpus", "validate"]) == 1
        assert "issuer-disjoint" in capsys.readouterr().err


class TestVerify:
    def test_an_intact_document_passes(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        record(corpus)

        assert cli.main(["corpus", "verify"]) == 0
        assert "1 intact, 0 failed" in capsys.readouterr().out

    def test_a_missing_document_fails(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        record(corpus, place=None)

        assert cli.main(["corpus", "verify"]) == 1
        assert "MISSING" in capsys.readouterr().out

    def test_changed_bytes_fail(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        record(corpus, place=CONTENT + b"appended")

        assert cli.main(["corpus", "verify"]) == 1
        assert "CHECKSUM MISMATCH" in capsys.readouterr().out

    def test_held_out_documents_are_verified_too(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        """The asymmetry: hashing is permitted where reading is not."""
        record(
            corpus,
            document_id="in-ar-frozen-fy2025",
            split=Split.HELD_OUT_PDF_CORE,
            issuer="Frozen Limited",
            filename="frozen.pdf",
            frozen=True,
        )

        assert cli.main(["corpus", "verify"]) == 0
        assert "held_out_pdf_core" in capsys.readouterr().out

    def test_a_split_can_be_selected(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        record(corpus)

        cli.main(["corpus", "verify", "--split", "held_out_pdf_core"])

        assert "no documents recorded" in capsys.readouterr().out


class TestList:
    def test_reports_nothing_when_empty(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        assert cli.main(["corpus", "list"]) == 0
        assert "no documents recorded" in capsys.readouterr().out

    def test_marks_frozen_entries(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        """An operator must be able to see at a glance what is off limits."""
        record(
            corpus,
            document_id="in-ar-frozen-fy2025",
            split=Split.HELD_OUT_PDF_CORE,
            issuer="Frozen Limited",
            filename="frozen.pdf",
            frozen=True,
        )

        cli.main(["corpus", "list"])

        assert "frozen" in capsys.readouterr().out

    def test_a_development_entry_is_not_marked_frozen(
        self,
        corpus: Path,
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        record(corpus)

        cli.main(["corpus", "list"])

        assert "frozen" not in capsys.readouterr().out


class TestChecksum:
    def test_prints_a_manifest_block(self, corpus: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        path = corpus / "development" / "acquired.pdf"
        path.write_bytes(CONTENT)

        assert cli.main(["corpus", "checksum", str(path)]) == 0
        out = capsys.readouterr().out

        assert "[[document]]" in out
        assert DIGEST in out
        assert 'filename          = "acquired.pdf"' in out
        assert 'split             = "development"' in out

    def test_reminds_the_author_to_record_challenges_first(
        self,
        corpus: Path,
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        """Recorded afterwards it is a rationalisation, not a prediction."""
        path = corpus / "development" / "acquired.pdf"
        path.write_bytes(CONTENT)

        cli.main(["corpus", "checksum", str(path)])

        assert "BEFORE running extraction" in capsys.readouterr().out

    def test_a_file_outside_a_split_directory_is_flagged(
        self,
        corpus: Path,
        tmp_path: Path,
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        path = tmp_path / "loose.pdf"
        path.write_bytes(CONTENT)

        cli.main(["corpus", "checksum", str(path)])
        out = capsys.readouterr().out

        assert "<split>" in out
        assert "move it first" in out

    def test_a_missing_file_exits_non_zero(self, corpus: Path, tmp_path: Path) -> None:
        assert cli.main(["corpus", "checksum", str(tmp_path / "absent.pdf")]) == 1


class TestArgumentParsing:
    def test_the_group_requires_a_subcommand(self, corpus: Path) -> None:
        with pytest.raises(SystemExit):
            cli.main(["corpus"])

    def test_an_unknown_corpus_subcommand_is_rejected(self, corpus: Path) -> None:
        with pytest.raises(SystemExit):
            cli.main(["corpus", "download"])

    def test_an_unknown_split_is_rejected(self, corpus: Path) -> None:
        with pytest.raises(SystemExit):
            cli.main(["corpus", "list", "--split", "holdout"])
