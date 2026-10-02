"""Tests for table structure derivation.

These run on a grid of cells rather than a PDF, which is the reason the derivation
lives above the detector boundary: every one of the four things a detector refuses
to supply — spans, header rows, units rows, footnote markers — is checkable here
without a document in sight.

The tests that matter most are :class:`TestFootnoteMarkers` and
:class:`TestHeaderPaths`. A marker mistaken for part of a value corrupts the value;
a parenthesised negative mistaken for a marker destroys a sign; and a header path
that loses its spanning row labels every figure by year alone, so a consolidated
column and a standalone one become indistinguishable.
"""

import pytest

from finsight.extraction.tables.contracts import (
    DetectedCell,
    DetectedTable,
    TableDetectionError,
)
from finsight.extraction.tables.structure import (
    derive,
    looks_numeric,
    split_footnote_marker,
    states_units,
)

# The fixture table, as a detector reports it. Row 0 column 2 is absent because
# "Year ended March 31" spans into it; row 4 column 2 is *empty*, which is a
# different fact the document itself states.
GRID: tuple[tuple[str | None, ...], ...] = (
    ("", "Year ended March 31", None),
    ("", "2025", "2024"),
    ("(Rs in crore)", "", ""),
    ("Deposits", "1,234", "1,100"),
    ("Of which: term deposits", "560", ""),
    ("Other income (a)", "56", "40"),
    ("Loss on sale", "(45)", "(30)"),
)

_LABEL_INDENT = {4: 72.0}
"""Row 4's text is drawn further right, which is how the filing nests it.

Applied to ``text_left`` rather than to the cell box, matching what a real detector
reports: in a ruled table every first-column cell shares the ruling line's left
edge, and only the text position carries the indentation.
"""


def table(grid: tuple[tuple[str | None, ...], ...] = GRID) -> DetectedTable:
    cells: list[DetectedCell] = []
    for row_index, row in enumerate(grid):
        for column_index, text in enumerate(row):
            absent = text is None
            left = 60.0 if column_index == 0 else 240.0
            indent = _LABEL_INDENT.get(row_index, left + 4.0)
            cells.append(
                DetectedCell(
                    row_index=row_index,
                    column_index=column_index,
                    text="" if absent else text,
                    bbox=None
                    if absent
                    else (left, 10.0 * row_index, left + 100.0, 10.0 * row_index + 9.0),
                    text_left=None if absent or not text.strip() else indent,
                )
            )
    return DetectedTable(
        page_number=1,
        bbox=(60.0, 0.0, 460.0, 140.0),
        row_count=len(grid),
        column_count=max(len(row) for row in grid),
        cells=tuple(cells),
    )


class TestLooksNumeric:
    @pytest.mark.parametrize(
        "text",
        ["1,234", "(45)", "1,23,456.78", "56", "-30", "12%", "  1 234  ", "2025"],
    )
    def test_presented_numerals_are_recognised(self, text: str) -> None:
        assert looks_numeric(text) is True

    @pytest.mark.parametrize(
        "text", ["", "   ", "Revenue", "Of which: term deposits", "Rs in crore", "-"]
    )
    def test_text_is_not_a_numeral(self, text: str) -> None:
        assert looks_numeric(text) is False

    def test_a_lakh_grouped_numeral_is_recognised(self) -> None:
        """Indian grouping is not the Western one, and the corpus uses it."""
        assert looks_numeric("₹1,23,456.78") is True


class TestStatesUnits:
    @pytest.mark.parametrize(
        "text",
        ["(Rs in crore)", "INR millions", "₹ in lakhs", "Rs. in Crores", "USD thousands"],
    )
    def test_a_units_row_is_recognised(self, text: str) -> None:
        assert states_units(text) is True

    @pytest.mark.parametrize("text", ["", "Revenue", "1,234", "Total income"])
    def test_ordinary_content_is_not_units(self, text: str) -> None:
        assert states_units(text) is False

    def test_a_value_naming_its_scale_is_not_a_units_row(self) -> None:
        """Otherwise "1,234 crore" would be read as a declaration, not a figure."""
        assert states_units("1,234 crore") is False


