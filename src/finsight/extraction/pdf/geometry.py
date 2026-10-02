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
