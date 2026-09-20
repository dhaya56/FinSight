"""End-to-end extraction against real infrastructure.

The third arrow of the PROJECT_BLUEPRINT.md §10.6 flow — stored bytes become
citable source elements — exercised against the running object store and
database with no fakes anywhere. Documents arrive through real intake, so what
is extracted is genuinely what was preserved.

Rows created here are removed afterwards. Stored objects are not, for the reason
given in ``test_ingestion_pipeline.py``: the port has no delete, and
content-addressed objects converge rather than accumulate.
"""

import hashlib
from collections.abc import Callable, Iterator
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from finsight.domain.representations.source import ElementType, ExtractionState
from finsight.extraction.contracts import (
    DocumentUnreadableError,
    UnsupportedFormatError,
)
from finsight.extraction.pdf.pymupdf_adapter import PyMuPdfProducer
from finsight.extraction.pdf.quality_signals import NO_TEXT_EXTRACTED
from finsight.extraction.service import (
    ExtractionService,
    TransactionalExtractionRecorder,
    build_extraction_service,
)
from finsight.ingestion.intake import IntakeService, build_intake_service
from finsight.object_store.s3_store import build_s3_object_store, dispose_s3_client
from finsight.persistence.database import dispose_engine, get_engine, session_scope
from finsight.persistence.repositories.source import SourceRepository
from finsight.persistence.tables.documents import DocumentVersion
from pdf_fixtures import PlacedText, build_encrypted_pdf, build_pdf

pytestmark = pytest.mark.integration

CLEANUP = """
    UPDATE document_versions SET current_extraction_run_id = NULL
    WHERE content_hash = ANY(:hashes);

    DELETE FROM source_elements WHERE extraction_run_id IN (
        SELECT id FROM extraction_runs WHERE document_version_id IN (
            SELECT id FROM document_versions WHERE content_hash = ANY(:hashes)
        )
    );

    DELETE FROM extraction_runs WHERE document_version_id IN (
        SELECT id FROM document_versions WHERE content_hash = ANY(:hashes)
    );

    DELETE FROM document_versions WHERE content_hash = ANY(:hashes);

    DELETE FROM documents
    WHERE id NOT IN (SELECT document_id FROM document_versions);
"""
"""Unwinds a test's rows in dependency order.

The pointer is cleared first: ``document_versions`` references
``extraction_runs`` and ``extraction_runs`` references ``document_versions``, so
neither can be deleted while the pointer still stands.
"""


@pytest.fixture(scope="module")
def intake() -> Iterator[IntakeService]:
    service = build_intake_service()
    yield service
    dispose_s3_client()
    dispose_engine()


@pytest.fixture(scope="module")
def extraction() -> ExtractionService:
    return build_extraction_service()


@pytest.fixture
def stored(intake: IntakeService) -> Iterator[Callable[[bytes, str], UUID]]:
    """Put bytes through real intake and clean up whatever they created."""
    hashes: list[str] = []

    def _store(content: bytes, content_type: str = "application/pdf") -> UUID:
        hashes.append(hashlib.sha256(content).hexdigest())
        return intake.receive(
            BytesIO(content), declared_content_type=content_type
        ).version_id

    yield _store

    if hashes:
        with get_engine().begin() as connection:
            for statement in filter(None, (s.strip() for s in CLEANUP.split(";"))):
                query = text(statement)
                if ":hashes" in statement:
                    query = query.bindparams(hashes=hashes)
                connection.execute(query)


def one_page(body: str = "Revenue from operations") -> bytes:
    """A distinct single-page PDF, so no two tests share a content hash."""
    return build_pdf(
        [
            [
                PlacedText(body, x=72, y_from_bottom=700),
                PlacedText(uuid4().hex, x=72, y_from_bottom=60),
            ]
        ]
    )


def current_run_id(version_id: UUID) -> UUID | None:
    with session_scope() as session:
        version = session.get(DocumentVersion, version_id)
        assert version is not None
        return version.current_extraction_run_id


