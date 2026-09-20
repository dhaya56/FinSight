"""Extraction orchestration: read the original, produce elements, record them.

This is the third arrow of the PROJECT_BLUEPRINT.md §10.6 flow, and the ordering
is the whole design.

**Nothing is parsed while a transaction is open.** The object-store read and the
parse both complete before the recording transaction begins (§29.7). A
five-hundred-page filing can take a long time to parse, and a PostgreSQL
transaction held across that would pin a connection, block vacuum, and put a
network call inside a database transaction — three ways for a slow document to
become a database problem.

**A run is recorded only once it has produced something.** The alternative —
opening a run row before parsing — would put the row's lifetime around a long
network and CPU operation for no gain, because the partial unique index that
makes re-extraction idempotent excludes failed rows and so would not bite until
completion anyway. A document that *cannot* be read still gets a failed run row,
because §11.10 requires failures to reach a recorded terminal state rather than
leaving silence.

**Recording is one transaction.** The run, its elements, and the pointer flip
commit together. A partial write would leave a version pointing at an element
set that does not match its run.

Transactions live in the recorder rather than the service, mirroring
``IntakeService`` and its ``VersionRecorder``, so the orchestration above can be
tested without a database and the transaction boundary has exactly one home.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from finsight.domain.representations.source import (
    ExtractedElement,
    ExtractionState,
    RecordedExtraction,
)
from finsight.extraction.contracts import (
    DocumentUnreadableError,
    PdfProducer,
    UnknownDocumentVersionError,
    UnsupportedFormatError,
    derive_state,
)
from finsight.extraction.pdf.pymupdf_adapter import PyMuPdfProducer
from finsight.object_store.port import ObjectStore
from finsight.object_store.s3_store import build_s3_object_store
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.source import SourceRepository
from finsight.persistence.tables.documents import DocumentVersion
from finsight.persistence.tables.source import FORMAT_PDF

PDF_CONTENT_TYPE: Final = "application/pdf"

PRODUCER_POLICY: Final = "pdf-native"
"""Which selection policy orchestrated a run.

One policy exists, and it routes every page to the single registered producer.
Recording it now means that when §12.9's per-page routing arrives, the runs made
before it can still be told apart from the runs made after — which is what makes
an evaluation across them meaningful.
"""

EXTRACTION_CONFIG_VERSION: Final = "1"
"""The versioned extraction configuration.

