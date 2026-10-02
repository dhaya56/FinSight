"""The table-detection boundary.

A detector reports the grid it found and nothing more. It does not decide which
rows are headers, which row carries units, which cells are merged, or what a
footnote marker refers to — those are derived in :mod:`structure`, from the grid
alone, by code that imports no PDF library and can therefore be tested without a
document.

That split is not tidiness. Probing PyMuPDF against a ruled financial table
established that a detector supplies almost none of the semantics this project
needs:

* ``header_rows`` came back as **0** for a table with two header rows, so header
  identification is unavailable rather than merely imperfect;
* merged cells are reported as a *missing* cell, never as a span count, so
  ``row_span`` and ``column_span`` have to be inferred;
* a units row (``(Rs in crore)``) arrived as an ordinary data row with no marking;
* a footnote marker stayed inside its cell's text, preserved but not separated.

Any other detector will be differently incomplete. Deriving structure above the
boundary means a second strategy inherits the same semantics for free, and means
a bug in span inference is one unit test rather than one per library.

**Two strategies are complementary, not competing.** On the same table, PyMuPDF's
``lines`` strategy read it correctly and its borderless copy **not at all** — zero
tables, a total miss rather than a degraded read — while ``text`` found the
borderless copy but reported 13 rows for 7 and clipped the right edge off the
table's region. So this contract returns candidates *per strategy* and leaves
selection to a recorded decision (§12.12), which §12.9's routing signals allow to
be a routing rule rather than a single winner. Those observations come from
synthetic fixtures and are evidence about plumbing, not about real filings
(CLAUDE.md §13).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Protocol, runtime_checkable

from finsight.extraction.contracts import ExtractionError


class TableDetectionError(ExtractionError):
    """A detector reported a grid that cannot be a table.

    Messages describe the shape problem and never the cell contents, so a
    malformed table cannot leak filing values into a log (CLAUDE.md §10).
    """


@dataclass(frozen=True, slots=True)
class DetectedCell:
    """One grid position as a detector reported it.

    ``bbox`` is ``None`` when the detector reported **no cell** at this position.
    That is the only signal a merged cell gives: a spanning header covering two
    period columns arrives as one populated cell followed by an absent one. It is
    kept as ``None`` rather than collapsed into an empty cell, because the two
    mean opposite things — an empty cell is a blank the document contains, and an
    absent cell is a position the document does not have. Conflating them is how
    a value ends up attributed to the wrong period.
    """

    row_index: int
    column_index: int
    text: str
    bbox: tuple[float, float, float, float] | None
    text_left: float | None = None
    """Where the cell's text actually begins, in displayed space.

    Reported separately from ``bbox`` because in a ruled table they are different
    measurements and only this one carries indentation. A cell's box is the *grid* —
    every first-column cell shares the ruling line's left edge — while the text
    inside it may sit further right to express hierarchy. Measured on this
    project's fixture, three first-column cells all had ``bbox`` left edge 60.0
    while their text began at 64.0, 72.0 and 64.0, and the 72.0 is the only record
    that "Of which: term deposits" is a component of the line above rather than a
    peer of it.

    ``None`` when the cell holds no text, or when a detector cannot report it. The
    derivation treats that as top level rather than guessing.
    """

    def __post_init__(self) -> None:
        if self.row_index < 0 or self.column_index < 0:
            raise TableDetectionError("cell indices must not be negative")

    @property
    def is_absent(self) -> bool:
        """True when the detector reported no cell here, not an empty one."""
        return self.bbox is None


@dataclass(frozen=True, slots=True)
class DetectedTable:
    """A candidate table: its region, its grid shape, and its cells.

    Rectangular by construction — ``row_count * column_count`` positions, each
    appearing exactly once — because a ragged grid has no meaningful column, and a
    column is what binds a number to its period.
    """

    page_number: int
    bbox: tuple[float, float, float, float]
    row_count: int
    column_count: int
    cells: tuple[DetectedCell, ...]

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise TableDetectionError("page_number is one-based")
        if self.row_count < 1 or self.column_count < 1:
            raise TableDetectionError("a table has at least one row and one column")
        expected = self.row_count * self.column_count
        if len(self.cells) != expected:
            raise TableDetectionError(
                f"expected {expected} grid positions, got {len(self.cells)}"
            )
        seen = {(cell.row_index, cell.column_index) for cell in self.cells}
        if len(seen) != expected:
            raise TableDetectionError("grid positions must be unique")
        for cell in self.cells:
            if cell.row_index >= self.row_count:
                raise TableDetectionError("a cell sits below the last row")
            if cell.column_index >= self.column_count:
                raise TableDetectionError("a cell sits beyond the last column")

    def cell_at(self, row_index: int, column_index: int) -> DetectedCell:
        """The cell at a grid position."""
        for cell in self.cells:
            if cell.row_index == row_index and cell.column_index == column_index:
                return cell
        raise TableDetectionError("no cell at that grid position")

    def row(self, row_index: int) -> tuple[DetectedCell, ...]:
        """One row, ordered left to right."""
        return tuple(
            sorted(
                (cell for cell in self.cells if cell.row_index == row_index),
                key=lambda cell: cell.column_index,
            )
        )


@runtime_checkable
class TableDetector(Protocol):
    """Finds candidate tables in a PDF and reports their grids.

    Stateless with respect to a document: the same bytes yield the same tables,
    since a recorded comparison has to be reproducible from the strategy name and
    version alone.
    """

    @property
    def strategy(self) -> str:
        """Which strategy this detector applies, recorded in comparisons.

        Separate from ``method``: one library may offer several strategies whose
        results differ enough to warrant separate decisions, which is exactly the
        case that made this contract necessary.
        """
        ...

    @property
    def method(self) -> str:
        """The library, recorded as the produced elements' ``extraction_method``."""
        ...

    @property
    def method_version(self) -> str:
        """The library's version, recorded per element (§16.5)."""
        ...

    def detect(self, source: IO[bytes]) -> Mapping[int, Sequence[DetectedTable]]:
        """Return the candidate tables on each page that has any.

        Keyed by one-based page number, and pages with no tables are absent rather
        than present-and-empty, so a caller cannot mistake "none found" for "not
        examined". Takes the whole document because opening it once per detector is
        cheaper than per page, and because a detector backed by another library
        may need the file rather than a page object.

        Raises:
            DocumentUnreadableError: the document could not be opened.
        """
        ...
