"""Source-representation persistence.

Elements are written in bulk from the first line of this module rather than one
at a time. A five-hundred-page offer document plausibly yields tens of thousands
of elements, and a per-row insert would hold a transaction open for the whole of
it — exactly what §29.7 forbids and what CLAUDE.md §7 means by bounded
transactions.

**Identifiers are allocated up front, in one batch, from the database.**
``uuidv7()`` gives time-ordered keys with good index locality and Python 3.12 has
no UUIDv7 generator, so the ids must come from PostgreSQL — but they do not have
to come back from the inserts. One ``SELECT uuidv7() FROM generate_series`` names
every id in the tree before anything is written, after which every depth inserts
through the plain path and nothing needs ``RETURNING``.

That replaces an earlier strategy where each depth with children asked for its
ids back. Two measurements forced the change. ENV-004 established that
correlating returned ids to their parameters costs roughly ten times the plain
insert rate, so the old strategy only avoided that cost for leaves. Tables broke
the assumption twice over: cells are leaves *and* need their ids, because
``source_table_cells`` is keyed on them; and a page holding both blocks and tables
puts leaves and parents on one depth, which sent the whole depth down the slow
path. Measured on 20,000 cells with their extension rows, returning ids ran at
1,302 and 1,152 cells/s across two runs against 4,539 and 3,878 pre-allocated —
about 3.4x, and 16s against 5s inside one transaction that §29.7 wants bounded.

There is no delete method, for the reason given in ``documents.py``: removal
arrives as tombstoning (§29.12), and superseded runs stay resolvable so that
citations issued against them keep working (§27.7).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from finsight.domain.representations.source import (
    ElementType,
    ExtractedElement,
    ExtractionState,
    TableSemantics,
)
from finsight.persistence.tables.documents import DocumentVersion
from finsight.persistence.tables.source import (
    ExtractionRun,
    SourceElement,
    SourceTable,
    SourceTableCell,
)


@dataclass(frozen=True, slots=True)
class ElementCounts:
    """What a run produced, counted rather than loaded."""

    total: int
    pages: int
    blocks: int
    tables: int
    cells: int
    coverage_gaps: int
    """Elements recording why they hold no text (§11.11), not elements missing."""


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
        """Insert an element tree with its semantics and return rows written.

        Ids for the whole tree are allocated first, so every depth inserts through
        the plain path and a cell's id is known before its extension row is built.
        Depths are still written one statement at a time, parents before children,
        which keeps the foreign key satisfied without deferring it.

        The count returned is element rows only. Extension rows are semantics
        *about* those elements rather than elements in their own right, and
        ``extraction_runs.element_count`` has to keep meaning what §11.11's coverage
        arithmetic assumes it means.
        """
        total = count_elements(elements)
        if total == 0:
            return 0

        identifiers = iter(self._allocate_ids(total))
        written = 0
        level: list[tuple[UUID | None, ExtractedElement]] = [
            (None, element) for element in elements
        ]
        table_rows: list[dict[str, Any]] = []
        cell_rows: list[dict[str, Any]] = []

        while level:
            rows: list[dict[str, Any]] = []
            following: list[tuple[UUID | None, ExtractedElement]] = []

            for parent_id, element in level:
                element_id = next(identifiers)
                rows.append(
                    _row_for(
                        run_id=run_id,
                        parent_id=parent_id,
                        element=element,
                        element_id=element_id,
                    )
                )
                extension = _extension_row(element_id, element)
                if extension is not None:
                    target, payload = extension
                    (table_rows if target is SourceTable else cell_rows).append(payload)
                following.extend(
                    (element_id, child) for child in element.children
                )

            written += self._insert(rows)
            level = following

        if table_rows:
            self._session.execute(insert(SourceTable), table_rows)
        if cell_rows:
            self._session.execute(insert(SourceTableCell), cell_rows)

        return written

    def _allocate_ids(self, count: int) -> list[UUID]:
        """Reserve ``count`` time-ordered identifiers in one round trip.

        From the database rather than from Python, because ``uuidv7()`` is what
        gives these keys their index locality and the standard library cannot
        produce them. One query for the whole tree, not one per depth.
        """
        statement = sql_text("SELECT uuidv7() FROM generate_series(1, :count)")
        result = self._session.execute(statement.bindparams(count=count))
        return list(result.scalars())

    def complete_run(
        self,
        *,
        run: ExtractionRun,
        state: ExtractionState,
        element_count: int,
    ) -> None:
        """Record how a run finished and how much it produced.

        ``completed_at`` comes from the **database** clock, not Python's, and that
        is not a stylistic preference. ``started_at`` defaults to the server's
        ``now()``, and ``ck_extraction_runs_completed_after_started`` compares the
        two: sourcing one end from the application process means the constraint
        adjudicates a race between two clocks. Measured on this host, the Python
        process ran between 5.3ms behind and 3.4ms ahead of the PostgreSQL
        container, drifting direction within minutes. A fast run — a malformed PDF
        rejected in under a millisecond, or a two-page filing — finishes inside
        that window, so the row was rejected on skew rather than on anything about
        the run. It presented as an intermittent test failure for two phases.

        ``clock_timestamp()`` rather than ``now()`` because ``now()`` is the
        transaction's start time, which would make every duration exactly zero.
        ``clock_timestamp()`` is real wall-clock time and is always at or after
        the transaction start, so the constraint holds by construction.
        """
        run.state = state.value
        run.element_count = element_count
        run.completed_at = func.clock_timestamp()
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

    def counts_for_run(self, *, run_id: UUID) -> ElementCounts:
        """Summarise a run without loading its elements.

        A five-hundred-page filing yields tens of thousands of rows, and reading
        them all back to count pages would make reporting cost more than the
        extraction it reports on. One query with conditional aggregates instead.
        """
        statement = select(
            func.count(),
            func.count().filter(SourceElement.element_type == ElementType.PAGE.value),
            func.count().filter(SourceElement.element_type == ElementType.BLOCK.value),
            func.count().filter(SourceElement.element_type == ElementType.TABLE.value),
            func.count().filter(SourceElement.element_type == ElementType.CELL.value),
            func.count().filter(SourceElement.failure_reason.is_not(None)),
        ).where(SourceElement.extraction_run_id == run_id)

        total, pages, blocks, tables, cells, gaps = self._session.execute(
            statement
        ).one()
        return ElementCounts(
            total=total,
            pages=pages,
            blocks=blocks,
            tables=tables,
            cells=cells,
            coverage_gaps=gaps,
        )

    def elements_for_run(self, *, run_id: UUID) -> Sequence[SourceElement]:
        """Return a run's elements with roots first, then children by parent.

        Enough to **rebuild** the hierarchy in memory, at any depth, because every
        row carries its ``parent_id``. The PDF path is now three levels — page,
        then blocks and tables, then cells — so the ordering here groups each
        parent's children together rather than yielding document order.

        **It does not yield document order, and that disclaimer now matters.**
        Interleaving a table's cells back into their page's reading sequence is a
        recursive query over ``(extraction_run_id, parent_id, ordinal)``, which the
        covering index supports and this method does not attempt. Chunking needs
        that order (§18.1), so the recursive read belongs with the chunker rather
        than here, where no current caller needs it.
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

    def _insert(self, rows: list[dict[str, Any]]) -> int:
        """Insert rows whose identifiers nobody needs, and report how many.

        The fast path, used for the leaves of the tree. See the module docstring
        for the measurement that makes this worth distinguishing.
        """
        self._session.execute(insert(SourceElement), rows)
        return len(rows)

