"""Tests for vertical-span label propagation and aggregate-row tagging.

Two defects and one new rule, all about attributing a figure correctly.

A value whose row label is lost cannot be cited, and a value summed together with
the total that already contains it is wrong by double. Both are silent: the
numbers are extracted perfectly and mean something other than what they appear to.
"""

import pytest

from finsight.extraction.tables.contracts import DetectedCell, DetectedTable
from finsight.extraction.tables.structure import derive, states_total

LABEL_X = 60.0
VALUE_X = 240.0


def build(
    grid: tuple[tuple[str | None, ...], ...],
    *,
    row_spans: dict[tuple[int, int], int] | None = None,
    indents: dict[int, float] | None = None,
) -> DetectedTable:
    """A detected table. ``None`` is an absent position, as a span reports it."""
    spans = row_spans or {}
    cells: list[DetectedCell] = []
    for row_index, row in enumerate(grid):
        for column_index, text in enumerate(row):
            absent = text is None
            left = LABEL_X if column_index == 0 else VALUE_X
            indent = (indents or {}).get(row_index, left + 4.0)
            cells.append(
                DetectedCell(
                    row_index=row_index,
                    column_index=column_index,
                    text="" if absent else text,
                    bbox=None
                    if absent
                    else (left, 10.0 * row_index, left + 150.0, 10.0 * row_index + 9),
                    text_left=None if absent or not text.strip() else indent,
                    reported_row_span=spans.get((row_index, column_index)),
                )
            )
    return DetectedTable(
        page_number=1,
        bbox=(LABEL_X, 0.0, 460.0, 10.0 * len(grid)),
        row_count=len(grid),
        column_count=max(len(row) for row in grid),
        cells=tuple(cells),
    )


SPANNED = (
    ("", "2025", "2024"),
    ("Borrowings", "100", "90"),
    (None, "50", "45"),
    ("Deposits", "10", "9"),
)


class TestVerticalSpanPropagation:
    def test_a_covered_row_inherits_the_spanning_label(self) -> None:
        """Without this the covered row's figures carry no label at all.

        Reproduced on a two-row span before the fix: row 2's values came back with
        an empty path, which makes them unattributable and so unusable as evidence.
        """
        derived = derive(build(SPANNED, row_spans={(1, 0): 2}))

        assert derived.cell_at(2, 1).row_label_path == ("Borrowings",)

    def test_the_spanning_row_keeps_its_own_label(self) -> None:
        derived = derive(build(SPANNED, row_spans={(1, 0): 2}))

        assert derived.cell_at(1, 1).row_label_path == ("Borrowings",)

    def test_a_later_label_is_not_overwritten(self) -> None:
        """Propagation must stop at the span's end, not run to the table's."""
        derived = derive(build(SPANNED, row_spans={(1, 0): 2}))

        assert derived.cell_at(3, 1).row_label_path == ("Deposits",)

    def test_nesting_is_inherited_whole(self) -> None:
        """A covered row belongs where its label belongs, indentation included."""
        grid = (
            ("", "2025", "2024"),
            ("Deposits", "", ""),
            ("Of which: term", "560", "500"),
            (None, "60", "55"),
        )
        derived = derive(
            build(grid, row_spans={(2, 0): 2}, indents={2: LABEL_X + 16.0})
        )

        assert derived.cell_at(3, 1).row_label_path == ("Deposits", "Of which: term")

    def test_a_span_running_past_the_last_row_is_clamped(self) -> None:
        """A detector may report a span wider than the grid. That is its error.

        Raising would discard a whole table over one bad number; fabricating the
        rows would invent positions the document does not have.
        """
        derived = derive(build(SPANNED, row_spans={(3, 0): 9}))

        assert derived.cell_at(3, 1).row_label_path == ("Deposits",)

    def test_an_unspanned_absent_label_inherits_nothing(self) -> None:
        """A row the detector simply failed to read must not borrow a neighbour's.

        The absent-neighbour inference would read row 2 as covered by row 1, and
        here the detector states otherwise. Guessing a label onto a figure is worse
        than leaving it unlabelled, because an unlabelled figure is visibly unusable
        while a wrongly labelled one is not.
        """
        derived = derive(build(SPANNED, row_spans={(1, 0): 1}))

        assert derived.cell_at(2, 1).row_label_path == ()


