"""Deriving table structure from a detected grid.

Four things every detector probed so far declines to supply — merged-cell spans,
which rows are headers, which row states the units, and which trailing characters
are footnote markers — and all four decide whether a number keeps its meaning.
This module derives them from the grid alone and imports no PDF library, so each
is a unit test over a small list of cells rather than a test over a document.

**Nothing here is interpreted.** A units row is *recorded verbatim* as the text
the document showed; turning "Rs in crore" into a scale factor of 10^7 is §16's
work. A header path records the text a header cell held; deciding that "2025"
denotes a fiscal year is §16's too. And a footnote marker is recorded *alongside*
the cell's text, never removed from it — ``text`` stays byte-for-byte what the
detector read, because citations are character offsets into that exact string
(§14.9). Separating the marker is additive metadata, not a rewrite.

**The heuristics are candidates, not truth.** Header-row identification in
particular is a judgement: period columns are numbers, so "the first row with
numbers" is not the boundary. The rule used here is that a *data* row has both a
row label and a value, and everything above the first data row is preamble, split
into header rows and units rows. It reads this project's fixtures correctly and
will not read every filing correctly. Which is the point of the ground truth —
these derivations are what it measures, and §12.12 admits nothing on the strength
of a heuristic that has only been tried on synthetic tables.
"""

import re
from dataclasses import dataclass
from typing import Final

from finsight.extraction.tables.contracts import DetectedCell, DetectedTable

_SCALE_WORDS: Final[frozenset[str]] = frozenset(
    {"crore", "crores", "lakh", "lakhs", "million", "millions", "billion", "billions",
     "thousand", "thousands"}
)
"""Scale vocabulary, including the Indian units the corpus actually uses."""

_CURRENCY_TOKENS: Final[frozenset[str]] = frozenset(
    {"rs", "rs.", "inr", "usd", "eur", "gbp", "₹", "$", "€", "£"}
)

_NUMERIC_RESIDUE: Final = re.compile("[\\s,.()%+\\- −–]")  # noqa: RUF001
"""Characters a presented numeral may carry around its digits.

The homoglyphs in that class are deliberate, and the suppressed lint is the point
of the rule being suppressed: it exists *because* filings use a non-breaking space
as a group separator and a Unicode minus or en dash where a hyphen is expected.
Replacing them with the ASCII lookalikes RUF001 suggests would stop this matching
the very numerals it was written for.
"""

_TRAILING_MARKER: Final = re.compile(
    r"(?P<body>\S.*?)\s*(?P<marker>\((?P<paren>[A-Za-z0-9]{1,3})\)|[*†‡#]+)$"
)
"""A footnote marker at the end of a cell, with text before it.

The "text before it" requirement is load-bearing. ``(45)`` and ``(1,234)`` are
*negative values*, and a pattern that matched a trailing parenthesis without
requiring a body would read the parentheses as a marker and quietly destroy the
sign — the single worst defect available in a financial table.

**The symbol set is measured, not assumed.** A first version of this pattern was
written from guesswork and missed ``#`` entirely. Counting marker forms across the
1,403-page development split found, as trailing markers: parenthesised digits 937,
parenthesised letters 450, ``*`` 361, ``#`` 56, ``**`` 39, ``***`` 6, ``##`` 1 and
``###`` 1. No superscript digits appeared anywhere in the corpus, and neither did
``†`` or ``‡`` — those two are kept because other filings use them and they cost
nothing, but their presence here is zero and should not be read as validated.
"""


def looks_numeric(text: str) -> bool:
    """True when the text presents as a number, however it is decorated.

    Deliberately permissive about presentation and strict about content: strip the
    separators, signs, percent and parentheses a filing may wrap a numeral in, and
    what remains must be digits and nothing else.
    """
    stripped = text.strip()
    if not stripped:
        return False
    for token in _CURRENCY_TOKENS:
        stripped = stripped.replace(token, "").replace(token.upper(), "")
    residue = _NUMERIC_RESIDUE.sub("", stripped)
    return residue.isdigit()


