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

    **The ``reported_*`` fields carry structure a detector states outright**, as
    opposed to structure derived from the grid. That is the difference between a
    parser that hands over a picture of a table and one that hands over the table.
    PyMuPDF reports neither spans nor headers, so both must be inferred — and span
    inference cannot distinguish a merged cell from a cell the detector failed to
    find, an ambiguity measured across **51% of real tables**. Docling reports
    ``col_span``, ``row_span`` and ``column_header`` explicitly, removing that
    error class rather than shrinking it.

    ``None`` on a reported field means *not reported*, and the derivation falls
    back to inference. It never means "no span" — that is ``1``. Conflating the
    two would turn silence into a claim.
    """

    row_index: int
    column_index: int
    text: str
    bbox: tuple[float, float, float, float] | None
    text_left: float | None = None
    reported_row_span: int | None = None
    reported_column_span: int | None = None
    reported_is_header: bool | None = None
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
        for name in ("reported_row_span", "reported_column_span"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise TableDetectionError(f"{name} must be at least 1")

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
    dropped_cells: float = 0.0
    """Cells the detector discarded rather than placed, where it admits to it.

    Docling drops cells matching neither a row nor a column band and reports the
    loss only through a log warning — measured at 4 of 35 on one real table, 11%
    of a financial table gone with nothing in the return value to show it. Carried
    here so such a table can be recorded as a coverage gap (§11.11) instead of
    being presented as complete.

    Fractional because the warning does not name its table, so a document's loss is
    shared across its tables. Over-reporting a gap is safe; under-reporting it is
    the failure this exists to prevent.
    """

    unassigned_words: int = 0
    """Words inside the table's region that reached no cell.

    Text conservation, measured per word rather than by comparing a clipped region
    against joined cell text — that cruder form scores a *generous* bounding box as
    data loss, which produced alarming and wrong figures when first tried.

    A handful is normal: a caption or a units note sits inside a table's box
    without belonging to any cell, which is correct behaviour. A large count means
    the grid failed to cover its own content, and is the signal the validation gate
    uses to refuse a table.
    """

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
