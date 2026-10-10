"""Glyph artefacts are decoded; judgement calls are not made.

The line between the two is the whole design. A ligature is the page's own letters
encoded as one glyph, so restoring them is decoding. Whether a word split across a line
is one word or two is a judgement about what an author meant, and this module refuses to
make it.

**Every character under test is named.** A no-break space, a thin space and a hair space
are indistinguishable from a space in source, so a test written with literal characters
cannot be reviewed — a reader cannot tell which case failed, or whether two cases are
even different.
"""

import pytest

from finsight.extraction.normalise import normalise

FF = chr(0xFB00)
FI = chr(0xFB01)
FL = chr(0xFB02)
FFI = chr(0xFB03)
FFL = chr(0xFB04)
LONG_ST = chr(0xFB05)
ST = chr(0xFB06)

NBSP = chr(0x00A0)
FIGURE_SPACE = chr(0x2007)
THIN_SPACE = chr(0x2009)
HAIR_SPACE = chr(0x200A)
NARROW_NBSP = chr(0x202F)
MATH_SPACE = chr(0x205F)
IDEOGRAPHIC_SPACE = chr(0x3000)

SOFT_HYPHEN = chr(0x00AD)
ZERO_WIDTH_SPACE = chr(0x200B)
ZERO_WIDTH_NON_JOINER = chr(0x200C)
ZERO_WIDTH_JOINER = chr(0x200D)
BYTE_ORDER_MARK = chr(0xFEFF)

CURLY_APOSTROPHE = chr(0x2019)
MINUS_SIGN = chr(0x2212)
EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
RUPEE = chr(0x20B9)
PRIVATE_USE = chr(0xE000)


class TestLigatures:
    """Measured: 573 of 741 passages unreachable because of these."""

    @pytest.mark.parametrize(
        ("encoded", "expected"),
        [
            (f"{FI}nancial", "financial"),
            (f"{FL}ow", "flow"),
            (f"e{FF}ective", "effective"),
            (f"o{FFI}ce", "office"),
            (f"ba{FFL}e", "baffle"),
            (LONG_ST, "st"),
            (ST, "st"),
        ],
    )
    def test_a_ligature_becomes_the_letters_it_stands_for(
        self, encoded: str, expected: str
    ) -> None:
        assert normalise(encoded) == expected

    def test_a_realistic_passage(self) -> None:
        source = (
            f"the fair value of future cash {FL}ows of a {FI}nancial "
            f"instrument will {FL}uctuate"
        )

        assert normalise(source) == (
            "the fair value of future cash flows of a financial "
            "instrument will fluctuate"
        )


class TestSpaces:
    """338 elements over forty characters carry no ASCII space at all."""

    @pytest.mark.parametrize(
        "space",
        [
            NBSP,
            FIGURE_SPACE,
            THIN_SPACE,
            HAIR_SPACE,
            NARROW_NBSP,
            MATH_SPACE,
            IDEOGRAPHIC_SPACE,
        ],
    )
    def test_an_indistinguishable_space_becomes_a_space(self, space: str) -> None:
        assert normalise(f"Revenues{space}1,62,990") == "Revenues 1,62,990"

    def test_a_row_separated_only_by_thin_spaces_becomes_words(self) -> None:
        row = f"Revenues*{THIN_SPACE}1,62,990{THIN_SPACE}1,53,670"

        assert normalise(row) == "Revenues* 1,62,990 1,53,670"


class TestInvisibleAndControl:
    def test_a_soft_hyphen_is_removed(self) -> None:
        """An optional break that was not taken is not part of the word."""
        assert normalise(f"inter{SOFT_HYPHEN}national") == "international"

    @pytest.mark.parametrize(
        "char",
        [
            ZERO_WIDTH_SPACE,
            ZERO_WIDTH_NON_JOINER,
            ZERO_WIDTH_JOINER,
            BYTE_ORDER_MARK,
        ],
    )
    def test_zero_width_characters_are_removed(self, char: str) -> None:
        assert normalise(f"net{char}profit") == "netprofit"

    @pytest.mark.parametrize(
        "code", [0x0001, 0x0083, 0x001F, 0x009F], ids=["c0", "c1-0083", "c0-unit", "c1"]
    )
    def test_control_characters_are_removed(self, code: int) -> None:
        assert normalise(f"total{chr(code)} assets") == "total assets"

    def test_tab_and_newline_survive(self) -> None:
        """A line break is a property of the page, and part of what was extracted."""
        assert normalise("a\tb\nc") == "a\tb\nc"


class TestLineBreakHyphen:
    """The break is closed; the judgement about the hyphen is refused."""

    def test_the_break_is_closed_and_the_hyphen_kept(self) -> None:
        assert normalise("long-\nterm debt") == "long-term debt"

    def test_a_genuine_word_break_is_also_only_closed(self) -> None:
        """"finan-cial" is made searchable in the analysis string, not here.

        Joining here would be right for this word and wrong for the 63% of split words
        whose hyphen is real, so the choice is made where being wrong costs nothing.
        """
        assert normalise("finan-\ncial risk") == "finan-cial risk"

    def test_indentation_around_the_break_is_consumed(self) -> None:
        assert normalise("related-  \n   party") == "related-party"

    def test_a_period_range_is_left_alone(self) -> None:
        """A digit on either side means a range, not one broken word."""
        assert normalise("FY2023-\n24") == "FY2023-\n24"

    def test_a_hyphen_not_at_a_break_is_untouched(self) -> None:
        assert normalise("wholly-owned subsidiary") == "wholly-owned subsidiary"


class TestLeftAlone:
    """Each of these was measured and deliberately not changed."""

    def test_a_curly_apostrophe_survives(self) -> None:
        """Measured: it lexes identically to a straight one, so changing it is churn."""
        source = f"the Company{CURLY_APOSTROPHE}s revenue"

        assert normalise(source) == source

    def test_the_minus_sign_survives(self) -> None:
        """Correct in a figure, and the numeral check already accepts it."""
        assert normalise(f"{MINUS_SIGN}412.00") == f"{MINUS_SIGN}412.00"

    @pytest.mark.parametrize("dash", [EN_DASH, EM_DASH], ids=["en", "em"])
    def test_en_and_em_dashes_survive(self, dash: str) -> None:
        assert normalise(f"2023{dash}24") == f"2023{dash}24"

    def test_the_rupee_sign_survives(self) -> None:
        assert normalise(f"{RUPEE}31,158 crore") == f"{RUPEE}31,158 crore"

    def test_a_private_use_glyph_survives(self) -> None:
        """Unmappable by definition; deleting text is worse than rendering a box."""
        assert normalise(f"value {PRIVATE_USE} here") == f"value {PRIVATE_USE} here"


class TestProperties:
    def test_normalising_twice_changes_nothing_further(self) -> None:
        """Extraction may be re-run, and the same bytes must give the same text."""
        source = f"{FI}nancial{NBSP}state-\nment{SOFT_HYPHEN} with {chr(0x83)} noise"
        once = normalise(source)

        assert normalise(once) == once

    def test_empty_text_is_returned_unchanged(self) -> None:
        assert normalise("") == ""

    def test_clean_text_is_returned_unchanged(self) -> None:
        clean = "Revenue from operations was 1,62,990 crore for the year."

        assert normalise(clean) == clean
