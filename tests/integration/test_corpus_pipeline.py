"""End-to-end corpus ingestion against real infrastructure.

Exercised with generated PDFs in a temporary corpus, so this suite runs before
any real filing has been acquired. What it proves is that the manifest, the
guard, intake and extraction compose correctly — not anything about extraction
quality, which no synthetic fixture can establish.

Rows created here are removed afterwards. Stored objects are not, for the reason
given in ``test_ingestion_pipeline.py``.
"""

import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from db_cleanup import statements as cleanup_statements
from finsight.corpus.manifest import CorpusEntry, Split, load_manifest
from finsight.corpus.service import CorpusIngestionService, build_corpus_ingestion_service
from finsight.corpus.store import ChecksumMismatchError, CorpusStore, HeldOutAccessError
from finsight.domain.representations.source import ExtractionState
from finsight.object_store.s3_store import dispose_s3_client
from finsight.persistence.database import dispose_engine, get_engine
from pdf_fixtures import PlacedText, build_malformed_pdf, build_pdf

pytestmark = pytest.mark.integration


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
selection_rationale = "a generated probe, not an acquired filing"
expected_challenges = "none; this is plumbing"
"""


def two_page_pdf() -> bytes:
    """A distinct document, so no two tests collide on a content hash."""
    return build_pdf(
        [
            [
                PlacedText("Revenue from operations", x=72, y_from_bottom=700),
                PlacedText(uuid4().hex, x=72, y_from_bottom=60),
            ],
            [PlacedText("Profit before tax", x=72, y_from_bottom=700)],
        ]
    )


@pytest.fixture
def corpus(tmp_path: Path) -> Iterator[Path]:
    """A temporary corpus, cleaned out of the database afterwards."""
    root = tmp_path / "corpus"
    for split in Split:
        (root / split.value).mkdir(parents=True)
    (root / "manifest.toml").write_text("manifest_version = 1\n", encoding="utf-8")

    yield root

    digests = [
        entry.sha256 for entry in load_manifest(root / "manifest.toml").entries
    ]
    if digests:
        with get_engine().begin() as connection:
            for statement in cleanup_statements():
                query = text(statement)
                if ":hashes" in statement:
                    query = query.bindparams(hashes=digests)
                connection.execute(query)


@pytest.fixture
def add(corpus: Path) -> Callable[..., CorpusEntry]:
    """Place a document and record it, returning the entry."""

    def _add(
        *,
        document_id: str | None = None,
        split: Split = Split.DEVELOPMENT,
        issuer: str | None = None,
        content: bytes | None = None,
        frozen: bool = False,
        corrupt: bool = False,
    ) -> CorpusEntry:
        body = content if content is not None else two_page_pdf()
        slug = document_id or f"in-ar-probe-{uuid4().hex[:8]}"
        filename = f"{slug}.pdf"
        digest = hashlib.sha256(body).hexdigest()

        entry_text = ENTRY.format(
            document_id=slug,
            split=split.value,
            filename=filename,
            issuer=issuer or f"Probe {slug} Limited",
            byte_size=len(body),
            digest=digest,
        )
        if frozen:
            entry_text += "frozen_at = 2026-09-21\n"

        manifest = corpus / "manifest.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + entry_text, encoding="utf-8"
        )
        stored = (body + b"corrupted") if corrupt else body
        (corpus / split.value / filename).write_bytes(stored)

        recorded = load_manifest(manifest).by_id(slug)
        assert recorded is not None
        return recorded

    return _add


@pytest.fixture(autouse=True, scope="module")
def _release_shared_clients() -> Iterator[None]:
    """Release pooled connections once, when this module is done with them.

    Module-scoped deliberately. Disposing per test would clear the cached engine
    and S3 client that other integration modules hold for their own module-scoped
    fixtures, which makes whichever suite runs next fail depending on collection
    order.
    """
    yield
    dispose_s3_client()
    dispose_engine()


@pytest.fixture
def service(corpus: Path) -> CorpusIngestionService:
    return build_corpus_ingestion_service(CorpusStore(root=corpus))


class TestCorpusIngestion:
    def test_a_development_document_is_ingested_end_to_end(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        entry = add()

        report = service.ingest([entry])
        outcome = report.outcomes[0]

        assert outcome.state is ExtractionState.SUCCEEDED
        assert outcome.version_id is not None
        assert outcome.run_id is not None
        assert outcome.seconds > 0

    def test_the_counts_describe_what_was_produced(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        """Counted by aggregate query, not by loading every element back."""
        report = service.ingest([add()])
        counts = report.outcomes[0].counts

        assert counts is not None
        assert counts.pages == 2
        assert counts.blocks > 0
        assert counts.total == (
            counts.pages + counts.blocks + counts.tables + counts.cells
        )
        assert counts.coverage_gaps == 0

    def test_several_documents_are_ingested_in_one_run(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        report = service.ingest([add(), add()])

        assert len(report.outcomes) == 2
        assert report.failures == ()
        assert report.total_seconds > 0

    def test_re_ingesting_is_idempotent(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        """Intake is content-addressed and extraction is index-guarded."""
        entry = add()
        first = service.ingest([entry]).outcomes[0]

        second = service.ingest([entry]).outcomes[0]

        assert second.already_existed is True
        assert second.version_id == first.version_id
        assert second.run_id == first.run_id


class TestPreconditions:
    def test_a_held_out_document_is_refused_before_any_work(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        entry = add(split=Split.HELD_OUT_PDF_CORE, frozen=True)

        with pytest.raises(HeldOutAccessError):
            service.ingest([entry])

    def test_one_held_out_entry_stops_the_whole_selection(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        """Preconditions cover the selection, not each document as it is reached.

        Checking per document would ingest the development entry first and only
        then refuse, leaving a partial run nobody asked for.
        """
        development = add()
        frozen = add(split=Split.HELD_OUT_PDF_CORE, frozen=True)

        with pytest.raises(HeldOutAccessError):
            service.ingest([development, frozen])

        with get_engine().connect() as connection:
            stored = connection.execute(
                text(
                    "SELECT count(*) FROM document_versions WHERE content_hash = :digest"
                ).bindparams(digest=development.sha256)
            ).scalar_one()

        assert stored == 0

    def test_a_changed_document_is_refused_before_intake(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        """Content addressing would give corrupted bytes a valid key (§32.7)."""
        entry = add(corrupt=True)

        with pytest.raises(ChecksumMismatchError):
            service.ingest([entry])

        with get_engine().connect() as connection:
            stored = connection.execute(
                text(
                    "SELECT count(*) FROM document_versions WHERE content_hash = :digest"
                ).bindparams(digest=entry.sha256)
            ).scalar_one()

        assert stored == 0


class TestDocumentFailures:
    def test_an_unreadable_document_does_not_abort_the_run(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        """§11.11: a failed filing is a coverage gap, not a reason to stop."""
        broken = add(content=build_malformed_pdf())
        good = add()

        report = service.ingest([broken, good])

        assert len(report.outcomes) == 2
        assert report.outcomes[1].state is ExtractionState.SUCCEEDED

    def test_a_recovered_empty_document_is_recorded_as_failed(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        """PyMuPDF repairs the broken xref and yields nothing; that is not success."""
        report = service.ingest([add(content=build_malformed_pdf())])
        outcome = report.outcomes[0]

        assert outcome.state is ExtractionState.FAILED
        assert outcome.succeeded is False
        assert report.failures == (outcome,)

    def test_a_failure_records_no_document_content(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        report = service.ingest([add(content=build_malformed_pdf())])

        rendered = repr(report.outcomes[0])
        assert "Revenue" not in rendered
        assert ".pdf" not in rendered


class TestMeasurement:
    def test_memory_is_measured_only_when_requested(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
    ) -> None:
        """Tracing distorts timings, so throughput runs leave it off."""
        entry = add()

        assert service.ingest([entry]).outcomes[0].peak_python_mib is None

        measured = service.ingest([entry], measure_memory=True).outcomes[0]
        assert measured.peak_python_mib is not None
        assert measured.peak_python_mib > 0


class TestReport:
    def test_a_json_report_carries_counts_and_no_content(
        self,
        service: CorpusIngestionService,
        add: Callable[..., CorpusEntry],
        tmp_path: Path,
    ) -> None:
        from finsight.cli.main import _write_report

        report = service.ingest([add()])
        destination = tmp_path / "artifacts" / "run.json"

        _write_report(destination, report)
        payload = json.loads(destination.read_text(encoding="utf-8"))

        assert payload["documents"][0]["pages"] == 2
        assert payload["documents"][0]["state"] == "succeeded"
        assert "Revenue" not in destination.read_text(encoding="utf-8")
