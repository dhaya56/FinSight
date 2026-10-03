"""Tests for the Docling table adapter.

These run against stub objects shaped like Docling's own output rather than
against the library, because the real thing needs ~500 MB of model weights and
takes seconds per page. What is being tested is **our conversion**, which is where
the defects were: character normalisation, empty-cell geometry, and silent drops.

The three cases that matter are :class:`TestVerbatimText`,
:class:`TestEmptyCellGeometry` and :class:`TestSilentLoss`. Each pins a defect
found by auditing real pages, and each would have passed unnoticed without one.
"""

from dataclasses import dataclass, field

import pytest

from finsight.extraction.pdf.docling_tables import (
    _assign_words,
    _contiguous,
    _empty_box,
    _grid_bounds,
    _joined,
    _table,
)

EN_DASH = chr(0x2013)
"""U+2013, named by codepoint rather than written literally.

The point of these tests is that this character survives extraction. A literal
here is flagged as an ambiguous homoglyph, and "fixing" that by substituting a
hyphen would silently delete the behaviour under test — which is the exact
substitution the detector was making.
"""

RUPEE = chr(0x20B9)
"""U+20B9, for the same reason: currency identity must not be normalised away."""


@dataclass
class _Box:
    l: float  # noqa: E741 - mirrors Docling's own attribute name
    t: float
    r: float
    b: float


@dataclass
class _Cell:
    start_row_offset_idx: int
    start_col_offset_idx: int
    text: str = ""
    row_span: int = 1
    col_span: int = 1
    column_header: bool = False
    bbox: _Box | None = None


@dataclass
class _Data:
    num_rows: int
    num_cols: int
    table_cells: list[_Cell] = field(default_factory=list)


@dataclass
class _Prov:
    page_no: int = 1
    bbox: _Box | None = None


@dataclass
class _Table:
    data: _Data
    prov: list[_Prov] = field(default_factory=lambda: [_Prov(bbox=_Box(0, 0, 400, 100))])


def cell(row: int, col: int, text: str, box: tuple[float, float, float, float], **kw):
    return _Cell(
        start_row_offset_idx=row,
        start_col_offset_idx=col,
        text=text,
        bbox=_Box(box[0], box[1], box[2], box[3]),
        **kw,
    )


class TestVerbatimText:
    """Cell text comes from the document, never from the detector.

    Docling normalises characters. Measured on one real page, its cells held no
    non-ASCII character at all where the document had eight U+2013 EN DASHes and
    two U+20B9 RUPEE SIGNs. In a financial table the en dash is the nil marker, so
    rewriting it to a hyphen destroys the difference between "no such item" and a
    minus sign — and §14.9 citations are offsets into stored text, which must
    therefore be the document's text.
    """

    def test_the_documents_characters_win_over_the_detectors(self) -> None:
        table = _Table(
            _Data(1, 1, [cell(0, 0, "-", (10.0, 10.0, 50.0, 20.0))])
        )
        words = [(12.0, 12.0, 20.0, 18.0, EN_DASH)]

        converted = _table(table, page_number=1, dropped=0.0, words=words)

        assert converted is not None
        assert converted.cells[0].text == EN_DASH

    def test_the_rupee_sign_survives(self) -> None:
        table = _Table(
            _Data(1, 1, [cell(0, 0, "1,234", (10.0, 10.0, 80.0, 20.0))])
        )
        words = [
            (12.0, 12.0, 20.0, 18.0, RUPEE + "1,234"),
        ]

        converted = _table(table, page_number=1, dropped=0.0, words=words)

        assert converted is not None
        assert RUPEE in converted.cells[0].text

    def test_a_cell_with_no_matching_word_keeps_the_detectors_text(self) -> None:
        """Falling back rather than emptying the cell.

        A cell whose words cannot be matched is still a cell with content. Silently
        blanking it would lose a value outright, which is worse than carrying a
        normalised one.
        """
        table = _Table(_Data(1, 1, [cell(0, 0, "Revenue", (10.0, 10.0, 50.0, 20.0))]))

        converted = _table(table, page_number=1, dropped=0.0, words=[])

        assert converted is not None
        assert converted.cells[0].text == "Revenue"


