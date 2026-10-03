"""Checking a proposed table region against the page's ruling lines.

A detector proposes regions. Nothing until now asked whether a region plausibly
contains a table, and ENV-008 §2.5 measured the cost: on two of six real pages the
proposed region held **no table at all** — an 18x8 grid over two-column prose, a
14x14 grid over an image — while the page's actual table was detected by nothing.
Both were accepted by the quality gate, because a paragraph shredded into a grid
has short cells and trips no prose rule.

Ruling lines are the independent signal the table detector does not use. They come
from the parser, which reports the page's vector drawings, so a region claimed
where the page draws no rules at all is a region unsupported by the document's own
typography.

**What this deliberately does not do.** An earlier design snapped a region's edges
to the rules it contains, to fix the clipped first and last rows ENV-008 recorded.
Measurement killed it: in this corpus tables are ruled *under headers and between
sections*, not row by row, so a rule run is a fraction of a table's extent.
Infosys p.234 carries a region spanning y 501-587 whose rules occupy only y
562-576. Snapping to that would have cut the table to a third of itself while
appearing to be a correctness fix. Rules bound *whether*, not *where*.

So this module answers one question — is this region supported by the page? — and
records what the detector missed, rather than guessing at an extent it cannot see.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

BBox = tuple[float, float, float, float]

UNSUPPORTED: Final = "region_has_no_ruling_lines"
"""A region claiming a table where the page draws none, though it draws some."""

MISSED_RULED_REGION: Final = "ruled_region_not_detected"
"""A run of ruling lines no region covers: a table the detector did not propose."""

BAND_GAP: Final = 40.0
"""Vertical gap above which two ruling lines belong to different tables.

Measured on five real pages carrying 103 rule gaps: within-table row spacing sits
at **13.9pt** at both the median and the 75th percentile, while gaps separating
tables measured 44.5pt and above. 40pt sits in the empty space between those
populations rather than being chosen for roundness.

It is used only to count how many distinct ruled areas a page has, never to decide
a table's extent — the same measurement showed no gap threshold recovers the table
count, 40pt giving 4 areas on a 4-table page and 3 on another. CLAUDE.md §9 keeps
it informational: nothing is rejected on the strength of this number alone.
"""

MIN_BAND_RULES: Final = 3
"""Rules a band needs before its absence from the detector's output is reported.

One or two rules are a heading underline or a figure's axis. Three is the least
that can bound two rows of anything, and reporting below that would bury a real
miss in noise. Unmeasured against human-verified truth, so a reported miss is a
prompt to look, not a count of lost tables.
"""


@dataclass(frozen=True, slots=True)
class RuleBand:
    """A run of horizontal ruling lines close enough to belong to one table."""

    top: float
    bottom: float
    count: int

    def overlaps(self, bbox: BBox) -> bool:
        """Whether a region's vertical span meets this band's at all."""
        return not (bbox[3] < self.top or bbox[1] > self.bottom)


@dataclass(frozen=True, slots=True)
class RegionReview:
    """What the page's typography says about a detector's proposals."""

    unsupported: frozenset[int]
    """Indices of regions claiming a table where the page draws no rules."""

    missed: tuple[RuleBand, ...]
    """Ruled areas no region covers — §11.11 coverage gaps, not silent losses."""

    bands: tuple[RuleBand, ...]

    @property
    def page_is_ruled(self) -> bool:
        """Whether the page draws rules at all.

        A page with none says nothing about its regions: Infosys p.140 carries
        real tables and draws not one rule, so every check here is disabled there
        rather than condemning the page.
        """
        return bool(self.bands)


def bands(rule_tops: Sequence[float], *, gap: float = BAND_GAP) -> tuple[RuleBand, ...]:
    """Group rule positions into runs separated by more than ``gap``."""
    ordered = sorted(rule_tops)
    if not ordered:
        return ()

    grouped: list[list[float]] = [[ordered[0]]]
    for position in ordered[1:]:
        if position - grouped[-1][-1] > gap:
            grouped.append([position])
        else:
            grouped[-1].append(position)

    return tuple(
        RuleBand(top=run[0], bottom=run[-1], count=len(run)) for run in grouped
    )


def review(
    regions: Sequence[BBox],
    rule_tops: Sequence[float],
    *,
    gap: float = BAND_GAP,
    min_band_rules: int = MIN_BAND_RULES,
) -> RegionReview:
    """Check proposed regions against the page's ruling lines.

    A region is *unsupported* when the page draws rules and the region contains
    none of them. A band is *missed* when no region overlaps it. Neither judgement
    is applied to an unruled page, where the evidence simply does not exist.
    """
    found = bands(rule_tops, gap=gap)
    if not found:
        return RegionReview(unsupported=frozenset(), missed=(), bands=())

    unsupported = {
        index
        for index, bbox in enumerate(regions)
        if not any(bbox[1] <= top <= bbox[3] for top in rule_tops)
    }

    missed = tuple(
        band
        for band in found
        if band.count >= min_band_rules
        and not any(
            band.overlaps(bbox)
            for index, bbox in enumerate(regions)
            if index not in unsupported
        )
    )

    return RegionReview(
        unsupported=frozenset(unsupported), missed=missed, bands=found
    )
