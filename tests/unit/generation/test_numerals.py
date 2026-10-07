"""Numeral verification: what must pass, and what must not.

The two failure directions are both costly and they pull opposite ways. Rejecting a legitimate
reformat makes the system refuse good answers; accepting a transposition lets a wrong figure
reach a reader. So the suite is deliberately balanced — a block of reformats that must pass, and
a block of near-misses that must fail.
"""

from decimal import Decimal

from finsight.generation.numerals import (
    MINUS_SIGNS,
    numerals_in,
    unsupported_numerals,
)


def values(text: str) -> list[Decimal]:
    return [numeral.value for numeral in numerals_in(text)]


class TestReading:
    def test_a_plain_integer(self) -> None:
        assert values("the figure was 412") == [Decimal("412")]

    def test_a_decimal(self) -> None:
        assert values("412.50") == [Decimal("412.50")]

    def test_thousands_separators(self) -> None:
        assert values("48,206.00") == [Decimal("48206.00")]

    def test_indian_grouping(self) -> None:
        """One crore: eight digits, not nine. A thousands-separator assumption reads it as two.

        The count is worth stating because it is easy to get wrong — 1,00,00,000 is
        10,000,000.
        """
        assert values("1,00,00,000") == [Decimal("10000000")]

    def test_a_parenthesised_figure_is_negative(self) -> None:
        """The worst defect available in a financial table is losing this sign."""
        assert values("(45)") == [Decimal("-45")]

    def test_every_minus_form_is_read_as_negative(self) -> None:
        """A typesetter's choice between these is invisible to a reader and must be to us.

        Reading only the ASCII hyphen as a sign would turn a negative figure positive.
        """
        for sign in MINUS_SIGNS:
            assert values(f"{sign}45") == [Decimal("-45")], f"{sign!r} not read as minus"

    def test_several_numerals_in_order(self) -> None:
        assert values("48,206.00 against 43,891.00") == [
            Decimal("48206.00"),
            Decimal("43891.00"),
        ]

    def test_a_percentage_yields_its_number(self) -> None:
        assert values("grew by 14.2%") == [Decimal("14.2")]

    def test_currency_symbols_are_not_part_of_the_numeral(self) -> None:
        assert values("₹412.00 and INR 412.00") == [
            Decimal("412.00"),
            Decimal("412.00"),
        ]

    def test_a_scale_word_is_not_part_of_the_numeral(self) -> None:
        assert values("48,206.00 crore") == [Decimal("48206.00")]

    def test_text_with_no_numerals(self) -> None:
        assert numerals_in("the Company monitors credit risk") == ()

    def test_as_written_is_preserved(self) -> None:
        """So a report can quote what the claim said, not a normalised form."""
        assert numerals_in("48,206.00 crore")[0].as_written == "48,206.00"


class TestReformatsThatMustPass:
    """Presentation differs, value does not. Rejecting these causes over-refusal."""

    def test_separators_added_or_removed(self) -> None:
        assert unsupported_numerals("48206 crore", "48,206 crore") == ()
        assert unsupported_numerals("48,206 crore", "48206 crore") == ()

    def test_trailing_zeros(self) -> None:
        assert unsupported_numerals("412.1", "412.10") == ()
        assert unsupported_numerals("412.00", "412") == ()

    def test_a_currency_symbol_in_only_one_place(self) -> None:
        assert unsupported_numerals("was ₹412.00", "amounted to 412.00") == ()

    def test_a_percent_sign_in_only_one_place(self) -> None:
        assert unsupported_numerals("14.2%", "14.2 per cent") == ()

    def test_indian_and_western_grouping_of_one_value(self) -> None:
        assert unsupported_numerals("10,000,000", "1,00,00,000") == ()

    def test_the_figure_appearing_later_in_a_long_span(self) -> None:
        span = "Other matters are discussed. " * 40 + "The allowance was 412.00 crore."
        assert unsupported_numerals("The allowance was 412.00 crore.", span) == ()


class TestNearMissesThatMustFail:
    """Value differs. Accepting any of these lets a wrong figure reach a reader."""

    def test_a_transposition(self) -> None:
        unsupported = unsupported_numerals("48,260.00", "48,206.00")

        assert [numeral.as_written for numeral in unsupported] == ["48,260.00"]

    def test_an_order_of_magnitude(self) -> None:
        assert unsupported_numerals("4,820.60", "48,206.00") != ()

    def test_a_dropped_negative_sign(self) -> None:
        """The span says (45); the claim says 45. Different quantities."""
        assert unsupported_numerals("a loss of 45", "the figure was (45)") != ()

    def test_a_decimal_shift(self) -> None:
        assert unsupported_numerals("41.20", "412.0") != ()

    def test_a_figure_present_in_no_span(self) -> None:
        unsupported = unsupported_numerals("revenue was 99,999.00", "revenue was 48,206.00")

        assert [numeral.value for numeral in unsupported] == [Decimal("99999.00")]

    def test_a_computed_growth_rate(self) -> None:
        """The arithmetic §7 forbids: both inputs present, the result stated nowhere."""
        span = "Revenue was 48,206.00 against 43,891.00 in the previous year."

        assert unsupported_numerals("Revenue grew by 9.8%.", span) != ()

    def test_an_empty_span_supports_nothing(self) -> None:
        assert unsupported_numerals("the figure was 412.00", "") != ()


class TestReporting:
    def test_a_claim_with_no_numerals_has_nothing_unsupported(self) -> None:
        assert unsupported_numerals("The Company monitors credit risk.", "") == ()

    def test_a_repeated_value_is_reported_once(self) -> None:
        unsupported = unsupported_numerals("412.00 and 412.00 again", "nothing here")

        assert len(unsupported) == 1

    def test_several_distinct_values_are_all_reported(self) -> None:
        unsupported = unsupported_numerals("1.5 and 2.5 and 3.5", "only 2.5 appears")

        assert [numeral.value for numeral in unsupported] == [
            Decimal("1.5"),
            Decimal("3.5"),
        ]

    def test_the_order_the_claim_wrote_them_is_kept(self) -> None:
        unsupported = unsupported_numerals("first 9.9 then 8.8", "")

        assert [numeral.as_written for numeral in unsupported] == ["9.9", "8.8"]

    def test_a_supported_and_an_unsupported_figure_together(self) -> None:
        span = "The allowance was 412.00 crore."
        unsupported = unsupported_numerals("It was 412.00, up from 386.00.", span)

        assert [numeral.value for numeral in unsupported] == [Decimal("386.00")]


class TestBothSidesReadIdentically:
    """A quirk matters only if the two sides disagree, and they cannot."""

    def test_a_version_like_string_is_self_consistent(self) -> None:
        assert unsupported_numerals("see 2.11.5", "refer to section 2.11.5") == ()

    def test_a_date_is_self_consistent(self) -> None:
        assert unsupported_numerals("on 31/03/2025", "as at 31/03/2025") == ()

    def test_a_fiscal_label_is_self_consistent(self) -> None:
        assert unsupported_numerals("in FY2024-25", "for FY2024-25") == ()
