"""Tests for checking proposed table regions against ruling lines.

Every case here is drawn from a real page ENV-008 §2.5 measured, because the rules
were written against those pages and a synthetic fixture has twice in this project
overstated how a real document behaves.

The two that matter most pull in opposite directions.
:meth:`TestUnsupported.test_a_region_with_no_rules_on_a_ruled_page_is_unsupported`
is the one that catches a grid drawn over prose.
:meth:`TestUnsupported.test_an_unruled_page_condemns_nothing` is the one that stops
it condemning a whole document: Infosys p.140 carries real tables and draws not a
single rule, so a check that fired there would discard them all.
"""

from finsight.extraction.tables.regions import (
    MISSED_RULED_REGION,
    UNSUPPORTED,
    RuleBand,
    bands,
    review,
)

# Infosys p.300 as measured: two-column prose above, the real table below. The
# page yields two bands, 449-481 holding 2 rules and 534-757 holding 17, and the
# proposed region covers neither. The small upper band is kept at its real weight
# because it is what makes the min-rules floor observable on a real page.
PROSE_REGION = (44.0, 33.0, 563.0, 356.0)
TABLE_RULES = (
    449.0, 481.0,
    534.0, 548.0, 562.0, 576.0, 590.0, 604.0, 618.0, 632.0, 646.0,
    660.0, 674.0, 688.0, 702.0, 716.0, 730.0, 744.0, 757.0,
)


class TestBands:
    def test_rules_a_row_apart_are_one_band(self) -> None:
        """13.9pt is the measured within-table row spacing, at p50 and p75."""
        assert len(bands((100.0, 113.9, 127.8, 141.7))) == 1

    def test_a_table_sized_gap_splits_a_band(self) -> None:
        """44.5pt was the smallest measured gap between two real tables."""
        split = bands((100.0, 113.9, 158.4, 172.3))
        assert len(split) == 2
        assert split[0].count == 2 and split[1].count == 2

    def test_a_band_records_its_extent_and_weight(self) -> None:
        band = bands((100.0, 113.9, 127.8))[0]
        assert (band.top, band.bottom, band.count) == (100.0, 127.8, 3)

    def test_no_rules_is_no_bands(self) -> None:
        assert bands(()) == ()

    def test_input_need_not_be_sorted(self) -> None:
        """Drawing order is not reading order, and the caller should not have to care."""
        assert bands((127.8, 100.0, 113.9)) == bands((100.0, 113.9, 127.8))


class TestUnsupported:
    def test_a_region_with_no_rules_on_a_ruled_page_is_unsupported(self) -> None:
        """Infosys p.300: an 18x8 grid over prose while the real table sits below.

        The content-based gate accepted this region. Nothing inside it could have
        shown the problem — the cells are short, so no prose rule fires — which is
        why the signal has to come from the page rather than from the region.
        """
        result = review([PROSE_REGION], TABLE_RULES)

        assert result.unsupported == {0}

    def test_an_unruled_page_condemns_nothing(self) -> None:
        """Infosys p.140 draws no rules at all and carries real tables.

        A check that fired here would reject every table on every borderless page,
        which ENV-006 measured as the dominant presentation in this corpus.
        """
        result = review([PROSE_REGION, (0.0, 400.0, 100.0, 500.0)], ())

        assert result.unsupported == frozenset()
        assert result.missed == ()
        assert result.page_is_ruled is False

    def test_a_region_containing_rules_is_supported(self) -> None:
        covering = (40.0, 440.0, 570.0, 760.0)

        assert review([covering], TABLE_RULES).unsupported == frozenset()

    def test_one_rule_is_enough_to_support_a_region(self) -> None:
        """Deliberately permissive, and a known limit.

        HDFC p.279's spurious region clips exactly one rule and so survives this
        check, while the page's real table goes undetected. Refusing it would need
        a density threshold that no measurement supports yet, and over-refusing
        discards real tables. Recorded in the limitation register rather than
        guessed at.
        """
        grazing = (40.0, 400.0, 570.0, 455.0)

        assert review([grazing], TABLE_RULES).unsupported == frozenset()


class TestMissed:
    def test_a_ruled_area_no_region_covers_is_reported(self) -> None:
        """Infosys p.300 again: the real table, which no region proposed.

        One band is reported, not two: the page's other band carries two rules and
        falls under the floor, which is the behaviour measured on the real page.
        """
        result = review([PROSE_REGION], TABLE_RULES)

        assert len(result.missed) == 1
        assert result.missed[0].top == 534.0
        assert result.missed[0].count == 17

    def test_an_unsupported_region_cannot_cover_a_band(self) -> None:
        """Otherwise a bad region would mask the very table it displaced.

        A region spanning the page would overlap every band, so a detector that
        proposed one wrong region would silently suppress the report of what it
        missed — the opposite of what this exists to do.
        """
        whole_page = (0.0, 0.0, 600.0, 800.0)
        result = review([whole_page], TABLE_RULES)

        assert result.unsupported == frozenset()
        assert result.missed == ()

    def test_a_covered_band_is_not_reported(self) -> None:
        covering = (40.0, 440.0, 570.0, 760.0)

        assert review([covering], TABLE_RULES).missed == ()

    def test_a_stray_pair_of_rules_is_not_a_missed_table(self) -> None:
        """A heading underline and a figure's axis are not tables.

        Three is the least that can bound two rows of anything; reporting one or
        two would bury a real miss among page furniture.
        """
        result = review([], (100.0, 113.9))

        assert result.missed == ()
        assert result.bands == (RuleBand(top=100.0, bottom=113.9, count=2),)

    def test_three_rules_are_enough(self) -> None:
        assert len(review([], (100.0, 113.9, 127.8)).missed) == 1


class TestReasonCodes:
    def test_the_codes_are_distinct_and_stable(self) -> None:
        """Stored and filtered on, so they are identifiers rather than prose."""
        assert UNSUPPORTED != MISSED_RULED_REGION
        assert UNSUPPORTED == "region_has_no_ruling_lines"
        assert MISSED_RULED_REGION == "ruled_region_not_detected"