def states_units(text: str) -> bool:
    """True when the text announces a scale or currency rather than a value.

    Matches "(Rs in crore)", "INR millions", "₹ in lakhs". Requires a scale word or
    a currency token *and* no digits, so "1,234 crore" — a value that happens to
    name its scale — is not mistaken for a units row.
    """
    lowered = text.strip().lower()
    if not lowered or any(character.isdigit() for character in lowered):
        return False
    words = set(re.findall(r"[^\s()\[\],]+", lowered))
    return bool(words & _SCALE_WORDS) or bool(words & _CURRENCY_TOKENS)


def split_footnote_marker(text: str) -> tuple[str, tuple[str, ...]]:
    """Return the cell's body and any trailing footnote markers.

    The returned body is *not* what gets stored as the element's text — the
    verbatim string is. The body exists so a row label can be compared and matched
    without its marker.
    """
    match = _TRAILING_MARKER.match(text.strip())
    if match is None:
        return text.strip(), ()
    paren = match.group("paren")
    marker = paren if paren is not None else match.group("marker")
    return match.group("body").strip(), (marker,)


@dataclass(frozen=True, slots=True)
class DerivedCell:
    """One cell with its structure resolved.

    ``text`` is verbatim. Everything else is derived and may be wrong, which is
    why each is separately measurable.
    """

    row_index: int
    column_index: int
    text: str
    bbox: tuple[float, float, float, float] | None
    row_span: int = 1
    column_span: int = 1
    is_header: bool = False
    header_path: tuple[str, ...] = ()
    row_label_path: tuple[str, ...] = ()
    footnote_refs: tuple[str, ...] = ()
    units: str | None = None
    """The units declaration that governs this cell, verbatim, or None.

    Per cell rather than per table, because §17.3 requires "table-level **and
    column-level** context… attached to values" and a table-wide attribute cannot
    express that. A declaration sitting in one column governs that column; one in
    the label column governs the table; the most specific wins.

    **Known limitation, unmeasured.** A *row*-level scale change is not detected —
    a "Margin %" row among crore figures inherits the table's declaration, which is
    wrong for that row. Detecting it needs the cell's own content to be read as a
    unit, and reading content is §16's job, not extraction's. Recorded in the
    reconstruction limitation register rather than guessed at here.
    """

    @property
    def is_absent(self) -> bool:
        """True when no cell was reported here — spanned over, or not found."""
        return self.bbox is None


@dataclass(frozen=True, slots=True)
class DerivedTable:
    """A detected table with its header, units, spans and footnotes resolved."""

    page_number: int
    bbox: tuple[float, float, float, float]
    row_count: int
    column_count: int
    cells: tuple[DerivedCell, ...]
    header_row_indices: tuple[int, ...] = ()
    units_rows: tuple[str, ...] = ()
    """Verbatim text of any row that announces a scale or currency.

    Recorded rather than applied. A dropped units row puts every value in the
    table out by a factor of ten million, so it is captured here and normalised
    only by the ledger, under §16, where the scale becomes an explicit qualifier.
    """

    def cell_at(self, row_index: int, column_index: int) -> DerivedCell:
        for cell in self.cells:
            if cell.row_index == row_index and cell.column_index == column_index:
                return cell
        raise KeyError((row_index, column_index))


def derive(table: DetectedTable) -> DerivedTable:
    """Resolve spans, header rows, units rows, footnotes and paths for a grid."""
    spans = _column_spans(table)
    row_spans = _row_spans(table)
    header_rows, units_row_indices, units_rows = _classify_leading_rows(table)
    header_texts = _header_texts(table, header_rows, spans)
    labels = _row_label_paths(table, header_rows | units_row_indices)
    table_units, column_units = _units_scopes(table, units_row_indices, spans)

    cells = tuple(
        DerivedCell(
            row_index=cell.row_index,
            column_index=cell.column_index,
            text=cell.text,
            bbox=cell.bbox,
            row_span=row_spans.get((cell.row_index, cell.column_index), 1),
            column_span=spans.get((cell.row_index, cell.column_index), 1),
            is_header=cell.row_index in header_rows,
            header_path=()
            if cell.row_index in header_rows
            else header_texts.get(cell.column_index, ()),
            row_label_path=()
            if cell.row_index in header_rows or cell.column_index == 0
            else labels.get(cell.row_index, ()),
            footnote_refs=split_footnote_marker(cell.text)[1],
            units=None
            if cell.row_index in header_rows or cell.row_index in units_row_indices
            else column_units.get(cell.column_index, table_units),
        )
        for cell in sorted(table.cells, key=lambda c: (c.row_index, c.column_index))
    )

    return DerivedTable(
        page_number=table.page_number,
        bbox=table.bbox,
        row_count=table.row_count,
        column_count=table.column_count,
        cells=cells,
        header_row_indices=tuple(sorted(header_rows)),
        units_rows=units_rows,
    )