class TestEmptyCellGeometry:
    """An omitted position gets the box it occupies, not a placeholder.

    Docling omits empty positions entirely. A degenerate ``(0,0,0,0)`` placeholder
    put 56 of 280 cells on the corpus's largest table at the page origin — 20% of
    a financial table with coordinates pointing somewhere they are not, which a
    citation would resolve and highlight wrongly.
    """

    def test_an_omitted_position_is_placed_from_its_row_and_column(self) -> None:
        table = _Table(
            _Data(
                2,
                2,
                [
                    cell(0, 0, "a", (10.0, 10.0, 50.0, 20.0)),
                    cell(0, 1, "b", (60.0, 10.0, 100.0, 20.0)),
                    cell(1, 0, "c", (10.0, 30.0, 50.0, 40.0)),
                    # (1, 1) omitted: empty in the document
                ],
            )
        )

        converted = _table(table, page_number=1, dropped=0.0, words=[])

        assert converted is not None
        empty = converted.cell_at(1, 1)
        assert empty.bbox == (60.0, 30.0, 100.0, 40.0)
        assert empty.is_absent is False

    def test_a_spanned_position_is_absent_not_empty(self) -> None:
        """The distinction that decides which period a value belongs to."""
        table = _Table(
            _Data(
                1,
                2,
                [cell(0, 0, "Year ended", (10.0, 10.0, 100.0, 20.0), col_span=2)],
            )
        )

        converted = _table(table, page_number=1, dropped=0.0, words=[])

        assert converted is not None
        assert converted.cell_at(0, 1).is_absent is True
        assert converted.cell_at(0, 0).reported_column_span == 2

    def test_a_position_whose_row_is_unknown_gets_no_invented_box(self) -> None:
        """An invented coordinate is worse than a missing one, because it resolves."""
        bounds_rows, bounds_cols = _grid_bounds({(0, 0): (1.0, 2.0, 3.0, 4.0)}, 2, 2)

        assert _empty_box(1, 0, bounds_rows, bounds_cols) is None


class TestSilentLoss:
    """Everything discarded is counted, because a quiet loss is the worst kind."""

    def test_a_cell_outside_the_declared_grid_is_counted(self) -> None:
        table = _Table(
            _Data(
                1,
                1,
                [
                    cell(0, 0, "in", (10.0, 10.0, 50.0, 20.0)),
                    cell(5, 5, "out", (10.0, 10.0, 50.0, 20.0)),
                ],
            )
        )

        converted = _table(table, page_number=1, dropped=0.0, words=[])

        assert converted is not None
        assert converted.dropped_cells >= 1.0

    def test_words_reaching_no_cell_are_counted(self) -> None:
        """Text conservation, measured per word rather than by clipping a region."""
        table = _Table(_Data(1, 1, [cell(0, 0, "a", (10.0, 10.0, 50.0, 20.0))]))
        words = [
            (12.0, 12.0, 20.0, 18.0, "inside"),
            (300.0, 300.0, 320.0, 310.0, "outside"),
        ]

        converted = _table(table, page_number=1, dropped=0.0, words=words)

        assert converted is not None
        assert converted.unassigned_words == 1

    def test_a_table_declaring_no_rows_is_refused(self) -> None:
        assert _table(_Table(_Data(0, 0, [])), page_number=1, dropped=0.0, words=[]) is None


class TestWordAssignment:
    def test_a_word_goes_to_the_smallest_box_containing_it(self) -> None:
        """Docling reports a text extent on some tables and a cell boundary on
        others, so boxes overlap. The tighter claim wins, and one cell only."""
        boxes = {
            (0, 0): (0.0, 0.0, 100.0, 100.0),
            (0, 1): (10.0, 10.0, 30.0, 30.0),
        }
        assigned, unassigned = _assign_words(boxes, [(15.0, 15.0, 25.0, 25.0, "x")])

        assert assigned == {(0, 1): [(15.0, 15.0, 25.0, 25.0, "x")]}
        assert unassigned == 0

    def test_words_join_in_reading_order(self) -> None:
        words = [
            (50.0, 10.0, 60.0, 18.0, "second"),
            (10.0, 10.0, 20.0, 18.0, "first"),
        ]

        assert _joined(words) == "first second"


class TestPageRanges:
    @pytest.mark.parametrize(
        ("pages", "expected"),
        [
            ([1, 2, 3], [(1, 3)]),
            ([1, 5], [(1, 1), (5, 5)]),
            ([3, 1, 2, 9], [(1, 3), (9, 9)]),
            ([4], [(4, 4)]),
        ],
    )
    def test_pages_group_into_contiguous_runs(
        self, pages: list[int], expected: list[tuple[int, int]]
    ) -> None:
        """Converting ``(min, max)`` over a scattered selection processes every page
        between them — 357 pages instead of 10, when that mistake was first made."""
        assert _contiguous(pages) == expected
