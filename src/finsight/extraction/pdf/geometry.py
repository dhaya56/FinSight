"""Coordinate handling shared by everything that reads PyMuPDF geometry.

One function, in one place, deliberately. PyMuPDF reports geometry in the
*unrotated* page space while ``page.rect`` is the displayed rectangle, and storing
one of each cost a real annual report the citation coordinates on 78 of its 369
pages. Blocks, table regions and cells all inherit that trap, so the transform
lives here rather than being written out once per caller — a second copy is how
the two coordinate spaces drift apart again.
"""

import pymupdf


def displayed_bbox(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    rotation_matrix: pymupdf.Matrix,
) -> tuple[float, float, float, float]:
    """Move a box from unrotated page space into displayed space.

    The identity matrix on an unrotated page, so this costs nothing and is applied
    unconditionally rather than behind a rotation check. Normalised afterwards,
    because rotation can swap which corner is which and the domain's box types
    require top-left to bottom-right.
    """
    box = pymupdf.Rect(x0, y0, x1, y1) * rotation_matrix
    box.normalize()
    return (box.x0, box.y0, box.x1, box.y1)


_AXIS_TOLERANCE = 3.0
"""How far from level a segment may lie and still count as a horizontal rule.

Filings draw rules as hairlines whose endpoints differ by rounding, and a rule
tilted more than this is a diagonal in a figure rather than a table edge.
"""

_MIN_RULE_LENGTH = 40.0
"""Shorter than this is a tick mark, an underline or a box corner, not a rule.

A table rule spans at least a column. Counting every short stroke would put a
chart's gridlines and a heading's underline on the same footing as a table's,
which is the distinction this signal exists to make.
"""


def horizontal_rules(
    page: pymupdf.Page,
    *,
    tolerance: float = _AXIS_TOLERANCE,
    min_length: float = _MIN_RULE_LENGTH,
) -> tuple[float, ...]:
    """The displayed vertical positions of a page's horizontal ruling lines.

    Deduplicated, because a single visual rule is frequently several drawing
    items laid end to end: the raw item count on one real page was 140 against 25
    distinct positions, and counting items would weight a segmented rule six times
    more heavily than a solid one.

    Both stroked lines and rectangles thin enough to be a rule are included — a
    table ruled with either looks identical to a reader and must look identical
    here. Positions come back in displayed space, matching every other coordinate
    this package reports.
    """
    matrix = page.rotation_matrix
    positions: set[float] = set()

    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                start, end = item[1], item[2]
                if (
                    abs(start.y - end.y) <= tolerance
                    and abs(start.x - end.x) >= min_length
                ):
                    positions.add(round((start * matrix).y, 1))
            elif item[0] == "re":
                rect = item[1] * matrix
                rect.normalize()
                if rect.height <= tolerance and rect.width >= min_length:
                    positions.add(round((rect.y0 + rect.y1) / 2, 1))

    return tuple(sorted(positions))