def _row_for(
    *,
    run_id: UUID,
    parent_id: UUID | None,
    element: ExtractedElement,
    element_id: UUID,
) -> dict[str, Any]:
    """Flatten one element into an insert parameter set.

    ``created_at`` is omitted so the server default applies. ``id`` is supplied,
    because it was allocated before the insert so that extension rows could be
    built against it.
    """
    return {
        "id": element_id,
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


def _extension_row(
    element_id: UUID, element: ExtractedElement
) -> tuple[type[SourceTable] | type[SourceTableCell], dict[str, Any]] | None:
    """Build the semantics row for an element, or None when it has none.

    Returns the target table alongside the payload rather than inspecting the
    element type again at the call site, so adding a format's extension table is
    one branch here instead of a branch in every caller.
    """
    semantics = element.semantics
    if semantics is None:
        return None
    if isinstance(semantics, TableSemantics):
        return SourceTable, {
            "source_element_id": element_id,
            "caption": semantics.caption,
            "verdict": semantics.verdict.value if semantics.verdict else None,
            "verdict_reasons": list(semantics.verdict_reasons),
            "quality_signals": (
                dict(semantics.quality_signals)
                if semantics.quality_signals is not None
                else None
            ),
        }
    return SourceTableCell, {
        "source_element_id": element_id,
        "header_path": list(semantics.header_path),
        "row_label_path": list(semantics.row_label_path),
        "footnote_refs": list(semantics.footnote_refs),
        "is_header": semantics.is_header,
        "units": semantics.units,
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
