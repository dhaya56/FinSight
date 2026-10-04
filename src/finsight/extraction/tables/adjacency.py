"""Binding a table to the footnote text printed beneath it.

A footnote marker that resolves to nothing is worse than no marker: it tells a
reader a qualification exists and then withholds it. *"Includes one-time
non-recurring tax gains"* changes what a number means, and until now
``footnote_refs`` stored ``("a",)`` and pointed at no text anywhere.

**Binding is by marker, not by distance.** The obvious design — take the text
immediately below the table — is unsafe here, because ENV-008 §2.5 measured that
table regions are frequently mis-bounded by a row or more, so "immediately below"
is routinely another table's content or this table's own clipped rows. Matching the
*marker* instead means a binding rests on evidence from both ends: a cell that
refers to "(2)" and a line beneath that begins "(2)".

Measured on the twelve judged regions: four leading-marker lines sat below a
table, two matched a marker the table actually used and were bound, and two did
not and were left alone. Both unbound lines belonged to a table two regions away
whose boundary was wrong. **Under-binding is the designed failure.** A footnote
left unattached is a visible gap; a footnote attached to the wrong table silently
changes what a figure appears to mean.

Proximity still orders the search and bounds it: the scan stops at the next table
below, because text past another table belongs to that one.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from finsight.extraction.tables.structure import is_marker_token

BBox = tuple[float, float, float, float]

_LEADING_MARKER: Final = re.compile(
    r"^\s*(?P<marker>\((?P<paren>[A-Za-z0-9]{1,3})\)|[*†‡#]{1,3})\s*(?=\S)"
)
"""A marker opening a line of footnote text.

The mirror of the *trailing* marker a cell carries, and deliberately a separate
pattern: a cell's marker ends its text while a footnote's begins it, and one
pattern serving both would match a cell that merely opens with a parenthesis.

``(?=\\S)`` requires text after the marker. A line that is only "(2)" is a stray
fragment, not a footnote, and binding it would attach an empty qualification.
"""

_HORIZONTAL_OVERLAP: Final = 0.3
"""Overlap a block and a table must share, as a fraction of the **narrower** of
the two.

Multi-column pages put unrelated text directly beside a table, so some overlap
must be required. Measuring it against the table's width alone was wrong: a
heading like "Balance Sheet (contd.)" is a fifth of the width of the statement it
heads, so a correct heading failed a test asking it to span a third of the table.
Taking the narrower side makes the rule symmetric — a narrow heading over a wide
table and a wide paragraph under a narrow one both pass, and a neighbouring column
still fails because its overlap is negative.
"""


def _overlaps(box: BBox, table_bbox: BBox) -> bool:
    shared = min(box[2], table_bbox[2]) - max(box[0], table_bbox[0])
    narrower = min(box[2] - box[0], table_bbox[2] - table_bbox[0])
    return narrower > 0 and shared > _HORIZONTAL_OVERLAP * narrower

_CONTINUATION: Final = re.compile(
    r"\(\s*(?:cont'?d\.?|continued)\s*\)", re.IGNORECASE
)
"""A heading announcing that the table below continues one from an earlier page.

**The brackets are the whole rule.** Scanning the development split for the bare
word found 215 occurrences, essentially all of them ordinary prose — "continued to
mature the technology", "our continued commitment". Requiring the parenthesised
form found 98, and every one was a real continuation heading: "Balance Sheet
(contd.)", "B Other equity (continued)", "3.2 Property, plant and equipment
(continued)". No false positive in either document carrying them.

One document carried **none at all**, so this detects *declared* continuations and
says nothing about undeclared ones (§12.8).
"""

_MAX_BLOCKS_ABOVE: Final = 30
"""How many blocks above a table to search for a continuation heading.

Deliberately generous, and sized by a failure. A continuation heading sits at the
top of its page while its table may begin far down, and when the region is clipped
the blocks between are *the table's own rows* — on one real page the eight nearest
blocks above a region were all its own clipped line items. A tight bound searched
those and stopped short of the heading.

Affordable because the bracketed marker carries the precision: 98 occurrences
across the development split, no false positive. Scanning further costs recall
nothing and risks only picking up a heading that governs the whole page, which on
a continued statement is the right answer anyway.
"""

_MAX_BLOCKS_BELOW: Final = 8
"""How many blocks beneath a table to consider before giving up.

