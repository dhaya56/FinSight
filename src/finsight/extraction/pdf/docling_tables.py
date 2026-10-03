"""The Docling table detector.

Chosen for tables on measured evidence, not preference. Across ten pages reviewed
by hand it matched the human table count on 8, against 6 for the best PyMuPDF
configuration and 5 for the default — and more importantly it **segments
multi-table pages**. On a page holding four tables, PyMuPDF returned one 24-by-11
grid with a paragraph sliced across eleven columns; Docling returned four separate
tables. It also reports spans and header flags outright, which removes an
ambiguity no amount of inference can resolve.

PyMuPDF keeps the rest. Pages, blocks, narrative text and geometry still come from
it, because it is correct and roughly an order of magnitude faster. This is the
per-page routing §12.9 describes, and the schema already supports it: every
element records its own ``extraction_method``, so a run may legitimately mix
producers.

**Rotation is already applied, and must not be applied twice.** Docling reports
geometry in displayed space. Verified on three ``/Rotate 90`` pages whose MediaBox
is 612x792 portrait while both engines report 792x612: all 533 cells on those pages
fall inside the page rectangle PyMuPDF reports, as do all 207 on two unrotated
controls. Passing these boxes through the PyMuPDF rotation transform would rotate
them a second time and recreate exactly the defect that put 2,401 block boxes
outside their own page in Phase 5.

**Docling discards cells it cannot place**, and says so only in a log warning —
"N of M pdf cells matched neither a row nor a column band ... and were dropped".
Measured at 4 of 35 cells on one real table, which is 11% of a financial table
silently gone. Those warnings are captured here and surfaced as a count, so a
table that lost cells can be recorded as a coverage gap (§11.11) rather than
presented as complete.
"""

import logging
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import IO, Any, Final

import pymupdf

from finsight.extraction.contracts import DocumentUnreadableError
from finsight.extraction.pdf.geometry import displayed_bbox
from finsight.extraction.tables.contracts import DetectedCell, DetectedTable

Word = tuple[float, float, float, float, str]
"""A word and its box in displayed space: ``(x0, y0, x1, y1, text)``."""

_DROPPED: Final = re.compile(
    r"(\d+)\s+of\s+(\d+)\s+pdf cells matched neither a row nor a column band"
)

_log: Final = logging.getLogger(__name__)

STRATEGY: Final = "docling-tableformer"
"""Recorded as the detector strategy, distinguishing it from PyMuPDF's."""


