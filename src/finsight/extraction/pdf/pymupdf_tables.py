"""PyMuPDF table detectors, one per strategy.

Two instances of one class rather than two classes: the strategies differ only in
the string handed to ``find_tables``, and their outputs differ enormously. Keeping
them as configuration makes the comparison in §12.12 a loop over registered
detectors instead of a branch.

**What this module does not do.** It reports the grid and stops. Header rows,
units rows, spans and footnote markers are derived in
:mod:`finsight.extraction.tables.structure`, because PyMuPDF supplies none of
them — see that module and the detection contract for the measurements behind
that split.

**Rotation applies here too.** ``find_tables`` reports regions and cells in the
unrotated page space, exactly as ``get_text("blocks")`` does. The defect that cost
78 pages their citation coordinates is available again at cell level, so every box
goes through :func:`displayed_bbox` on the way out, and a test asserts cells stay
inside their page across all four rotations.
"""

from collections.abc import Mapping, Sequence
from typing import IO, Final

import pymupdf

from finsight.extraction.contracts import DocumentUnreadableError
from finsight.extraction.pdf.geometry import displayed_bbox
from finsight.extraction.tables.contracts import DetectedCell, DetectedTable

STRATEGY_LINES: Final = "lines"
"""Ruling lines define the grid.

Precise where a table is ruled, and blind where it is not: on a borderless copy of
a table it read correctly when ruled, this strategy found **zero** tables. A total
miss is harder to notice than a bad read, which is why the borderless fixture
exists.
"""

STRATEGY_TEXT: Final = "text"
"""Whitespace alignment defines the grid.

Finds borderless tables, at the cost of over-segmentation — 13 rows for a 7-row
table, a phantom empty row between every real one — and of clipping the table's
region, which loses a column from the *area* even when the text still arrives.
"""

STRATEGY_HYBRID: Final = "hybrid"
"""Rows from ruling lines, columns from text alignment.

The configuration this document class actually calls for, and the one never tried
until the defaults had already been judged. Financial statements here are ruled
*horizontally only* — row separators with columns set by alignment — so asking for
rows from lines and columns from text matches how they are typeset.

Measured against ten hand-reviewed pages it beat both defaults: 6 of 10 exact
table counts against 5 for ``lines`` and 2 for ``text``, count error 7 against 12
and 11, and zero false positives on the four pages holding no table. It also
bounds a single-table page correctly, excluding the prose above it, where ``text``
swallowed the whole page.

Two costs keep it from being the default. It still merges several tables on one
page into a single grid. And on a **fully ruled** table — horizontal *and* vertical
rules — it is worse than ``lines``: on this project's fixture it returned 15 cells
against 20 and lost the spanning period header, resolving a value to ``('2025',)``
where ``lines`` gives ``('Year ended March 31', '2025')``.

So neither dominates: ``lines`` is better where a complete grid is drawn, the
hybrid is better where only row separators are. That is a routing decision with no
free answer, which is part of why the production detector is neither of them.
"""

STRATEGIES: Final[tuple[str, ...]] = (STRATEGY_LINES, STRATEGY_TEXT, STRATEGY_HYBRID)
"""Every strategy registered. Selection is ADR-003's, not this module's."""

_STRATEGY_ARGUMENTS: Final[dict[str, dict[str, str]]] = {
    STRATEGY_LINES: {"strategy": STRATEGY_LINES},
    STRATEGY_TEXT: {"strategy": STRATEGY_TEXT},
    STRATEGY_HYBRID: {
        "horizontal_strategy": "lines_strict",
        "vertical_strategy": "text",
    },
}