def _column_spans(table: DetectedTable) -> dict[tuple[int, int], int]:
    """How many columns each populated cell covers.

    The only available signal is an absent neighbour: a detector reports a merged
    cell as one cell followed by nothing, never as a span count. Which means a
    genuine merge and a detector that simply failed to find a cell are
    indistinguishable here — a limitation the ground truth has to measure, not one
    this function can resolve.
    """
    spans: dict[tuple[int, int], int] = {}
    for row_index in range(table.row_count):
        row = table.row(row_index)
        for position, cell in enumerate(row):
            if cell.is_absent:
                continue
            width = 1
            for following in row[position + 1 :]:
                if not following.is_absent:
                    break
                width += 1
            spans[(cell.row_index, cell.column_index)] = width
    return spans


def _row_spans(table: DetectedTable) -> dict[tuple[int, int], int]:
    """How many rows each populated cell covers, by the same absent-neighbour rule."""
    spans: dict[tuple[int, int], int] = {}
    for column_index in range(table.column_count):
        for row_index in range(table.row_count):
            cell = table.cell_at(row_index, column_index)
            if cell.is_absent:
                continue
            height = 1
            for below in range(row_index + 1, table.row_count):
                if not table.cell_at(below, column_index).is_absent:
                    break
                height += 1
            spans[(row_index, column_index)] = height
    return spans


def looks_like_year(text: str) -> bool:
    """True when the text is a bare four-digit year rather than a magnitude.

    The distinction carries real weight. ``Particulars | 2025 | 2024`` is one of
    the commonest header shapes in an Indian filing, and without separating a year
    from a value it reads as a data row — a label plus numbers — which would put
    the header boundary in the wrong place and leave every column unlabelled.

    Bare is the operative word: ``2025`` is a year, ``2,025`` is a figure. A filing
    that grouped its years would defeat this, and none in the corpus does.
    """
    stripped = text.strip()
    return (
        len(stripped) == 4
        and stripped.isdigit()
        and 1900 <= int(stripped) <= 2199
    )


def _is_data_row(row: tuple[DetectedCell, ...]) -> bool:
    """A data row carries a label in its first column and a *magnitude* beyond it.

    All three qualifications matter. Requiring a value excludes a caption and a
    units row. Requiring a label excludes a period header whose cells are bare
    years. Requiring the value to be a magnitude rather than any numeral excludes
    ``Particulars | 2025 | 2024``, where both halves are otherwise satisfied.

    The known misfire: a genuine data row whose values are all bare four-digit
    numbers — a headcount of ``2,025`` written without its separator — would read
    as a header. It can only misfire above the first real data row, since this
    stops there, and it is one of the things the ground truth measures rather than
    something this rule can settle.
    """
    if not row or not row[0].text.strip():
        return False
    return any(
        looks_numeric(cell.text) and not looks_like_year(cell.text) for cell in row[1:]
    )


