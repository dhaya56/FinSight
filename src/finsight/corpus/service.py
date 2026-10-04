"""Driving intake and extraction over the corpus.

This orchestrates services that already exist rather than adding a pipeline. Its
value is in what it records: the per-document evidence ENV records are written
from, and which a synthetic fixture cannot supply.

**Governance fails fast; documents fail individually.** Every entry is checked
for readability before any work starts, so a held-out split is refused before the
first byte is read rather than halfway through. A document that cannot be parsed,
by contrast, is recorded and the run continues — §11.11 treats a failed document
as a coverage gap in the corpus, not as a reason to abandon the rest.

**Checksums are verified before intake, not after.** Intake is content-addressed,
so storing a corrupted file would give it a perfectly valid object key under the
wrong identity, and nothing downstream would notice. The manifest is the
authority on what the bytes should be.
"""

import time
import tracemalloc
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from finsight.corpus.manifest import CorpusEntry
from finsight.corpus.store import CorpusStore
from finsight.domain.errors import DocumentRejectedError
from finsight.domain.representations.source import ExtractionState
from finsight.extraction.contracts import ExtractionError
from finsight.extraction.service import ExtractionService, build_extraction_service
from finsight.ingestion.intake import IntakeService, build_intake_service
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.document_metadata import (
    DocumentMetadataRepository,
)
from finsight.persistence.repositories.source import ElementCounts, SourceRepository
from finsight.persistence.tables.document_metadata import SOURCE_CORPUS_MANIFEST


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    """What happened to one corpus document."""

    document_id: str
    byte_size: int
    version_id: UUID | None = None
    already_existed: bool = False
    run_id: UUID | None = None
    state: ExtractionState | None = None
    counts: ElementCounts | None = None
    seconds: float = 0.0
    peak_python_mib: float | None = None
    """Peak Python-side allocation, when measurement was requested.

    Not process memory. ``tracemalloc`` sees Python allocations only, and PyMuPDF
    does its page work in C, so this captures the document bytes and the element
    objects while missing the parser's own buffers. Resident set size is the
    figure that matters for a host with little headroom, and it needs a
    dependency this project has not admitted.
    """

    failure: str | None = None
    """Why this document produced nothing, as a short code. Never its content."""

    @property
    def succeeded(self) -> bool:
        return self.failure is None and self.state is not ExtractionState.FAILED


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """The result of ingesting a selection of the corpus."""

    outcomes: tuple[IngestionOutcome, ...]

    @property
    def total_seconds(self) -> float:
        return sum(outcome.seconds for outcome in self.outcomes)

    @property
    def failures(self) -> tuple[IngestionOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.succeeded)


class CorpusIngestionService:
    """Put corpus documents through intake and extraction, and record what happened."""

    def __init__(
        self,
        store: CorpusStore,
        intake: IntakeService,
        extraction: ExtractionService,
        session_scope_factory: Callable[[], AbstractContextManager[Session]] = session_scope,
    ) -> None:
        self._store = store
        self._intake = intake
        self._extraction = extraction
        self._session_scope = session_scope_factory

    def ingest(
        self,
        entries: Sequence[CorpusEntry],
        *,
        measure_memory: bool = False,
    ) -> IngestionReport:
        """Ingest each entry, continuing past documents that fail.

        Both preconditions are checked across the *whole* selection before any
        document is processed. A frozen split or a file that no longer matches
        its checksum is a fault in the corpus, not in a filing, and discovering
        it after two documents have already been ingested would leave a partial
        run that nobody asked for.

        Raises:
            HeldOutAccessError: an entry belongs to a frozen split.
            DocumentMissingError, ChecksumMismatchError: the corpus is not intact.
        """
        for entry in entries:
            self._store.ensure_readable(entry)
            self._store.require_intact(entry)

        return IngestionReport(
            outcomes=tuple(
                self._ingest_one(entry, measure_memory=measure_memory)
                for entry in entries
            )
        )

    def _ingest_one(
        self,
        entry: CorpusEntry,
        *,
        measure_memory: bool,
    ) -> IngestionOutcome:
        if measure_memory:
            tracemalloc.start()
        started = time.perf_counter()
        try:
            return self._run(entry, started=started, measure_memory=measure_memory)
        except (DocumentRejectedError, ExtractionError) as error:
            return IngestionOutcome(
                document_id=entry.document_id,
                byte_size=entry.byte_size,
                seconds=time.perf_counter() - started,
                peak_python_mib=_peak_mib() if measure_memory else None,
                failure=type(error).__name__,
            )
        finally:
            if measure_memory and tracemalloc.is_tracing():
                tracemalloc.stop()

    def _run(
        self,
        entry: CorpusEntry,
        *,
        started: float,
        measure_memory: bool,
    ) -> IngestionOutcome:
        with self._store.open_content(entry) as handle:
            received = self._intake.receive(
                handle,
                declared_content_type=entry.format,
                filename=entry.filename,
            )

        self._record_metadata(entry, version_id=received.version_id)

        recorded = self._extraction.extract(received.version_id)

        with self._session_scope() as session:
            counts = SourceRepository(session).counts_for_run(run_id=recorded.run_id)

        return IngestionOutcome(
            document_id=entry.document_id,
            byte_size=entry.byte_size,
            version_id=received.version_id,
            already_existed=received.already_existed,
            run_id=recorded.run_id,
            state=recorded.state,
            counts=counts,
            seconds=time.perf_counter() - started,
            peak_python_mib=_peak_mib() if measure_memory else None,
        )


    def _record_metadata(self, entry: CorpusEntry, *, version_id: UUID) -> None:
        """Record the §20.2 filter values the manifest declares for this entry.

        Before extraction rather than after, so a filing whose extraction fails is
        still identifiable. The values describe the document, not the parse, and
        losing them because a parser stumbled would make the failure harder to
        investigate than it needs to be.

        ``source`` records that these are a curator's claim rather than something
        read from the filing. Nothing extracts issuer or period yet, and the
        distinction has to survive until something does.
        """
        with self._session_scope() as session:
            DocumentMetadataRepository(session).record(
                document_version_id=version_id,
                issuer_name=entry.issuer_name,
                issuer_identifier=entry.issuer_identifier or None,
                document_type=str(entry.document_type),
                jurisdiction=entry.jurisdiction or None,
                fiscal_period=entry.fiscal_period,
                period_end=entry.period_end,
                reporting_basis=entry.reporting_basis,
                currency=entry.currency or None,
                units_as_presented=entry.units_as_presented or None,
                source=SOURCE_CORPUS_MANIFEST,
            )


def _peak_mib() -> float:
    if not tracemalloc.is_tracing():
        return 0.0
    _current, peak = tracemalloc.get_traced_memory()
    return peak / 1024 / 1024


def build_corpus_ingestion_service(store: CorpusStore) -> CorpusIngestionService:
    """Wire corpus ingestion to the configured object store, database and producer."""
    return CorpusIngestionService(
        store=store,
        intake=build_intake_service(),
        extraction=build_extraction_service(),
    )