class TestReportedRowSpan:
    def test_a_reported_span_overrides_the_absent_neighbour_guess(self) -> None:
        """The column path honoured reported spans and this one did not.

        Invisible wherever the two agree, which is most tables — so the asymmetry
        survived until a detector disagreed with the guess.
        """
        derived = derive(build(SPANNED, row_spans={(1, 0): 1}))

        assert derived.cell_at(1, 0).row_span == 1

    def test_the_guess_still_applies_when_nothing_is_reported(self) -> None:
        derived = derive(build(SPANNED))

        assert derived.cell_at(1, 0).row_span == 2


class TestStatesTotal:
    @pytest.mark.parametrize(
        ("text", "word"),
        [
            ("Total", "total"),
            ("Total assets", "total"),
            ("Sub-total", "sub-total"),
            ("Subtotal", "subtotal"),
            ("Sub total", "sub total"),
            ("Grand total", "grand total"),
            ("Aggregate of the above", "aggregate"),
            ("TOTAL EQUITY AND LIABILITIES", "total"),
        ],
    )
    def test_aggregate_vocabulary_is_recognised(self, text: str, word: str) -> None:
        assert states_total(text) == (True, word)

    @pytest.mark.parametrize(
        "text",
        ["Deposits", "Revenue from operations", "", "   ", "Other income"],
    )
    def test_a_line_item_is_not_an_aggregate(self, text: str) -> None:
        assert states_total(text) == (False, None)

    def test_a_marker_does_not_hide_the_word(self) -> None:
        """Footnote markers sit on total rows as readily as anywhere else."""
        assert states_total("Total assets (a)")[0] is True

    def test_matching_is_on_word_boundaries(self) -> None:
        """A substring match would tag labels that merely contain the letters."""
        assert states_total("Totalisator receipts")[0] is False

    def test_the_longest_form_wins(self) -> None:
        """"Sub-total" must not be reported as "total" with the prefix lost."""
        assert states_total("Sub-total")[1] == "sub-total"


TOTALS = (
    ("", "2025", "2024"),
    ("Deposits", "100", "90"),
    ("Borrowings", "60", "54"),
    ("Total liabilities", "160", "144"),
)


class TestTotalRows:
    def test_a_total_row_is_tagged(self) -> None:
        assert derive(build(TOTALS)).total_row_indices == (3,)

    def test_every_cell_in_the_row_carries_the_flag(self) -> None:
        """The consumer that must not double-count holds a value, not a label."""
        derived = derive(build(TOTALS))

        assert derived.cell_at(3, 1).is_total is True
        assert derived.cell_at(3, 2).is_total is True

    def test_line_item_rows_are_not_tagged(self) -> None:
        derived = derive(build(TOTALS))

        assert (derived.cell_at(1, 1).is_total, derived.cell_at(2, 1).is_total) == (
            False,
            False,
        )

    def test_a_header_naming_a_total_column_is_not_a_total_row(self) -> None:
        """Otherwise a "Total" column heading tags the entire header row.

        Common in segment tables, where the last column aggregates the others.
        """
        grid = (
            ("", "Segment A", "Total"),
            ("Revenue", "10", "25"),
        )
        derived = derive(build(grid))

        assert derived.total_row_indices == ()

    def test_a_units_row_is_not_a_total_row(self) -> None:
        grid = (
            ("", "2025", "2024"),
            ("(Rs in crore)", "", ""),
            ("Total assets", "160", "144"),
        )
        derived = derive(build(grid))

        assert derived.total_row_indices == (2,)

    def test_a_table_with_no_aggregate_tags_nothing(self) -> None:
        grid = (("", "2025"), ("Deposits", "100"))

        assert derive(build(grid)).total_row_indices == ()
