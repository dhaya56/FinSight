"""Digit-group joining: the cases that must change, and the ones that must not."""

import pytest

from finsight.lexical.normalise import for_analysis


class TestJoining:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("10,000", "10000"),
            ("1,234", "1234"),
            ("123,456", "123456"),
            ("12,345,678", "12345678"),
            ("1,23,456", "123456"),
            ("1,00,00,000", "10000000"),
            ("1,234.56", "1234.56"),
            ("(10,000)", "(10000)"),
            ("-10,000", "-10000"),
            ("Rs 1,234 crore", "Rs 1234 crore"),
            ("₹10,000", "₹10000"),
        ],
    )
    def test_a_grouped_figure_becomes_one_token(
        self, source: str, expected: str
    ) -> None:
        assert for_analysis(source) == expected

    def test_the_two_spellings_converge(self) -> None:
        """The whole point: a query one way must reach a document the other way."""
        assert for_analysis("10,000") == for_analysis("10000")


class TestLeftAlone:
    @pytest.mark.parametrize(
        "source",
        [
            "notes 1,2,3",
            "sections 4,5 and 6",
            "a, b, c",
            "10000",
            "45.6%",
            "FY2024-25",
            "1,2345",
            "",
            "no digits at all",
        ],
    )
    def test_text_that_must_not_change(self, source: str) -> None:
        assert for_analysis(source) == source

    def test_an_enumeration_of_single_digits_keeps_its_commas(self) -> None:
        """Requiring a full two- or three-digit group is what protects this."""
        assert "," in for_analysis("refer to notes 1,2,3 and 7")

    def test_a_four_digit_run_after_a_comma_is_not_a_group(self) -> None:
        """Without the negative lookahead this would join on the first three."""
        assert for_analysis("1,2345") == "1,2345"


class TestProperties:
    def test_only_commas_are_ever_removed(self) -> None:
        source = "Revenue of Rs 1,23,456.78 crore in FY2024-25 (up 10,000 bps)"

        result = for_analysis(source)

        assert result.replace(",", "") == source.replace(",", "")
        assert len(result) <= len(source)

    def test_it_is_idempotent(self) -> None:
        """Applied twice, because both sides of a search call it independently."""
        source = "12,345,678 and 1,23,456"

        assert for_analysis(for_analysis(source)) == for_analysis(source)

    def test_no_digit_is_lost(self) -> None:
        source = "1,234,567"

        result = for_analysis(source)

        assert [c for c in result if c.isdigit()] == [
            c for c in source if c.isdigit()
        ]