class PyMuPdfTableDetector:
    """Detects candidate tables with one of PyMuPDF's strategies."""

    METHOD: Final = "pymupdf"

    def __init__(self, strategy: str = STRATEGY_LINES) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(f"unknown strategy: {strategy!r}")
        self._strategy = strategy

    @property
    def strategy(self) -> str:
        return self._strategy

    @property
    def method(self) -> str:
        return self.METHOD

    @property
    def method_version(self) -> str:
        return str(pymupdf.__version__)

    def detect(self, source: IO[bytes]) -> Mapping[int, Sequence[DetectedTable]]:
        """Find candidate tables on every page, keyed by one-based page number.

        Raises:
            DocumentUnreadableError: the document is encrypted or unparsable.
        """
        data = source.read()
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except (RuntimeError, ValueError) as error:
            raise DocumentUnreadableError("the document could not be parsed") from error

        found: dict[int, Sequence[DetectedTable]] = {}
        with document:
            if document.needs_pass:
                raise DocumentUnreadableError("the document is password protected")
            for index in range(document.page_count):
                tables = self.tables_on_page(document[index], page_number=index + 1)
                if tables:
                    found[index + 1] = tables
        return found

    def tables_on_page(
        self, page: pymupdf.Page, *, page_number: int
    ) -> tuple[DetectedTable, ...]:
        """Detect tables on an already-open page.

        The in-process path, for a producer that has the page open and would
        otherwise parse the document a second time. :meth:`detect` remains the
        neutral contract that the comparison harness and any other library's
        detector implement; this one takes a PyMuPDF page and therefore stays
        inside the PDF package.
        """
        tables = page.find_tables(**_STRATEGY_ARGUMENTS[self._strategy]).tables
        if not tables:
            return ()
        matrix = page.rotation_matrix
        spans = _text_span_boxes(page, matrix)
        return tuple(
            self._table(table, page_number=page_number, matrix=matrix, spans=spans)
            for table in tables
        )

    def _table(
        self,
        table: pymupdf.table.Table,
        *,
        page_number: int,
        matrix: pymupdf.Matrix,
        spans: tuple[tuple[float, float, float, float], ...],
    ) -> DetectedTable:
        rows = table.extract()
        cells: list[DetectedCell] = []

        for row_index, row in enumerate(table.rows):
            texts = rows[row_index] if row_index < len(rows) else []
            for column_index in range(table.col_count):
                box = row.cells[column_index] if column_index < len(row.cells) else None
                raw = texts[column_index] if column_index < len(texts) else None
                placed = (
                    None
                    if box is None
                    else displayed_bbox(box[0], box[1], box[2], box[3], matrix)
                )
                cells.append(
                    DetectedCell(
                        row_index=row_index,
                        column_index=column_index,
                        text="" if raw is None else str(raw),
                        bbox=placed,
                        text_left=None if placed is None else _text_left(placed, spans),
                    )
                )

        return DetectedTable(
            page_number=page_number,
            bbox=displayed_bbox(
                table.bbox[0], table.bbox[1], table.bbox[2], table.bbox[3], matrix
            ),
            row_count=len(table.rows),
            column_count=table.col_count,
            cells=tuple(cells),
        )


def _text_span_boxes(
    page: pymupdf.Page, matrix: pymupdf.Matrix
) -> tuple[tuple[float, float, float, float], ...]:
    """Every text span's box on the page, in displayed space.

    Gathered once per page rather than per cell. The rotation transform applies
    here for the same reason it applies everywhere else: a span box compared
    against a cell box in a different coordinate space matches nothing on a rotated
    page, which would silently disable indentation detection on exactly the pages
    most likely to hold a wide financial table.
    """
    boxes: list[tuple[float, float, float, float]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                if not str(span["text"]).strip():
                    continue
                box = span["bbox"]
                boxes.append(displayed_bbox(box[0], box[1], box[2], box[3], matrix))
    return tuple(boxes)


def _text_left(
    cell: tuple[float, float, float, float],
    spans: tuple[tuple[float, float, float, float], ...],
) -> float | None:
    """The leftmost text within a cell, or None when the cell holds none.

    Matched by span centre rather than full containment, because a span that
    overhangs a ruling line by a fraction of a point still belongs to the cell it
    sits in.
    """
    x0, y0, x1, y1 = cell
    lefts = [
        span[0]
        for span in spans
        if x0 <= (span[0] + span[2]) / 2 <= x1 and y0 <= (span[1] + span[3]) / 2 <= y1
    ]
    return min(lefts) if lefts else None
