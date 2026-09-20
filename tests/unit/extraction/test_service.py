"""Tests for extraction orchestration.

The ordering tests are the point. What the service must guarantee is that the
object-store read and the parse happen with no database transaction open
(§29.7), and that a document which cannot be read still leaves a recorded
terminal state (§11.10).

The structural half of that guarantee is not asserted here because it cannot be
violated: :class:`ExtractionService` takes no session and no session factory, so
it has nothing to hold open. Transactions exist only inside the recorder, and
each of its methods opens and closes its own. These tests cover the half that
*can* regress — the order the service calls things in.
"""

import io
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import IO
from uuid import UUID, uuid4

import pytest

from finsight.domain.representations.source import (
    BlockLocation,
    ElementType,
    ExtractedElement,
    ExtractionState,
    PageLocation,
    RecordedExtraction,
)
from finsight.extraction.contracts import (
    DocumentUnreadableError,
    UnknownDocumentVersionError,
    UnsupportedFormatError,
    derive_state,
)
from finsight.extraction.service import (
    ExtractionPlan,
    ExtractionService,
    VersionForExtraction,
)

OBJECT_KEY = "originals/sha256/ab/cd/" + "ab" * 32
METHOD = "fake"
METHOD_VERSION = "0.0.0-test"


def page(*, failure_reason: str | None = None, text: str | None = "Revenue") -> ExtractedElement:
    children = (
        ()
        if text is None
        else (
            ExtractedElement(
                element_type=ElementType.BLOCK,
                ordinal=0,
                locator="p. 1",
                location=BlockLocation(bbox=(72.0, 100.0, 300.0, 120.0)),
                extraction_method=METHOD,
                extraction_method_version=METHOD_VERSION,
                text=text,
            ),
        )
    )
    return ExtractedElement(
        element_type=ElementType.PAGE,
        ordinal=0,
        locator="p. 1",
        location=PageLocation(page_number=1, width=595.0, height=842.0, rotation=0),
        extraction_method=METHOD,
        extraction_method_version=METHOD_VERSION,
        failure_reason=failure_reason,
        children=children,
    )


class FakeObjectStore:
    """Records when it is read, so call ordering can be asserted."""

    def __init__(self, journal: list[str], data: bytes = b"%PDF-fake") -> None:
        self._journal = journal
        self._data = data

    @contextmanager
    def open_stream(self, key: str) -> Iterator[IO[bytes]]:
        self._journal.append(f"read:{key}")
        yield io.BytesIO(self._data)
        self._journal.append("close-stream")