class _DroppedCellCollector(logging.Handler):
    """Captures Docling's dropped-cell warnings for the duration of one parse.

    Docling reports this loss through the logging module and nowhere in its return
    value, so without intercepting it a table missing 11% of its cells is
    indistinguishable from a complete one.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.dropped = 0
        self.events = 0

    def emit(self, record: logging.LogRecord) -> None:
        match = _DROPPED.search(record.getMessage())
        if match:
            self.dropped += int(match.group(1))
            self.events += 1


@contextmanager
def _collect_dropped_cells() -> Iterator[_DroppedCellCollector]:
    collector = _DroppedCellCollector()
    root = logging.getLogger()
    root.addHandler(collector)
    try:
        yield collector
    finally:
        root.removeHandler(collector)


class DoclingTableDetector:
    """Detects tables with Docling's layout and TableFormer models.

    OCR is disabled deliberately. §12.11 defers OCR, the corpus contains no scanned
    pages across 1,403, and Docling's default pipeline otherwise initialises an OCR
    model and fetches it from a third-party host at parse time — which §20.6
    forbids and the restricted parser worker (§11.7) would refuse outright.
    """

    METHOD: Final = "docling"

    def __init__(self, *, artifacts_path: str | None = None) -> None:
        self._artifacts_path = artifacts_path
        self._converter: Any = None
        self._lock = threading.Lock()

    @property
    def strategy(self) -> str:
        return STRATEGY

    @property
    def method(self) -> str:
        return self.METHOD

    @property
    def method_version(self) -> str:
        import docling

        return str(getattr(docling, "__version__", "unknown"))

    def _build(self) -> Any:
        """Construct the converter once, lazily.

        Model loading is expensive and the import pulls torch, so neither happens
        until a document is actually parsed. Guarded by a lock because the
        converter is not safe to build twice concurrently.
        """
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions()
        options.do_ocr = False
        if self._artifacts_path is not None:
            options.artifacts_path = self._artifacts_path
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )

    @property
    def converter(self) -> Any:
        with self._lock:
            if self._converter is None:
                self._converter = self._build()
            return self._converter

    def detect(
        self,
        source: IO[bytes],
        pages: Sequence[int] | None = None,
    ) -> Mapping[int, Sequence[DetectedTable]]:
        """Return the candidate tables on each page that has any.

        ``pages`` restricts the work to those one-based page numbers. This is not
        a convenience: Docling runs at roughly 1.7s on a page with no table and up
        to 16s on a dense one, so a 590-page filing is most of an hour. §12.9
        routes per page for exactly this reason, and without page selection there
        is no way to send only the difficult pages here and leave the rest on the
        fast path.

        Contiguous runs are converted together so the model is not reloaded per
        page, and the converter itself is built once and reused.

        Raises:
            DocumentUnreadableError: the document could not be converted.
        """
        data = source.read()
        ranges = _contiguous(pages) if pages else [None]

        by_page: dict[int, list[DetectedTable]] = {}
        unplaced: dict[int, int] = {}
        with pymupdf.open(stream=data, filetype="pdf") as native:
            for page_range in ranges:
                document, dropped_total = self._convert(data, page_range)
                tables = list(getattr(document, "tables", []))
                # The warning does not name its table, so the loss is shared
                # evenly rather than attributed to one. Over-reporting a gap is
                # safe; under-reporting it is the failure this exists to prevent.
                share = dropped_total / len(tables) if tables else 0.0

                for table in tables:
                    page_number = _page_of(table)
                    if page_number is None:
                        # A table Docling cannot place on a page cannot be cited,
                        # so it is counted rather than discarded in silence.
                        unplaced[0] = unplaced.get(0, 0) + 1
                        continue
                    words = _words_of(native, page_number)
                    converted = _table(
                        table, page_number=page_number, dropped=share, words=words
                    )
                    if converted is None:
                        unplaced[page_number] = unplaced.get(page_number, 0) + 1
                        continue
                    by_page.setdefault(page_number, []).append(converted)

        if unplaced:
            _log.warning(
                "docling returned %d table(s) that could not be converted: %s",
                sum(unplaced.values()),
                unplaced,
            )
        return by_page

    def _convert(
        self, data: bytes, page_range: tuple[int, int] | None
    ) -> tuple[Any, int]:
        from docling.datamodel.base_models import DocumentStream

        stream = DocumentStream(name="document.pdf", stream=_as_buffer(data))
        with _collect_dropped_cells() as collector:
            try:
                if page_range is None:
                    result = self.converter.convert(stream)
                else:
                    result = self.converter.convert(stream, page_range=page_range)
            except Exception as error:
                raise DocumentUnreadableError(
                    "the document could not be parsed"
                ) from error
            return result.document, collector.dropped


def _words_of(document: pymupdf.Document, page_number: int) -> list[Word]:
    """Every word on a page, in **displayed** space.

    The transform is not optional. PyMuPDF reports words in unrotated page space
    while Docling reports cells in displayed space, so on the 78 ``/Rotate 90``
    pages in this corpus an untransformed word would never fall inside any cell —
    and the failure would be silent, producing tables of empty cells rather than
    an error.
    """
    page = document[page_number - 1]
    matrix = page.rotation_matrix
    out: list[Word] = []
    for x0, y0, x1, y1, text, *_rest in page.get_text("words"):
        if not str(text).strip():
            continue
        box = displayed_bbox(x0, y0, x1, y1, matrix)
        out.append((box[0], box[1], box[2], box[3], str(text)))
    return out


def _contains(box: tuple[float, float, float, float], word: Word) -> bool:
    """True when a word's centre falls inside a box.

    Centre rather than full containment: a word overhanging a cell edge by a
    fraction of a point still belongs to the cell it sits in.
    """
    cx = (word[0] + word[2]) / 2
    cy = (word[1] + word[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _area(box: tuple[float, float, float, float]) -> float:
    return abs((box[2] - box[0]) * (box[3] - box[1]))


def _assign_words(
    boxes: dict[tuple[int, int], tuple[float, float, float, float]],
    words: list[Word],
) -> tuple[dict[tuple[int, int], list[Word]], int]:
    """Give every word to exactly one cell, and count those that fit none.

    Each word goes to the **smallest** box containing it, because Docling reports
    a cell's box as a text extent on some tables and as a full cell boundary on
    others. Where boundaries overlap, the tighter one is the better claim, and
    assigning to one cell only is what stops a value being counted twice.

    The unassigned count is a genuine quality signal rather than bookkeeping: words
    inside a table's region that reached no cell are text the table lost.
    """
    assigned: dict[tuple[int, int], list[Word]] = {}
    unassigned = 0
    for word in words:
        best: tuple[int, int] | None = None
        best_area = float("inf")
        for key, box in boxes.items():
            if _contains(box, word):
                area = _area(box)
                if area < best_area:
                    best, best_area = key, area
        if best is None:
            unassigned += 1
        else:
            assigned.setdefault(best, []).append(word)
    return assigned, unassigned


def _joined(words: list[Word]) -> str:
    """Join a cell's words in reading order, left to right within each line."""
    ordered = sorted(words, key=lambda w: (round(w[1], 1), w[0]))
    return " ".join(w[4] for w in ordered)


