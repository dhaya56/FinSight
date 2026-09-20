"""Source-representation persistence.

Elements are written in bulk from the first line of this module rather than one
at a time. A five-hundred-page offer document plausibly yields tens of thousands
of elements, and a per-row insert would hold a transaction open for the whole of
it — exactly what §29.7 forbids and what CLAUDE.md §7 means by bounded
transactions.

Identifiers stay server-side. ``uuidv7()`` gives time-ordered keys with good
index locality, and Python 3.12 has no UUIDv7 generator, so generating them in
the application would mean falling back to random UUIDs and losing that
ordering. Instead each depth of the element tree is inserted in one statement
that returns its new ids in parameter order, and those ids become the next
depth's parents.

There is no delete method, for the reason given in ``documents.py``: removal
arrives as tombstoning (§29.12), and superseded runs stay resolvable so that
citations issued against them keep working (§27.7).
"""

import datetime
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from finsight.domain.representations.source import ExtractedElement, ExtractionState
from finsight.persistence.tables.documents import DocumentVersion
from finsight.persistence.tables.source import ExtractionRun, SourceElement


class SourceRepository:
    """Read and record source elements within a caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def start_run(
        self,
        *,
        document_version_id: UUID,
        format: str,
        producer_policy: str,
        config_version: str,
    ) -> ExtractionRun:
        """Open a run in the ``failed`` state.

        A run starts as failed and is completed on success, rather than the
        reverse. A worker killed mid-extraction leaves a row that correctly says
        nothing usable was produced; the opposite default would leave an
        abandoned run advertising itself as complete, and no later process could
        tell it apart from a real one.

        The cost is that the partial unique index — which excludes failed rows —
        only bites when the run completes. Two workers racing on the same version
        would both extract, and the loser would fail at completion rather than at
        start. Correct, but wasteful, and it matters only once more than one
        worker exists; extraction is single-caller today.
        """
        run = ExtractionRun(
            document_version_id=document_version_id,
            format=format,
            producer_policy=producer_policy,
            config_version=config_version,
            state=ExtractionState.FAILED.value,
            element_count=0,
        )
        self._session.add(run)
        self._session.flush()
        return run

    def record_elements(
        self,
        *,
        run_id: UUID,
        elements: Sequence[ExtractedElement],
    ) -> int:
        """Insert an element tree and return how many rows were written.

        One statement per depth. The root elements go first, their returned ids
        become the parents of the next depth, and so on until the tree runs out.
        """
        written = 0
        level: list[tuple[UUID | None, ExtractedElement]] = [
            (None, element) for element in elements
        ]

        while level:
            rows = [
                _row_for(run_id=run_id, parent_id=parent_id, element=element)
                for parent_id, element in level
            ]
            new_ids = self._insert_returning_ids(rows)
            written += len(new_ids)
            level = [
                (new_id, child)
                for new_id, (_, element) in zip(new_ids, level, strict=True)
                for child in element.children
            ]

        return written

    def complete_run(
        self,
        *,
        run: ExtractionRun,
        state: ExtractionState,
        element_count: int,
    ) -> None:
        """Record how a run finished and how much it produced."""
        run.state = state.value
        run.element_count = element_count
        run.completed_at = datetime.datetime.now(tz=datetime.UTC)
        self._session.flush()

    def set_current_run(self, *, run: ExtractionRun) -> None:
        """Point a document version at the current output of the extraction stage.

        Refuses a run that did not produce usable output. A pointer to a failed
        run would hand every downstream reader an empty element set that looks
        like a successfully extracted empty document.
        """
        if run.state == ExtractionState.FAILED.value:
            raise ValueError("a failed extraction run cannot become the current run")

        version = self._session.get(DocumentVersion, run.document_version_id)
        if version is None:
            raise ValueError("extraction run references an unknown document version")

        version.current_extraction_run_id = run.id
        self._session.flush()

    def current_run(self, *, document_version_id: UUID) -> ExtractionRun | None:
        """Return the run a version currently points at, or None."""
        statement = (
            select(ExtractionRun)
            .join(
                DocumentVersion,
                DocumentVersion.current_extraction_run_id == ExtractionRun.id,
            )
            .where(DocumentVersion.id == document_version_id)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def run_for_configuration(
        self,
        *,
        document_version_id: UUID,
        producer_policy: str,
        config_version: str,
    ) -> ExtractionRun | None:
        """Return the non-failed run for this configuration, or None.

        The same triple the partial unique index covers, so a caller can avoid
        redoing work the database would refuse anyway.
        """
        statement = select(ExtractionRun).where(
            ExtractionRun.document_version_id == document_version_id,
            ExtractionRun.producer_policy == producer_policy,
            ExtractionRun.config_version == config_version,
            ExtractionRun.state != ExtractionState.FAILED.value,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def elements_for_run(self, *, run_id: UUID) -> Sequence[SourceElement]:
        """Return a run's elements with roots first, then children by parent.

        Enough to rebuild the two-level PDF hierarchy in memory. Full document
        order across a deeper tree is a recursive query over
        ``(extraction_run_id, parent_id, ordinal)``, which the covering index
        supports; this method does not attempt it.
        """
        statement = (
            select(SourceElement)
            .where(SourceElement.extraction_run_id == run_id)
            .order_by(
                SourceElement.parent_id.is_(None).desc(),
                SourceElement.parent_id,
                SourceElement.ordinal,
            )
        )
        return list(self._session.execute(statement).scalars())

    def _insert_returning_ids(self, rows: list[dict[str, Any]]) -> list[UUID]:
        """Insert one depth of the tree and return the new ids in parameter order.

        ``sort_by_parameter_order`` is what makes the returned ids line up with
        the rows that produced them. Without it the database may return them in
        any order, and every parent link would be assigned to the wrong child.
        """
        statement = insert(SourceElement).returning(
            SourceElement.id, sort_by_parameter_order=True
        )
        result = self._session.execute(statement, rows)
        return list(result.scalars())


def _row_for(
    *,
    run_id: UUID,
    parent_id: UUID | None,
    element: ExtractedElement,
) -> dict[str, Any]:
    """Flatten one element into an insert parameter set.

    ``id`` and ``created_at`` are omitted so the server defaults apply.
    """
    return {
        "extraction_run_id": run_id,
        "parent_id": parent_id,
        "ordinal": element.ordinal,
        "element_type": element.element_type.value,
        "extraction_method": element.extraction_method,
        "extraction_method_version": element.extraction_method_version,
        "locator": element.locator,
        "text": element.text,
        "char_count": element.char_count,
        "failure_reason": element.failure_reason,
        "location": element.location.to_mapping(),
    }


def count_elements(elements: Iterable[ExtractedElement]) -> int:
    """Count an element tree, including children.

    The caller needs this before ``complete_run``; counting the tree it already
    holds is cheaper than asking the database to count rows it just wrote.
    """
    total = 0
    for element in elements:
        total += 1 + count_elements(element.children)
    return total