A bound on work, not a judgement. Footnotes sit directly under their table; a
marker matched eight blocks down is more likely a coincidence than a reference.
"""


@dataclass(frozen=True, slots=True)
class Footnote:
    """One footnote, bound to the table whose cells refer to it."""

    marker: str
    """The reference as the cell carries it — ``a``, ``2``, ``*`` — not ``(a)``.

    Normalised to the cell's form so the two ends compare directly; the brackets
    are presentation, and a cell writing ``(a)`` and a footnote line writing
    ``(a)`` must not fail to match a cell writing ``a``.
    """

    text: str
    """The footnote verbatim, marker included, because §14.9 citation offsets are
    into the stored string and a trimmed prefix shifts every one of them."""

    bbox: BBox


def _marker_key(token: str) -> str:
    return token.strip().strip("()").lower()


def bind_footnotes(
    table_bbox: BBox,
    markers: Sequence[str],
    blocks: Sequence[tuple[BBox, str]],
    other_tables: Sequence[BBox] = (),
    *,
    max_blocks: int = _MAX_BLOCKS_BELOW,
) -> tuple[Footnote, ...]:
    """Find the footnote text beneath a table for the markers its cells carry.

    ``blocks`` are the page's text blocks as ``(bbox, text)`` in displayed space.
    ``other_tables`` bounds the search: a block at or below another table's top
    edge belongs to that table, not this one.

    Returns at most one footnote per distinct marker — the nearest match wins,
    since a later line repeating a marker is a different table's footnote.
    """
    wanted = {_marker_key(marker) for marker in markers if _marker_key(marker)}
    if not wanted:
        return ()

    _, _, _, bottom = table_bbox
    width = table_bbox[2] - table_bbox[0]
    if width <= 0:
        return ()

    # The nearest table below, if any, is where this table's text stops.
    floor = min(
        (box[1] for box in other_tables if box[1] >= bottom - 1.0),
        default=float("inf"),
    )

    candidates = sorted(
        (
            (box, text)
            for box, text in blocks
            if box[1] >= bottom - 1.0
            and box[1] < floor
            and _overlaps(box, table_bbox)
        ),
        key=lambda item: item[0][1],
    )[:max_blocks]

    found: dict[str, Footnote] = {}
    for box, text in candidates:
        match = _LEADING_MARKER.match(text)
        if match is None:
            continue
        paren = match.group("paren")
        # A line opening "(RSU) ..." is prose about restricted stock units, not
        # footnote two. Same reasoning as the cell-side rule, same helper.
        if paren is not None and not is_marker_token(paren):
            continue
        token = paren if paren is not None else match.group("marker")
        key = _marker_key(token)
        if key in wanted and key not in found:
            found[key] = Footnote(marker=key, text=text, bbox=box)

    return tuple(found[key] for key in sorted(found))


def continuation_title(
    table_bbox: BBox,
    blocks: Sequence[tuple[BBox, str]],
    *,
    max_blocks: int = _MAX_BLOCKS_ABOVE,
) -> str | None:
    """The title of the statement this table continues, or None.

    Returns the heading's text with the "(continued)" marker removed — the title
    identifies *what* is continued, and is what a later layer would group on.

    **The nearest marked heading wins.** Pages carry nested continuations — a note
    heading continued under a section heading also continued — and the nearer one
    is the more specific.

    **This records a declaration; it does not link two tables.** Asserting which
    earlier table this one continues would mean matching on titles the extractor
    does not reliably have, across regions ENV-008 §2.5 measured as frequently
    mis-bounded. The document's own statement is evidence; the join inferred from
    it would not be.
    """
    top = table_bbox[1]
    width = table_bbox[2] - table_bbox[0]
    if width <= 0:
        return None

    # Starts above the table, rather than *ends* above it. A clipped region can
    # begin partway through its own heading, and requiring the heading to finish
    # before the region starts excluded exactly those cases.
    above = sorted(
        (
            (box, text)
            for box, text in blocks
            if box[1] < top and _overlaps(box, table_bbox)
        ),
        key=lambda item: -item[0][1],
    )[:max_blocks]

    for _, text in above:
        match = _CONTINUATION.search(text)
        if match is None:
            continue
        title = " ".join(text[: match.start()].split())
        return title or None
    return None