class FakeProducer:
    """Returns prepared elements, or raises a prepared error."""

    method = METHOD
    method_version = METHOD_VERSION

    def __init__(
        self,
        journal: list[str],
        elements: tuple[ExtractedElement, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self._journal = journal
        self._elements = elements
        self._error = error

    def produce(self, source: IO[bytes]) -> tuple[ExtractedElement, ...]:
        source.read()
        self._journal.append("produce")
        if self._error is not None:
            raise self._error
        return self._elements


class FakeRecorder:
    """Stands in for the database, capturing what it was asked to record."""

    def __init__(
        self,
        journal: list[str],
        *,
        version: VersionForExtraction,
        existing: RecordedExtraction | None = None,
        missing: bool = False,
    ) -> None:
        self._journal = journal
        self._version = version
        self._existing = existing
        self._missing = missing
        self.recorded: Sequence[ExtractedElement] | None = None
        self.configurations: list[tuple[str, str]] = []

    def prepare(
        self,
        *,
        document_version_id: UUID,
        producer_policy: str,
        config_version: str,
    ) -> ExtractionPlan:
        self._journal.append("prepare")
        self.configurations.append((producer_policy, config_version))
        if self._missing:
            raise UnknownDocumentVersionError("no such document version is recorded")
        return ExtractionPlan(version=self._version, existing=self._existing)

    def record(
        self,
        *,
        document_version_id: UUID,
        elements: Sequence[ExtractedElement],
        producer_policy: str,
        config_version: str,
    ) -> RecordedExtraction:
        self._journal.append("record")
        self.recorded = elements
        return RecordedExtraction(
            run_id=uuid4(),
            document_version_id=document_version_id,
            state=derive_state(elements),
            element_count=len(elements),
        )

    def record_failure(
        self,
        *,
        document_version_id: UUID,
        producer_policy: str,
        config_version: str,
    ) -> RecordedExtraction:
        self._journal.append("record_failure")
        return RecordedExtraction(
            run_id=uuid4(),
            document_version_id=document_version_id,
            state=ExtractionState.FAILED,
            element_count=0,
        )


def build(
    journal: list[str],
    *,
    elements: tuple[ExtractedElement, ...] = (),
    error: Exception | None = None,
    existing: RecordedExtraction | None = None,
    content_type: str = "application/pdf",
    missing: bool = False,
) -> tuple[ExtractionService, FakeRecorder]:
    version = VersionForExtraction(
        document_version_id=uuid4(),
        object_key=OBJECT_KEY,
        detected_content_type=content_type,
    )
    recorder = FakeRecorder(
        journal, version=version, existing=existing, missing=missing
    )
    service = ExtractionService(
        object_store=FakeObjectStore(journal),
        producer=FakeProducer(journal, elements=elements, error=error),
        recorder=recorder,
    )
    return service, recorder


class TestOrdering:
    def test_the_document_is_read_and_parsed_between_transactions(self) -> None:
        """Nothing is parsed while the database is being talked to."""
        journal: list[str] = []
        service, _ = build(journal, elements=(page(),))

        service.extract(uuid4())

        assert journal == [
            "prepare",
            f"read:{OBJECT_KEY}",
            "produce",
            "close-stream",
            "record",
        ]

    def test_the_stream_is_closed_before_recording(self) -> None:
        """The object-store connection is released before a transaction opens."""
        journal: list[str] = []
        service, _ = build(journal, elements=(page(),))

        service.extract(uuid4())

        assert journal.index("close-stream") < journal.index("record")


class TestIdempotence:
    def test_an_existing_run_short_circuits(self) -> None:
        """Re-extraction reads nothing and parses nothing."""
        journal: list[str] = []
        existing = RecordedExtraction(
            run_id=uuid4(),
            document_version_id=uuid4(),
            state=ExtractionState.SUCCEEDED,
            element_count=12,
            already_existed=True,
        )
        service, _ = build(journal, existing=existing)

        result = service.extract(uuid4())

        assert result is existing
        assert journal == ["prepare"]

    def test_the_configuration_is_passed_to_the_lookup(self) -> None:
        """A different configuration must be able to find its own run."""
        journal: list[str] = []
        service, recorder = build(journal, elements=(page(),))

        service.extract(uuid4())

        assert recorder.configurations == [("pdf-native", "1")]

    def test_a_custom_configuration_is_honoured(self) -> None:
        journal: list[str] = []
        version = VersionForExtraction(
            document_version_id=uuid4(),
            object_key=OBJECT_KEY,
            detected_content_type="application/pdf",
        )
        recorder = FakeRecorder(journal, version=version)
        service = ExtractionService(
            object_store=FakeObjectStore(journal),
            producer=FakeProducer(journal, elements=(page(),)),
            recorder=recorder,
            producer_policy="experimental",
            config_version="7",
        )

        service.extract(uuid4())

        assert recorder.configurations == [("experimental", "7")]


class TestFormatGuard:
    @pytest.mark.parametrize(
        "content_type",
        [
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/html",
            "application/xml",
        ],
    )
    def test_a_format_without_a_producer_is_refused(self, content_type: str) -> None:
        """Intake accepts these; no producer handles them yet."""
        journal: list[str] = []
        service, _ = build(journal, content_type=content_type)

        with pytest.raises(UnsupportedFormatError):
            service.extract(uuid4())

    def test_an_unsupported_format_records_nothing(self) -> None:
        """Blaming the document with a failed run would misattribute the gap."""
        journal: list[str] = []
        service, _ = build(journal, content_type="text/html")

        with pytest.raises(UnsupportedFormatError):
            service.extract(uuid4())

        assert journal == ["prepare"]


class TestFailure:
    def test_an_unreadable_document_records_a_failed_run(self) -> None:
        """§11.10: a failure reaches a recorded terminal state, not silence."""
        journal: list[str] = []
        service, _ = build(
            journal, error=DocumentUnreadableError("the document is password protected")
        )

        with pytest.raises(DocumentUnreadableError):
            service.extract(uuid4())

        assert journal == [
            "prepare",
            f"read:{OBJECT_KEY}",
            "produce",
            "record_failure",
        ]

    def test_the_original_error_still_reaches_the_caller(self) -> None:
        """Recording the failure must not swallow the reason for it."""
        journal: list[str] = []
        service, _ = build(journal, error=DocumentUnreadableError("password protected"))

        with pytest.raises(DocumentUnreadableError, match="password"):
            service.extract(uuid4())

    def test_an_unknown_version_is_refused_before_anything_is_read(self) -> None:
        journal: list[str] = []
        service, _ = build(journal, missing=True)

        with pytest.raises(UnknownDocumentVersionError):
            service.extract(uuid4())

        assert journal == ["prepare"]


class TestRecordedOutput:
    def test_the_produced_elements_are_the_ones_recorded(self) -> None:
        """Nothing filters or reshapes elements between producer and store."""
        journal: list[str] = []
        elements = (page(), page(failure_reason="no_text_extracted", text=None))
        service, recorder = build(journal, elements=elements)

        service.extract(uuid4())

        assert recorder.recorded == elements

    def test_a_coverage_gap_yields_a_partial_run(self) -> None:
        journal: list[str] = []
        service, _ = build(
            journal,
            elements=(page(), page(failure_reason="no_text_extracted", text=None)),
        )

        assert service.extract(uuid4()).state is ExtractionState.PARTIAL

    def test_a_clean_document_yields_a_successful_run(self) -> None:
        journal: list[str] = []
        service, _ = build(journal, elements=(page(),))

        assert service.extract(uuid4()).state is ExtractionState.SUCCEEDED

    def test_a_document_yielding_no_elements_is_a_failed_run(self) -> None:
        """A zero-page document is not a successfully extracted empty document."""
        journal: list[str] = []
        service, _ = build(journal, elements=())

        assert service.extract(uuid4()).state is ExtractionState.FAILED