Bump this whenever a change would make the same document produce different
elements — a different producer, different reading-order rules, a different
choice about what counts as a coverage gap. Bumping it causes the next run to be
recorded as new work rather than silently changing what stored citations point
at. Leaving it unchanged after such a change is the mistake this constant exists
to prevent.
"""


@dataclass(frozen=True, slots=True)
class VersionForExtraction:
    """What the service needs to know about a stored version to extract it."""

    document_version_id: UUID
    object_key: str
    detected_content_type: str


@dataclass(frozen=True, slots=True)
class ExtractionPlan:
    """The state of the world before extraction starts."""

    version: VersionForExtraction
    existing: RecordedExtraction | None
    """A non-failed run already recorded for this exact configuration, if any."""


class ExtractionRecorder(Protocol):
    """Reads and records extraction state in the authoritative store."""

    def prepare(
        self,
        *,
        document_version_id: UUID,
        producer_policy: str,
        config_version: str,
    ) -> ExtractionPlan:
        """Look up the version and any run already recorded for this configuration.

        Raises:
            UnknownDocumentVersionError: no such version is recorded.
        """
        ...

    def record(
        self,
        *,
        document_version_id: UUID,
        elements: Sequence[ExtractedElement],
        producer_policy: str,
        config_version: str,
    ) -> RecordedExtraction:
        """Persist a run, its elements, and the resulting pointer, atomically."""
        ...

    def record_failure(
        self,
        *,
        document_version_id: UUID,
        producer_policy: str,
        config_version: str,
    ) -> RecordedExtraction:
        """Record that extraction was attempted and produced nothing usable."""
        ...


class TransactionalExtractionRecorder:
    """Record extraction state in short, self-contained transactions."""

    def __init__(
        self,
        session_scope_factory: Callable[[], AbstractContextManager[Session]] = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    def prepare(
        self,
        *,
        document_version_id: UUID,
        producer_policy: str,
        config_version: str,
    ) -> ExtractionPlan:
        with self._session_scope() as session:
            version = session.get(DocumentVersion, document_version_id)
            if version is None:
                raise UnknownDocumentVersionError("no such document version is recorded")

            found = VersionForExtraction(
                document_version_id=version.id,
                object_key=version.object_key,
                detected_content_type=version.detected_content_type,
            )
            run = SourceRepository(session).run_for_configuration(
                document_version_id=document_version_id,
                producer_policy=producer_policy,
                config_version=config_version,
            )
            existing = (
                None
                if run is None
                else RecordedExtraction(
                    run_id=run.id,
                    document_version_id=document_version_id,
                    state=ExtractionState(run.state),
                    element_count=run.element_count,
                    already_existed=True,
                )
            )
            return ExtractionPlan(version=found, existing=existing)

    def record(
        self,
        *,
        document_version_id: UUID,
        elements: Sequence[ExtractedElement],
        producer_policy: str,
        config_version: str,
    ) -> RecordedExtraction:
        state = derive_state(elements)
        with self._session_scope() as session:
            repository = SourceRepository(session)
            run = repository.start_run(
                document_version_id=document_version_id,
                format=FORMAT_PDF,
                producer_policy=producer_policy,
                config_version=config_version,
            )
            written = repository.record_elements(run_id=run.id, elements=elements)
            repository.complete_run(run=run, state=state, element_count=written)
            if state is not ExtractionState.FAILED:
                repository.set_current_run(run=run)

            return RecordedExtraction(
                run_id=run.id,
                document_version_id=document_version_id,
                state=state,
                element_count=written,
            )

    def record_failure(
        self,
        *,
        document_version_id: UUID,
        producer_policy: str,
        config_version: str,
    ) -> RecordedExtraction:
        """Leave a diagnostic row without touching the current-run pointer.

        Failed runs are excluded from the partial unique index, so the row is
        evidence of the attempt and never blocks a retry.
        """
        with self._session_scope() as session:
            repository = SourceRepository(session)
            run = repository.start_run(
                document_version_id=document_version_id,
                format=FORMAT_PDF,
                producer_policy=producer_policy,
                config_version=config_version,
            )
            repository.complete_run(
                run=run, state=ExtractionState.FAILED, element_count=0
            )
            return RecordedExtraction(
                run_id=run.id,
                document_version_id=document_version_id,
                state=ExtractionState.FAILED,
                element_count=0,
            )


class ExtractionService:
    """Turn a stored document version into its source representation."""

    def __init__(
        self,
        object_store: ObjectStore,
        producer: PdfProducer,
        recorder: ExtractionRecorder,
        *,
        producer_policy: str = PRODUCER_POLICY,
        config_version: str = EXTRACTION_CONFIG_VERSION,
    ) -> None:
        self._object_store = object_store
        self._producer = producer
        self._recorder = recorder
        self._producer_policy = producer_policy
        self._config_version = config_version

    def extract(self, document_version_id: UUID) -> RecordedExtraction:
        """Extract one stored version, or report the run already recorded for it.

        Raises:
            UnknownDocumentVersionError: no such version is recorded.
            UnsupportedFormatError: no producer handles this version's format.
            DocumentUnreadableError: the document could not be opened. A failed
                run is recorded before this propagates.
        """
        plan = self._recorder.prepare(
            document_version_id=document_version_id,
            producer_policy=self._producer_policy,
            config_version=self._config_version,
        )
        if plan.existing is not None:
            return plan.existing

        if plan.version.detected_content_type != PDF_CONTENT_TYPE:
            raise UnsupportedFormatError(
                f"no producer is registered for {plan.version.detected_content_type}"
            )

        elements = self._produce(plan.version.object_key, document_version_id)
        return self._recorder.record(
            document_version_id=document_version_id,
            elements=elements,
            producer_policy=self._producer_policy,
            config_version=self._config_version,
        )

    def _produce(
        self,
        object_key: str,
        document_version_id: UUID,
    ) -> tuple[ExtractedElement, ...]:
        """Read and parse with no transaction open, recording an unreadable document.

        The failure is recorded and then re-raised rather than swallowed: the
        row says an attempt happened, and the exception says why, which a caller
        needs in order to distinguish a broken document from a broken pipeline.
        """
        try:
            with self._object_store.open_stream(object_key) as stream:
                return self._producer.produce(stream)
        except DocumentUnreadableError:
            self._recorder.record_failure(
                document_version_id=document_version_id,
                producer_policy=self._producer_policy,
                config_version=self._config_version,
            )
            raise


def build_extraction_service() -> ExtractionService:
    """Wire extraction to the configured object store, producer and database."""
    return ExtractionService(
        object_store=build_s3_object_store(),
        producer=PyMuPdfProducer(),
        recorder=TransactionalExtractionRecorder(),
    )