def _classify_leading_rows(
    table: DetectedTable,
) -> tuple[frozenset[int], frozenset[int], tuple[str, ...]]:
    """Split the rows above the first data row into header rows and units rows.

    Returns the header rows, the units rows, and the units text. The units *rows*
    are returned as well as their text because they have to be excluded from
    row-label paths: a scale note drawn to the left of the line items would
    otherwise be read as an indentation parent and appear as the outermost label
    on every row in the table.
    """
    headers: list[int] = []
    units_rows: list[int] = []
    units: list[str] = []

    for row_index in range(table.row_count):
        row = table.row(row_index)
        if _is_data_row(row):
            break
        stated = [cell.text.strip() for cell in row if states_units(cell.text)]
        if stated:
            units_rows.append(row_index)
            units.extend(stated)
        else:
            headers.append(row_index)

    return frozenset(headers), frozenset(units_rows), tuple(units)


def _units_scopes(
    table: DetectedTable,
    units_row_indices: frozenset[int],
    spans: dict[tuple[int, int], int],
) -> tuple[str | None, dict[int, str]]:
    """Split units declarations into a table-wide one and per-column ones.

    Where a declaration sits decides what it governs. "(Rs in crore)" in the label
    column is a statement about the table; the same text sitting above one period
    column is a statement about that column, and applying it to the whole table
    would misscale every other column in it. A declaration that spans several
    columns governs all of them.

    The label column is treated as table scope rather than as "column 0's units"
    because column 0 holds row labels, not values, so a units note there can only
    be describing the figures to its right.
    """
    table_units: str | None = None
    column_units: dict[int, str] = {}

    for row_index in sorted(units_row_indices):
        for cell in table.row(row_index):
            text = cell.text.strip()
            if cell.is_absent or not states_units(text):
                continue
            if cell.column_index == 0:
                table_units = text
                continue
            width = spans.get((row_index, cell.column_index), 1)
            for offset in range(width):
                column_units[cell.column_index + offset] = text

    return table_units, column_units


def _header_texts(
    table: DetectedTable,
    header_rows: frozenset[int],
    spans: dict[tuple[int, int], int],
) -> dict[int, tuple[str, ...]]:
    """The header path for each column, outermost first, with spans resolved.

    Span resolution is what makes a two-level header usable. "Year ended March 31"
    spanning two period columns must appear in *both* paths, or the 2024 column
    loses its own heading and every value beneath it is labelled only by its year.
    """
    paths: dict[int, list[str]] = {index: [] for index in range(table.column_count)}

    for row_index in sorted(header_rows):
        for cell in table.row(row_index):
            text = cell.text.strip()
            if cell.is_absent or not text:
                continue
            width = spans.get((row_index, cell.column_index), 1)
            for offset in range(width):
                column = cell.column_index + offset
                if column in paths:
                    paths[column].append(text)

    return {column: tuple(texts) for column, texts in paths.items()}


def _row_label_paths(
    table: DetectedTable, header_rows: frozenset[int]
) -> dict[int, tuple[str, ...]]:
    """The row-label path for each data row, outermost first.

    Nesting comes from indentation, because that is how a filing expresses it: "Of
    which: term deposits" sits further right than the "Deposits" it belongs under,
    and losing that relationship turns a component into a peer of its own total.

    Indentation is read from ``text_left``, never from the cell's box. In a ruled
    table every first-column cell shares the ruling line's left edge, so the box
    carries no indentation at all — measuring it there produced a flat path for a
    visibly nested table. Rows with no reported text position are treated as top
    level rather than guessed at.
    """
    paths: dict[int, tuple[str, ...]] = {}
    stack: list[tuple[float, str]] = []

    for row_index in range(table.row_count):
        if row_index in header_rows:
            continue
        label_cell = table.cell_at(row_index, 0)
        label = split_footnote_marker(label_cell.text)[0]
        if not label:
            continue

        if label_cell.text_left is None:
            stack = [(0.0, label)]
        else:
            indent = label_cell.text_left
            while stack and stack[-1][0] >= indent - _INDENT_TOLERANCE:
                stack.pop()
            stack.append((indent, label))

        paths[row_index] = tuple(text for _, text in stack)

    return paths


_INDENT_TOLERANCE: Final = 1.0
"""Points of horizontal slack before a label counts as indented.

Cell boxes on the same nominal left edge differ by fractions of a point, so an
exact comparison would read rounding noise as document hierarchy.
"""
