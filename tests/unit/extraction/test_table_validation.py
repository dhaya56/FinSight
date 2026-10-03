"""Tests for the table quality verdict.

Two properties are under test, and they pull in opposite directions.

**A table that is not a table must be refused.** Every rejection rule here was
written against something found on a rendered page by hand: a region that was a
paragraph, a grid with no content, a one-column strip. None of them raised an
error, and each would have entered retrieval presenting as financial data.

**A table that is merely unusual must not be refused.** The signals are measured
and recorded; they gate nothing, because no cutoff on them is calibrated yet.
:meth:`TestSignalsDoNotGate.test_a_prose_heavy_table_is_still_accepted` is the test
that holds that line — if a graded threshold is ever added without evidence, it
fails.
"""

import pytest

from finsight.domain.representations.source import Verdict
from finsight.extraction.tables.contracts import DetectedCell, DetectedTable
from finsight.extraction.tables.structure import derive
from finsight.extraction.tables.validation import (
    ALL_PROSE,
    CELLS_DROPPED,
    DEGENERATE,
    EMPTY,
    TableQuality,
    assess,
)

FINANCIAL: tuple[tuple[str | None, ...], ...] = (
    ("", "2025", "2024"),
    ("Deposits", "1,234", "1,100"),
    ("Borrowings", "560", "480"),
)

_PROSE = (
    "The Company has evaluated subsequent events through the date these "
    "statements were issued and identified no matters requiring disclosure."
)


def table(
    grid: tuple[tuple[str | None, ...], ...] = FINANCIAL,
    *,
    dropped_cells: float = 0.0,
    unassigned_words: int = 0,
) -> DetectedTable:
    """Build a detected table from a grid, ``None`` marking an absent position."""
    cells: list[DetectedCell] = []
    for row_index, row in enumerate(grid):
        for column_index, text in enumerate(row):
            absent = text is None
            left = 60.0 + 180.0 * column_index
            top = 10.0 * row_index
            cells.append(
                DetectedCell(
                    row_index=row_index,
                    column_index=column_index,
                    text="" if absent else text,
                    bbox=None if absent else (left, top, left + 170.0, top + 9.0),
                    text_left=None if absent or not text.strip() else left + 4.0,
                )
            )
    return DetectedTable(
        page_number=1,
        bbox=(60.0, 0.0, 600.0, 10.0 * len(grid)),
        row_count=len(grid),
        column_count=max(len(row) for row in grid),
        cells=tuple(cells),
        dropped_cells=dropped_cells,
        unassigned_words=unassigned_words,
    )


def quality(
    grid: tuple[tuple[str | None, ...], ...] = FINANCIAL,
    *,
    dropped_cells: float = 0.0,
    unassigned_words: int = 0,
) -> TableQuality:
    detected = table(
        grid, dropped_cells=dropped_cells, unassigned_words=unassigned_words
    )
    return assess(detected, derive(detected))


class TestAcceptance:
    def test_an_ordinary_financial_table_is_accepted(self) -> None:
        assert quality().verdict is Verdict.ACCEPTED

    def test_an_accepted_table_carries_no_reasons(self) -> None:
        """Reasons are grounds for doubt, so a clean table has none.

        The schema enforces the converse — reasons without a verdict are refused —
        and this is the side of it the code has to honour.
        """
        assert quality().reasons == ()

    def test_only_an_accepted_table_is_usable(self) -> None:
        assert quality().usable is True
        assert quality((("",), ("",))).usable is False


class TestRejection:
    def test_a_grid_with_no_text_is_not_a_table(self) -> None:
        empty = quality((("", "", ""), ("", "", ""), ("", "", "")))
        assert empty.verdict is Verdict.REJECTED
        assert EMPTY in empty.reasons

    def test_a_single_column_has_no_grid_relationship(self) -> None:
        """One column cannot bind a value to a period, which is the point of a row."""
        strip = quality((("Deposits",), ("1,234",), ("560",)))
        assert strip.verdict is Verdict.REJECTED
        assert DEGENERATE in strip.reasons

    def test_a_single_row_has_no_grid_relationship(self) -> None:
        assert DEGENERATE in quality((("Deposits", "1,234"),)).reasons

    def test_a_region_of_sentences_is_a_paragraph(self) -> None:
        """Observed on a real page: a detector returned a paragraph as a table."""
        paragraph = quality(((_PROSE, _PROSE), (_PROSE, _PROSE)))
        assert paragraph.verdict is Verdict.REJECTED
        assert ALL_PROSE in paragraph.reasons

    def test_rejection_outranks_review(self) -> None:
        """An empty table is refused outright, not sent for review.

        Both conditions hold at once here, and reporting the weaker verdict would
        route a non-table into a queue that assumes there is something to look at.
        """
        both = quality((("", ""), ("", "")), dropped_cells=40.0)
        assert both.verdict is Verdict.REJECTED
        assert CELLS_DROPPED not in both.reasons


