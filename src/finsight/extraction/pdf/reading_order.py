"""Deciding the order blocks are read in.

Reading order is a judgement, not a fact recoverable from the file. A PDF stores
drawing instructions, and the order they appear in is the order a generator
happened to emit them — it may or may not match how a person reads the page.
FinSight therefore *decides* an order, records it as ``ordinal``, and keeps the
geometry that would let a better producer decide differently later.

The rule here is top-to-bottom, then left-to-right, and nothing more.

**It is wrong for multi-column layouts, and knowingly so.** Two columns of text
interleave under this rule: a block halfway down the left column sorts before a
block near the top of the right column, so the two columns are read across
rather than down. Every candidate library gets this wrong in its own way, and
choosing between the approaches needs real filings, which belong to a later
evaluation slice (§12.9). An obviously simple rule that fails visibly is better
here than a heuristic that fails quietly and gets mistaken for correct.
"""


def reading_order_key(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """Sort key placing a block above, then left of, its successors.

    Assumes the top-left origin that ``BlockLocation`` fixes: y increases
    downward, so ascending y0 runs down the page. No tolerance band groups
    blocks onto a shared line, because any tolerance is a threshold, and
    thresholds require development data before they are chosen (CLAUDE.md §4,
    §8). Blocks side by side at fractionally different heights therefore order
    by height, which is the same multi-column limitation described above.
    """
    x0, y0, _x1, _y1 = bbox
    return (y0, x0)