def _contiguous(pages: Sequence[int]) -> list[tuple[int, int]]:
    """Group page numbers into contiguous runs, so each is one conversion.

    Converting ``(min, max)`` over a scattered selection would process every page
    between them. On a selection of ten pages spread through three documents that
    was 357 pages of work instead of 10.
    """
    runs: list[tuple[int, int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], page)
        else:
            runs.append((page, page))
    return runs


def _as_buffer(data: bytes) -> Any:
    import io

    return io.BytesIO(data)


def _page_of(table: Any) -> int | None:
    prov = getattr(table, "prov", None)
    if not prov:
        return None
    page_no = getattr(prov[0], "page_no", None)
    return int(page_no) if page_no else None


def _table(
    table: Any,
    *,
    page_number: int,
    dropped: float,
    words: list[Word],
) -> DetectedTable | None:
    """Convert one Docling table: its structure, but the document's own text.

    **Structure from Docling, text from PyMuPDF.** Docling segments pages and
    reports spans and header flags that nothing else here can derive — but it also
    rewrites characters. Measured on one page, the document's eight U+2013 EN
    DASHes came back as ASCII hyphens and both U+20B9 RUPEE SIGNs vanished, leaving
    no non-ASCII character at all in any cell. In a financial table the en dash is
    the nil marker, so normalising it to a hyphen destroys the difference between
    "no such item" and a minus sign, and §14.9 citations are offsets into stored
    text that must therefore be the document's.

    So each cell's text is rebuilt from the words PyMuPDF reports inside that
    cell's box. The cost is negligible: word extraction runs at roughly 350
    pages/s against Docling's ~0.07, so this is under 1% of a page already paid
    for.

    Docling omits empty positions entirely. The grid is rebuilt from the declared
    row and column counts: a position covered by a span is **absent** (a merge), a
    position covered by nothing is **empty** (a blank the document contains). That
    separation is only sound because spans are explicit, and getting it backwards
    attributes a value to the wrong period.
    """
    data = table.data
    rows = int(getattr(data, "num_rows", 0) or 0)
    columns = int(getattr(data, "num_cols", 0) or 0)
    if rows < 1 or columns < 1:
        return None

    reported: dict[tuple[int, int], Any] = {}
    boxes: dict[tuple[int, int], tuple[float, float, float, float]] = {}
    covered: set[tuple[int, int]] = set()
    out_of_grid = 0

    for cell in getattr(data, "table_cells", []):
        start_row = int(getattr(cell, "start_row_offset_idx", 0) or 0)
        start_col = int(getattr(cell, "start_col_offset_idx", 0) or 0)
        if start_row >= rows or start_col >= columns:
            # A cell outside the grid Docling itself declared. Counted, because
            # dropping it quietly is how a value disappears.
            out_of_grid += 1
            continue
        row_span = max(1, int(getattr(cell, "row_span", 1) or 1))
        column_span = max(1, int(getattr(cell, "col_span", 1) or 1))
        box = getattr(cell, "bbox", None)
        if box is not None:
            left, right = sorted((float(box.l), float(box.r)))
            top, bottom = sorted((float(box.t), float(box.b)))
            boxes[(start_row, start_col)] = (left, top, right, bottom)
        reported[(start_row, start_col)] = (cell, row_span, column_span)
        for r in range(start_row, min(start_row + row_span, rows)):
            for c in range(start_col, min(start_col + column_span, columns)):
                covered.add((r, c))

    assigned, unassigned = _assign_words(boxes, words)
    row_bounds, column_bounds = _grid_bounds(boxes, rows, columns)

    cells: list[DetectedCell] = []
    for r in range(rows):
        for c in range(columns):
            key = (r, c)
            entry = reported.get(key)
            if entry is not None:
                _cell, row_span, column_span = entry
                found = assigned.get(key, [])
                cells.append(
                    DetectedCell(
                        row_index=r,
                        column_index=c,
                        # Verbatim where the document supplies it; Docling's own
                        # text only where no word could be matched, so a cell is
                        # never silently emptied.
                        text=_joined(found) if found else str(
                            getattr(_cell, "text", "") or ""
                        ),
                        bbox=boxes.get(key),
                        text_left=min((w[0] for w in found), default=None),
                        reported_row_span=row_span,
                        reported_column_span=column_span,
                        reported_is_header=bool(
                            getattr(_cell, "column_header", False)
                        ),
                    )
                )
            elif key in covered:
                cells.append(
                    DetectedCell(row_index=r, column_index=c, text="", bbox=None)
                )
            else:
                cells.append(
                    DetectedCell(
                        row_index=r,
                        column_index=c,
                        text="",
                        bbox=_empty_box(r, c, row_bounds, column_bounds),
                        reported_row_span=1,
                        reported_column_span=1,
                    )
                )

    return DetectedTable(
        page_number=page_number,
        bbox=_bbox_of(table),
        row_count=rows,
        column_count=columns,
        cells=tuple(cells),
        dropped_cells=dropped + out_of_grid,
        unassigned_words=unassigned,
    )