class TestReview:
    def test_a_dropped_cell_requires_review(self) -> None:
        """The detector said it could not place content. That content is lost.

        One cell is enough: on a balance sheet the dropped cell may be the total.
        """
        dropped = quality(dropped_cells=1.0)
        assert dropped.verdict is Verdict.REVIEW_REQUIRED
        assert dropped.reasons == (CELLS_DROPPED,)

    def test_no_dropped_cells_passes(self) -> None:
        assert quality(dropped_cells=0.0).verdict is Verdict.ACCEPTED


class TestSignalsDoNotGate:
    def test_a_prose_heavy_table_is_still_accepted(self) -> None:
        """Measured, recorded, and deliberately not acted on.

        A note disclosure is mostly sentences with a figure in it, and refusing it
        on an uncalibrated ratio would discard real financial content. CLAUDE.md §9
        keeps the gate informational until an approved baseline sets the cutoff; the
        signal is stored so that baseline has something to calibrate against.
        """
        mixed = quality(((_PROSE, "2025"), ("Deposits", "1,234"), (_PROSE, "560")))
        assert mixed.verdict is Verdict.ACCEPTED
        assert 0.0 < mixed.prose_ratio < 1.0

    def test_unassigned_words_are_recorded_without_gating(self) -> None:
        stray = quality(unassigned_words=7)
        assert stray.unassigned_words == 7
        assert stray.verdict is Verdict.ACCEPTED

    def test_a_table_of_words_is_accepted(self) -> None:
        """Numeric density is a signal, not a requirement.

        Auditor-attestation and related-party tables are entirely textual, and an
        earlier derivation bug that treated a non-numeric table as all headers was
        found exactly here.
        """
        words = quality(
            (
                ("Director", "Independent"),
                ("A. Shah", "Yes"),
                ("B. Rao", "No"),
            )
        )
        assert words.verdict is Verdict.ACCEPTED
        assert words.numeric_ratio == 0.0


class TestMeasurement:
    def test_numeric_ratio_counts_label_cells_in_its_denominator(self) -> None:
        """Pinning the definition, because the name does not give it away.

        The denominator is every body cell, row labels included, so a well-formed
        two-period table caps at 4/6 and a five-period one reaches 10/12. The signal
        therefore moves with column count as well as with numeric density, which is
        a limitation whatever calibrates it has to know about.
        """
        assert quality().numeric_ratio == pytest.approx(4 / 6, abs=5e-5)

    def test_numeric_ratio_ignores_header_rows(self) -> None:
        """Years in a header are not data, and counting them inflates the signal.

        Every numeral in this table sits in its header. Without the exclusion it
        would score 2/6 and read as partly numeric; it has no figures at all.
        """
        headers_only = quality(
            (
                ("", "2025", "2024"),
                ("Auditor", "Independent", "Independent"),
            )
        )
        assert headers_only.numeric_ratio == 0.0

    def test_filled_ratio_ignores_absent_positions(self) -> None:
        """An absent position is covered by a neighbour's span, not missing.

        Counting it as unfilled would make every spanning header look like a hole,
        which is the opposite of what the signal is for. The two grids below hold
        the same six populated cells and differ only in whether the ninth position
        is absent or empty, so the ratio has to differ.
        """
        spanning = (
            ("", "Year ended March 31", None),
            ("", "2025", "2024"),
            ("Deposits", "1,234", "1,100"),
        )
        flat = (
            ("", "Year ended March 31", ""),
            ("", "2025", "2024"),
            ("Deposits", "1,234", "1,100"),
        )
        assert quality(spanning).filled_ratio == pytest.approx(6 / 8, abs=5e-5)
        assert quality(flat).filled_ratio == pytest.approx(6 / 9, abs=5e-5)

    def test_an_empty_cell_lowers_the_filled_ratio(self) -> None:
        """Empty is a fact the document states, and distinct from absent."""
        blanks = quality(
            (
                ("", "2025", "2024"),
                ("Deposits", "1,234", ""),
                ("Borrowings", "560", "480"),
            )
        )
        assert blanks.filled_ratio == pytest.approx(7 / 9, abs=5e-5)

    def test_signals_on_a_table_with_nothing_in_it_are_zero_not_undefined(self) -> None:
        """Division by an empty set, three times over. A crash here is a lost page."""
        empty = quality((("", ""), ("", "")))
        assert (empty.prose_ratio, empty.numeric_ratio, empty.filled_ratio) == (
            0.0,
            0.0,
            0.0,
        )
