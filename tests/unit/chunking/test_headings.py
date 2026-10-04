"""Tests for recovering section structure from unlabelled blocks.

Every rejection case here is text that a real development filing actually produced
and that an earlier version of this module wrongly called a heading. They are kept
verbatim because a heading heuristic fails in ways no invented example suggests: a
subsidiary table numbered ``3 Infosys``, a CSR projects table numbered ``1. …``,
``2. …``, ``3. …``, and a metric reading ``7.6 years``.

The bias is stated once and tested throughout: **missing a heading merges two
sections; inventing one attaches a wrong heading path to everything after it.**
The second is far worse, so every rule here trades recall for precision.
"""

import pytest

from finsight.chunking.headings import (
    HeadingStack,
    heading_level,
    suppress_list_runs,
)

MAX = 120


def level(text: str) -> int | None:
    return heading_level(text, max_chars=MAX)


class TestRecognisedHeadings:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1. Brief outline on CSR Policy of the Company:", 1),
            ("2. Composition of CSR Committee:", 1),
            ("3. Significant accounting policies", 1),
            ("3.2 Property, plant and equipment", 2),
            ("7.2.1 Deferred tax", 3),
            ("Item 7", 1),
            ("Note 12", 1),
            ("Annexure 6", 1),
            ("Part II", 1),
            ("Schedule III", 1),
        ],
    )
    def test_real_headings_are_found(self, text: str, expected: int) -> None:
        assert level(text) == expected

    def test_a_trailing_dot_is_punctuation_not_depth(self) -> None:
        """``7.`` and ``7.2`` both contain one dot and are different depths.

        Counting dots naively made every top-level section a subsection, which
        put 280 of 341 detected headings at level 2 on a real filing.
        """
        assert level("7. Risk factors") == 1
        assert level("7.2 Credit risk") == 2


class TestRejectedCandidates:
    def test_a_bare_number_is_a_table_row(self) -> None:
        """``3 Infosys`` is a subsidiary table row, not section three.

        Twenty consecutive rows of one table were read as sections before the dot
        was required. They escape the table-derived guard because the detector
        never found that table.
        """
        assert level("3 Infosys") is None
        assert level("4 EdgeVerve") is None
        assert level("11 Blue Acorn iCi") is None

    def test_a_decimal_measurement_is_not_a_section(self) -> None:
        """A decimal is indistinguishable from a sub-section number."""
        assert level("7.6 years") is None
        assert level("63.39 64.50") is None
        assert level("4.7 6.1") is None
        assert level("43.0 13.2% growth Y-o-Y (2)") is None

    def test_a_numbered_sentence_is_not_a_heading(self) -> None:
        assert level("1. We acquired three businesses during the year.") is None

    def test_prose_longer_than_the_bound_is_not_a_heading(self) -> None:
        assert level("1. " + "word " * 60) is None

    def test_a_number_with_no_title_is_a_list_marker(self) -> None:
        assert level("2.") is None
        assert level("2. a") is None

    def test_empty_and_blank_text_is_not_a_heading(self) -> None:
        assert level("") is None
        assert level("   \n  ") is None

    def test_a_colon_ending_is_allowed(self) -> None:
        """Filings write ``2. Composition of CSR Committee:``."""
        assert level("2. Composition of CSR Committee:") == 1


class TestListRunSuppression:
    def test_adjacent_same_level_candidates_are_a_list(self) -> None:
        """A CSR projects table numbers its rows 1., 2., 3. with nothing between."""
        assert suppress_list_runs([1, 1, 1, 1]) == [None, None, None, None]

    def test_headings_separated_by_body_text_survive(self) -> None:
        """A section, its body, then the next section."""
        assert suppress_list_runs([1, None, None, 1]) == [1, None, None, 1]

    def test_a_section_followed_by_its_subsection_survives(self) -> None:
        """Adjacent headings at *different* levels are real structure."""
        assert suppress_list_runs([1, 2]) == [1, 2]

    def test_a_lone_candidate_survives(self) -> None:
        assert suppress_list_runs([None, 1, None]) == [None, 1, None]

    def test_a_run_at_the_start_is_suppressed(self) -> None:
        assert suppress_list_runs([2, 2, None]) == [None, None, None]

    def test_an_empty_document_is_handled(self) -> None:
        assert suppress_list_runs([]) == []


class TestHeadingStack:
    def test_a_deeper_heading_extends_the_path(self) -> None:
        stack = HeadingStack()
        stack.push(1, "3. Significant accounting policies")
        stack.push(2, "3.2 Property, plant and equipment")

        assert stack.path == (
            "3. Significant accounting policies",
            "3.2 Property, plant and equipment",
        )

    def test_a_sibling_replaces_rather_than_nests(self) -> None:
        stack = HeadingStack()
        stack.push(1, "3. Policies")
        stack.push(1, "4. Estimates")

        assert stack.path == ("4. Estimates",)

    def test_returning_to_a_shallower_level_discards_the_deeper_path(self) -> None:
        stack = HeadingStack()
        stack.push(1, "3. Policies")
        stack.push(2, "3.2 Leases")
        stack.push(1, "4. Estimates")

        assert stack.path == ("4. Estimates",)

    def test_a_skipped_level_still_nests(self) -> None:
        """Filings skip levels; demanding a strict sequence would drop structure."""
        stack = HeadingStack()
        stack.push(1, "7. Risk factors")
        stack.push(3, "7.1.2 Concentration")

        assert stack.path == ("7. Risk factors", "7.1.2 Concentration")

    def test_whitespace_in_a_title_is_collapsed(self) -> None:
        stack = HeadingStack()
        stack.push(1, "7.  Risk\n  factors")

        assert stack.path == ("7. Risk factors",)