class TestFootnoteMarkers:
    @pytest.mark.parametrize(
        ("text", "body", "marker"),
        [
            ("Other income (a)", "Other income", "a"),
            ("Revenue (1)", "Revenue", "1"),
            ("Total*", "Total", "*"),
            ("Deposits**", "Deposits", "**"),
            ("Profit †", "Profit", "†"),
            ("Finance costs#", "Finance costs", "#"),
            ("Other income##", "Other income", "##"),
        ],
    )
    def test_a_trailing_marker_is_separated(
        self, text: str, body: str, marker: str
    ) -> None:
        assert split_footnote_marker(text) == (body, (marker,))

    @pytest.mark.parametrize("text", ["(45)", "(1,234)", "(30)"])
    def test_a_parenthesised_negative_is_not_a_marker(self, text: str) -> None:
        """The worst available defect in a financial table.

        Reading the parentheses as a footnote marker would leave the body as a
        bare number and silently discard the sign.
        """
        assert split_footnote_marker(text) == (text, ())

    @pytest.mark.parametrize("text", ["Revenue", "", "1,234"])
    def test_content_without_a_marker_is_unchanged(self, text: str) -> None:
        assert split_footnote_marker(text) == (text.strip(), ())

    @pytest.mark.parametrize("marker", ["(1)", "(a)", "*", "**", "***", "#", "##"])
    def test_every_form_the_corpus_actually_uses_is_covered(self, marker: str) -> None:
        """The marker set is measured, not guessed.

        Counting forms across the 1,403-page development split found ``#`` 56 times
        as a trailing marker — a form the first version of this pattern, written
        from assumption, missed entirely. This test exists so the set cannot
        regress to guesswork.
        """
        body, refs = split_footnote_marker(f"Finance costs{marker}")

        assert body == "Finance costs"
        assert refs == (marker.strip("()"),)

    def test_the_verbatim_text_is_never_rewritten(self) -> None:
        """Separation is additive. Citations are offsets into the stored string."""
        derived = derive(table())
        cell = derived.cell_at(5, 0)

        assert cell.text == "Other income (a)"
        assert cell.footnote_refs == ("a",)


class TestSpans:
    def test_a_spanning_header_covers_the_absent_column(self) -> None:
        derived = derive(table())

        assert derived.cell_at(0, 1).column_span == 2

    def test_an_absent_cell_reports_itself_as_absent(self) -> None:
        derived = derive(table())

        assert derived.cell_at(0, 2).is_absent is True

    def test_an_empty_cell_is_not_absent(self) -> None:
        """The distinction the whole grid model exists to preserve.

        Row 4 column 2 is a blank the document contains; row 0 column 2 is a
        position the document does not have. Collapsing them attributes a value to
        the wrong period.
        """
        derived = derive(table())

        assert derived.cell_at(4, 2).is_absent is False
        assert derived.cell_at(4, 2).text == ""

    def test_an_unmerged_cell_spans_one_column(self) -> None:
        derived = derive(table())

        assert derived.cell_at(3, 1).column_span == 1


class TestHeaderRows:
    def test_the_leading_non_data_rows_are_headers(self) -> None:
        derived = derive(table())

        assert derived.header_row_indices == (0, 1)

    def test_the_units_row_is_not_a_header(self) -> None:
        """It sits above the data but labels the scale, not the columns."""
        derived = derive(table())

        assert 2 not in derived.header_row_indices

    def test_a_period_header_of_bare_years_is_still_a_header(self) -> None:
        """2025 and 2024 are numerals, so a numeric test alone would misread them."""
        derived = derive(table())

        assert derived.cell_at(1, 1).is_header is True

    def test_a_labelled_period_header_is_still_a_header(self) -> None:
        """``Particulars | 2025 | 2024`` — the commonest header shape in a filing.

        It satisfies both halves of the naive data-row test: a label in the first
        column and numerals beyond it. Only separating a bare year from a magnitude
        keeps the header boundary in the right place.
        """
        grid = (
            ("Particulars", "2025", "2024"),
            ("Deposits", "1,234", "1,100"),
        )

        assert derive(table(grid)).header_row_indices == (0,)

    def test_data_rows_are_not_headers(self) -> None:
        derived = derive(table())

        assert all(
            derived.cell_at(row, 0).is_header is False for row in (3, 4, 5, 6)
        )


class TestUnitsRows:
    def test_the_units_row_is_recorded_verbatim(self) -> None:
        derived = derive(table())

        assert derived.units_rows == ("(Rs in crore)",)

    def test_a_table_without_units_records_none(self) -> None:
        grid = (("Particulars", "2025"), ("Revenue", "1,234"))

        assert derive(table(grid)).units_rows == ()


class TestUnitsScope:
    """§17.3 requires table-level *and column-level* unit context."""

    def test_a_declaration_in_the_label_column_governs_every_cell(self) -> None:
        derived = derive(table())

        assert derived.cell_at(3, 1).units == "(Rs in crore)"
        assert derived.cell_at(3, 2).units == "(Rs in crore)"

    def test_a_declaration_above_one_column_governs_only_that_column(self) -> None:
        """The case a table-wide attribute cannot express.

        A scale note sitting above a single column is a statement about that
        column. Applying it to the whole table would misscale every other column,
        which for a table mixing crore figures with percentages is a wrong number
        that looks entirely plausible.
        """
        grid = (
            ("Particulars", "2025", "2024"),
            ("", "Rs in crore", ""),
            ("Revenue", "1,234", "21.0"),
        )
        derived = derive(table(grid))

        assert derived.cell_at(2, 1).units == "Rs in crore"
        assert derived.cell_at(2, 2).units is None

    def test_a_header_cell_carries_no_units(self) -> None:
        derived = derive(table())

        assert derived.cell_at(1, 1).units is None

    def test_the_units_row_itself_carries_no_units(self) -> None:
        derived = derive(table())

        assert derived.cell_at(2, 0).units is None

    def test_a_table_without_a_declaration_leaves_units_unset(self) -> None:
        """Unset rather than assumed. §16 cannot recover from an invented scale."""
        grid = (("Particulars", "2025"), ("Revenue", "1,234"))
        derived = derive(table(grid))

        assert derived.cell_at(1, 1).units is None


