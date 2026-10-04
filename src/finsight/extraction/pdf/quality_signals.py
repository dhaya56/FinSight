"""What a page yielded, measured rather than judged.

These are counts, not decisions. PROJECT_BLUEPRINT.md §12.9 routes pages to
different producers — a scanned page needs OCR, a dense layout may need a
layout-aware parser — but the thresholds that make those calls require
development data and a recorded evaluation (CLAUDE.md §4, §8). So this module
measures, and something later decides.

The distinction that matters is between a page that produced *nothing* and a
page that produced blocks holding no characters. The first is a coverage gap
(§11.11): the region was not searched, and §27 must be able to tell a reader
that, rather than implying the page was blank. The second is a page FinSight did
read, whose blocks happen to be empty — a different fact, and one that a later
OCR routing rule will want to treat differently.
"""

from dataclasses import dataclass
from typing import Final

NO_TEXT_EXTRACTED: Final = "no_text_extracted"

TABLE_CELLS_DROPPED: Final = "table_cells_dropped"
"""A detector discarded cells it could not place into the table's grid.

Recorded on the table element so the run becomes ``partial`` rather than
``succeeded``. The loss is real and unrecoverable at this layer — the point is that
an incomplete table must not present as a complete one.
"""

TABLE_REGION_UNSUPPORTED: Final = "table_region_unsupported"
"""A region claimed a table where the page draws no ruling lines, though it draws
some elsewhere.

Measured on two of six real pages: a detector proposed a grid over two-column
prose and over an image, while each page's actual table was proposed by nothing
(ENV-008 §2.5). Both passed the content-based quality gate, because a paragraph
shredded into a grid has short cells.

A gap rather than a deletion. The region is refused as evidence by its verdict,
and this keeps the failure visible in the run state instead of letting a page that
lost its table report success.
"""
"""Recorded as a page's ``failure_reason`` when it yielded no blocks at all.

A short stable code, never a message: later phases count and surface these, and
prose is not a contract (see ``domain/errors.py``). It never contains any part of
the document.
"""


@dataclass(frozen=True, slots=True)
class PageSignals:
    """What one page gave up, in counts."""

    text_block_count: int
    image_block_count: int
    char_count: int

    @property
    def yielded_nothing(self) -> bool:
        """True when the page produced no text blocks whatsoever.

        The condition that records a coverage gap. Image blocks do not rescue a
        page here: an image is not text FinSight can cite, and until OCR exists
        a page of pictures has genuinely not been searched.
        """
        return self.text_block_count == 0

    @property
    def is_text_bearing(self) -> bool:
        """True when the page yielded at least one character.

        Deliberately not the same as :attr:`yielded_nothing` inverted. A page of
        empty blocks was read and produced structure without content, which is a
        weaker signal than producing nothing at all.
        """
        return self.char_count > 0

    @property
    def is_image_only(self) -> bool:
        """True when the page holds images and no characters.

        The shape of a scanned filing, and the strongest available hint that a
        page will need OCR. Reported, not acted upon.
        """
        return self.image_block_count > 0 and not self.is_text_bearing