class TestExtractionEndToEnd:
    def test_a_stored_document_becomes_source_elements(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        version_id = stored(one_page())

        result = extraction.extract(version_id)

        assert result.state is ExtractionState.SUCCEEDED
        assert result.element_count == 3
        assert result.already_existed is False

    def test_the_elements_are_readable_afterwards(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        version_id = stored(one_page("Profit before tax"))
        result = extraction.extract(version_id)

        with session_scope() as session:
            elements = SourceRepository(session).elements_for_run(run_id=result.run_id)
            blocks = [
                element.text
                for element in elements
                if element.element_type == ElementType.BLOCK.value
            ]

        assert "Profit before tax\n" in blocks

    def test_text_survives_the_whole_pipeline_unchanged(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        """Intake, object store, producer and PostgreSQL, end to end."""
        amount = "1,23,456.78"
        version_id = stored(one_page(amount))
        result = extraction.extract(version_id)

        with session_scope() as session:
            elements = SourceRepository(session).elements_for_run(run_id=result.run_id)
            texts = [element.text for element in elements if element.text]

        assert f"{amount}\n" in texts

    def test_the_version_points_at_the_run(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        version_id = stored(one_page())

        result = extraction.extract(version_id)

        assert current_run_id(version_id) == result.run_id


class TestIdempotence:
    def test_re_extracting_the_same_configuration_is_a_no_op(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        version_id = stored(one_page())
        first = extraction.extract(version_id)

        second = extraction.extract(version_id)

        assert second.already_existed is True
        assert second.run_id == first.run_id

    def test_a_repeat_run_writes_no_second_element_set(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        version_id = stored(one_page())
        extraction.extract(version_id)
        extraction.extract(version_id)

        with get_engine().connect() as connection:
            runs = connection.execute(
                text(
                    "SELECT count(*) FROM extraction_runs "
                    "WHERE document_version_id = :version_id"
                ).bindparams(version_id=version_id)
            ).scalar_one()

        assert runs == 1

    def test_a_configuration_bump_produces_a_new_run_and_moves_the_pointer(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        """Re-extraction under a new configuration is new work, not a duplicate."""
        version_id = stored(one_page())
        first = extraction.extract(version_id)

        bumped = ExtractionService(
            object_store=build_s3_object_store(),
            producer=PyMuPdfProducer(),
            recorder=TransactionalExtractionRecorder(),
            config_version="2",
        )
        second = bumped.extract(version_id)

        assert second.run_id != first.run_id
        assert second.already_existed is False
        assert current_run_id(version_id) == second.run_id

    def test_the_superseded_run_and_its_elements_remain(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        """Citations issued against the old run must keep resolving (§27.7)."""
        version_id = stored(one_page())
        first = extraction.extract(version_id)

        ExtractionService(
            object_store=build_s3_object_store(),
            producer=PyMuPdfProducer(),
            recorder=TransactionalExtractionRecorder(),
            config_version="2",
        ).extract(version_id)

        with session_scope() as session:
            survivors = SourceRepository(session).elements_for_run(run_id=first.run_id)

        assert len(survivors) == first.element_count


class TestCoverageGaps:
    def test_an_empty_page_is_a_partial_run_not_a_failed_one(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        """One unreadable region must not discard the pages that did extract."""
        content = build_pdf(
            [[PlacedText(uuid4().hex, x=72, y_from_bottom=700)], []]
        )
        version_id = stored(content)

        result = extraction.extract(version_id)

        assert result.state is ExtractionState.PARTIAL

    def test_the_gap_is_recorded_as_a_row(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        content = build_pdf(
            [[PlacedText(uuid4().hex, x=72, y_from_bottom=700)], []]
        )
        version_id = stored(content)
        result = extraction.extract(version_id)

        with session_scope() as session:
            elements = SourceRepository(session).elements_for_run(run_id=result.run_id)
            reasons = [element.failure_reason for element in elements]

        assert NO_TEXT_EXTRACTED in reasons

    def test_a_partial_run_still_becomes_the_current_run(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        content = build_pdf(
            [[PlacedText(uuid4().hex, x=72, y_from_bottom=700)], []]
        )
        version_id = stored(content)

        result = extraction.extract(version_id)

        assert current_run_id(version_id) == result.run_id


class TestControlledFailure:
    def test_an_unreadable_document_records_a_failed_run(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        """Signed as a PDF, so intake accepts it; unparsable, so extraction fails."""
        content = b"%PDF-1.7\n" + uuid4().hex.encode() + b"\ntrailer\n%%EOF\n"
        version_id = stored(content)

        with pytest.raises(DocumentUnreadableError):
            extraction.extract(version_id)

        with get_engine().connect() as connection:
            state = connection.execute(
                text(
                    "SELECT state FROM extraction_runs "
                    "WHERE document_version_id = :version_id"
                ).bindparams(version_id=version_id)
            ).scalar_one()

        assert state == ExtractionState.FAILED.value

    def test_a_failed_run_leaves_the_pointer_unset(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        """A pointer to a failed run would look like a successfully empty document."""
        content = b"%PDF-1.7\n" + uuid4().hex.encode() + b"\ntrailer\n%%EOF\n"
        version_id = stored(content)

        with pytest.raises(DocumentUnreadableError):
            extraction.extract(version_id)

        assert current_run_id(version_id) is None

    def test_a_failed_run_does_not_block_a_retry(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        """The partial unique index excludes failed rows, so the attempt repeats."""
        content = b"%PDF-1.7\n" + uuid4().hex.encode() + b"\ntrailer\n%%EOF\n"
        version_id = stored(content)

        for _ in range(2):
            with pytest.raises(DocumentUnreadableError):
                extraction.extract(version_id)

        with get_engine().connect() as connection:
            runs = connection.execute(
                text(
                    "SELECT count(*) FROM extraction_runs "
                    "WHERE document_version_id = :version_id"
                ).bindparams(version_id=version_id)
            ).scalar_one()

        assert runs == 2

    def test_an_encrypted_document_is_refused(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        version_id = stored(build_encrypted_pdf())

        with pytest.raises(DocumentUnreadableError, match="password"):
            extraction.extract(version_id)

    def test_a_format_without_a_producer_is_refused(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        """Intake accepts HTML; no producer handles it yet."""
        content = b"<html><body><p>" + uuid4().hex.encode() + b"</p></body></html>"
        version_id = stored(content, "text/html")

        with pytest.raises(UnsupportedFormatError):
            extraction.extract(version_id)

    def test_an_unsupported_format_records_no_run(
        self,
        extraction: ExtractionService,
        stored: Callable[..., UUID],
    ) -> None:
        content = b"<html><body><p>" + uuid4().hex.encode() + b"</p></body></html>"
        version_id = stored(content, "text/html")

        with pytest.raises(UnsupportedFormatError):
            extraction.extract(version_id)

        with get_engine().connect() as connection:
            runs = connection.execute(
                text(
                    "SELECT count(*) FROM extraction_runs "
                    "WHERE document_version_id = :version_id"
                ).bindparams(version_id=version_id)
            ).scalar_one()

        assert runs == 0