def _grid_bounds(
    boxes: dict[tuple[int, int], tuple[float, float, float, float]],
    rows: int,
    columns: int,
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[float, float]]]:
    """Vertical extent of each row and horizontal extent of each column.

    Derived from the cells that do have geometry, so a position Docling omitted
    can be given the box it would occupy. Without this an empty cell has no
    coordinates at all, and a placeholder at the page origin would put 20% of a
    large table's cells — 56 of 280 on the biggest in this corpus — somewhere they
    are not.
    """
    row_bounds: dict[int, tuple[float, float]] = {}
    column_bounds: dict[int, tuple[float, float]] = {}
    for (r, c), (x0, y0, x1, y1) in boxes.items():
        if r < rows:
            lo, hi = row_bounds.get(r, (y0, y1))
            row_bounds[r] = (min(lo, y0), max(hi, y1))
        if c < columns:
            lo, hi = column_bounds.get(c, (x0, x1))
            column_bounds[c] = (min(lo, x0), max(hi, x1))
    return row_bounds, column_bounds


def _empty_box(
    row: int,
    column: int,
    row_bounds: dict[int, tuple[float, float]],
    column_bounds: dict[int, tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    """The box an omitted position occupies, or None when it cannot be placed.

    ``None`` here would mean *absent*, which is why a position whose row or column
    extent is unknown is left without geometry rather than guessed: an invented
    coordinate is worse than a missing one, because it resolves.
    """
    vertical = row_bounds.get(row)
    horizontal = column_bounds.get(column)
    if vertical is None or horizontal is None:
        return None
    return (horizontal[0], vertical[0], horizontal[1], vertical[1])


_EMPTY_BOX: Final[tuple[float, float, float, float]] = (0.0, 0.0, 0.0, 0.0)
"""Placeholder geometry for a position Docling omitted as empty.

A degenerate box rather than ``None``, because ``None`` means *absent* — spanned
over or unresolved — and an omitted position with no span covering it is a blank
the document contains. The distinction decides whether a value belongs to the
period beside it or the one after.
"""


def _bbox_of(table: Any) -> tuple[float, float, float, float]:
    prov = getattr(table, "prov", None)
    if prov:
        box = getattr(prov[0], "bbox", None)
        if box is not None:
            left, right = sorted((float(box.l), float(box.r)))
            top, bottom = sorted((float(box.t), float(box.b)))
            return (left, top, right, bottom)
    return _EMPTY_BOX