class TestHeaderPaths:
    def test_a_spanning_header_appears_in_both_column_paths(self) -> None:
        """Without this the 2024 column is labelled only by its year."""
        derived = derive(table())

        assert derived.cell_at(3, 1).header_path == ("Year ended March 31", "2025")
        assert derived.cell_at(3, 2).header_path == ("Year ended March 31", "2024")

    def test_a_header_cell_carries_no_header_path(self) -> None:
        derived = derive(table())

        assert derived.cell_at(0, 1).header_path == ()


class TestRowLabelPaths:
    def test_an_indented_row_keeps_its_parent(self) -> None:
        """"Of which: term deposits" is a component of Deposits, not a peer of it."""
        derived = derive(table())

        assert derived.cell_at(4, 1).row_label_path == (
            "Deposits",
            "Of which: term deposits",
        )

    def test_a_top_level_row_stands_alone(self) -> None:
        derived = derive(table())

        assert derived.cell_at(3, 1).row_label_path == ("Deposits",)

    def test_a_later_top_level_row_does_not_inherit_the_indent(self) -> None:
        """The stack has to unwind, or every later row becomes a child."""
        derived = derive(table())

        assert derived.cell_at(6, 1).row_label_path == ("Loss on sale",)

    def test_a_row_label_path_drops_the_footnote_marker(self) -> None:
        """The path is for matching; the marker stays on the cell's own text."""
        derived = derive(table())

        assert derived.cell_at(5, 1).row_label_path == ("Other income",)

    def test_a_units_row_never_becomes_a_parent_label(self) -> None:
        """A units row sits above the data and is not an ancestor of it.

        The fixture hides this: its units row shares the data rows' indentation, so
        the stack happens to unwind. Drawn further left — as a caption-style scale
        note often is — it would otherwise become the outermost label on every row
        in the table, and "(Rs in crore)" would appear in every row-label path.
        """
        grid = (
            ("Particulars", "2025"),
            ("(Rs in crore)", ""),
            ("Deposits", "1,234"),
        )
        outdented = table(grid)
        shifted = tuple(
            DetectedCell(
                row_index=cell.row_index,
                column_index=cell.column_index,
                text=cell.text,
                bbox=cell.bbox,
                # Drawn further left than the line items, so a naive indentation
                # stack would make it their parent rather than their sibling.
                text_left=20.0
                if (cell.row_index, cell.column_index) == (1, 0)
                else cell.text_left,
            )
            for cell in outdented.cells
        )
        derived = derive(
            DetectedTable(
                page_number=1,
                bbox=outdented.bbox,
                row_count=outdented.row_count,
                column_count=outdented.column_count,
                cells=shifted,
            )
        )

        assert derived.cell_at(2, 1).row_label_path == ("Deposits",)


class TestGridValidation:
    def test_a_ragged_grid_is_refused(self) -> None:
        with pytest.raises(TableDetectionError, match="grid positions"):
            DetectedTable(
                page_number=1,
                bbox=(0.0, 0.0, 10.0, 10.0),
                row_count=2,
                column_count=2,
                cells=(
                    DetectedCell(row_index=0, column_index=0, text="a", bbox=None),
                ),
            )

    def test_duplicate_positions_are_refused(self) -> None:
        cell = DetectedCell(row_index=0, column_index=0, text="a", bbox=None)

        with pytest.raises(TableDetectionError, match="unique"):
            DetectedTable(
                page_number=1,
                bbox=(0.0, 0.0, 10.0, 10.0),
                row_count=1,
                column_count=2,
                cells=(cell, cell),
            )

    def test_a_cell_beyond_the_grid_is_refused(self) -> None:
        with pytest.raises(TableDetectionError, match="beyond the last column"):
            DetectedTable(
                page_number=1,
                bbox=(0.0, 0.0, 10.0, 10.0),
                row_count=1,
                column_count=1,
                cells=(DetectedCell(row_index=0, column_index=5, text="", bbox=None),),
            )

    def test_page_numbers_are_one_based(self) -> None:
        with pytest.raises(TableDetectionError, match="one-based"):
            DetectedTable(
                page_number=0,
                bbox=(0.0, 0.0, 10.0, 10.0),
                row_count=1,
                column_count=1,
                cells=(DetectedCell(row_index=0, column_index=0, text="", bbox=None),),
            )
