"""Turning a derived table into source elements.

The last library-neutral step: a :class:`DerivedTable` becomes an
:class:`ExtractedElement` tree that the repository can persist, with semantics
attached as typed values rather than as text.

**Absent cells do not become elements.** A position the detector reported nothing
for is covered by its neighbour's ``column_span``, which is the honest best
statement available — and since a merge and a detection failure are
indistinguishable from a grid, writing a row for "something might be missing here"
would record a conclusion the evidence does not support. The span is the record.

**A table's extension row exists only when something is known to put in it.** A
table carries its quality verdict, so an assessed table writes a ``source_tables``
row; an unassessed one writes none. Attaching ``TableSemantics(caption=None)`` to
every table regardless would fill the table with NULLs, and a column of NULLs
reads as evidence that filings have no captions rather than as evidence that we
never looked. ``caption`` stays NULL for that reason — §12.6 wants it, no detector
in use reports one, and caption derivation shares its machinery with footnote
binding, since text above a table and text below it are the same geometric
problem. Both arrive together.
"""

from finsight.domain.representations.source import (
    CellLocation,
    CellSemantics,
    ElementType,
    ExtractedElement,
    TableLocation,
    TableSemantics,
)
from finsight.extraction.tables.structure import DerivedCell, DerivedTable
from finsight.extraction.tables.validation import TableQuality


def table_locator(page_number: int, ordinal: int) -> str:
    """The human-readable citation address of a table."""
    return f"p. {page_number}, table {ordinal + 1}"


def cell_locator(page_number: int, table_ordinal: int, cell: DerivedCell) -> str:
    """The human-readable citation address of a cell.

    Row and column are one-based in the address because it is shown to a reader,
    while the stored indices are zero-based because they index a grid. Keeping the
    two conventions apart here means neither leaks into the other.
    """
    return (
        f"{table_locator(page_number, table_ordinal)}, "
        f"R{cell.row_index + 1}C{cell.column_index + 1}"
    )


def to_element(
    derived: DerivedTable,
    *,
    ordinal: int,
    method: str,
    method_version: str,
    failure_reason: str | None = None,
    quality: TableQuality | None = None,
) -> ExtractedElement:
    """Build the table element and its cells.

    ``ordinal`` positions the table among its page's children, so it participates
    in the same reading order as the blocks around it.

    ``failure_reason`` marks a table whose content is known to be incomplete — a
    detector that dropped cells it could not place. Carried on the table rather
    than on a cell because the loss is of positions, not of one value, and because
    §11.11 coverage gaps are what turn a run into ``partial`` instead of letting an
    incomplete statement pass as whole.
    """
    cells = tuple(
        _cell(
            cell,
            page_number=derived.page_number,
            table_ordinal=ordinal,
            cell_ordinal=index,
            method=method,
            method_version=method_version,
        )
        for index, cell in enumerate(
            cell for cell in derived.cells if not cell.is_absent
        )
    )

    return ExtractedElement(
        element_type=ElementType.TABLE,
        ordinal=ordinal,
        locator=table_locator(derived.page_number, ordinal),
        location=TableLocation(bbox=derived.bbox),
        extraction_method=method,
        extraction_method_version=method_version,
        failure_reason=failure_reason,
        children=cells,
        semantics=_semantics(quality),
    )


def _semantics(quality: TableQuality | None) -> TableSemantics | None:
    """Attach the verdict, or nothing at all when none was formed.

    None rather than an empty record: a table with no assessment is different from
    one assessed and found clean, and writing a row either way would make the two
    indistinguishable. Caption stays None until caption derivation exists.
    """
    if quality is None:
        return None
    return TableSemantics(
        caption=None,
        verdict=quality.verdict,
        verdict_reasons=quality.reasons,
        quality_signals={
            "prose_ratio": quality.prose_ratio,
            "numeric_ratio": quality.numeric_ratio,
            "filled_ratio": quality.filled_ratio,
            "unassigned_words": float(quality.unassigned_words),
            "dropped_cells": quality.dropped_cells,
        },
    )


def _cell(
    cell: DerivedCell,
    *,
    page_number: int,
    table_ordinal: int,
    cell_ordinal: int,
    method: str,
    method_version: str,
) -> ExtractedElement:
    assert cell.bbox is not None  # absent cells are filtered before this point
    return ExtractedElement(
        element_type=ElementType.CELL,
        ordinal=cell_ordinal,
        locator=cell_locator(page_number, table_ordinal, cell),
        location=CellLocation(
            bbox=cell.bbox,
            row_index=cell.row_index,
            column_index=cell.column_index,
            row_span=cell.row_span,
            column_span=cell.column_span,
        ),
        extraction_method=method,
        extraction_method_version=method_version,
        text=cell.text,
        semantics=CellSemantics(
            header_path=cell.header_path,
            row_label_path=cell.row_label_path,
            footnote_refs=cell.footnote_refs,
            is_header=cell.is_header,
            units=cell.units,
        ),
    )
