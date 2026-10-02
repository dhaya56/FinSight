"""The PyMuPDF producer.

The only module in FinSight that imports a PDF library, mirroring the rule that
keeps boto3 inside ``s3_store.py``. Everything above it sees
:class:`ExtractedElement` trees and the :class:`PdfProducer` protocol, so
replacing or supplementing this producer is a sibling file and a policy entry.

**Provisional, not selected.** PyMuPDF is the producer Phase 4 builds on, and
that is a working decision, not a §4 production selection: no evaluation has
compared it against Docling, MinerU, pdfplumber or pypdfium2 on real filings.
ADR-002 records that distinction. Nothing here should be read as evidence that
PyMuPDF extracts financial documents well; the tests prove the plumbing works.

**Coordinates.** PyMuPDF reports geometry with a top-left origin and y
increasing downward — not the PDF format's own bottom-left user space.
:class:`BlockLocation` fixes that same convention, so the two agree by
construction. The fixtures are written with ReportLab, which *does* use
bottom-left origin, precisely so that a flip in either library fails a test
rather than cancelling out.

**Rotation is not resolved for us, and this was a real defect.** ``page.rect``
is the displayed rectangle with ``/Rotate`` applied, but ``get_text("blocks")``
returns boxes in the *unrotated* page space. On a ``/Rotate 90`` page the two
disagree, and a stored citation box lands somewhere the reader is not looking.
Measured on the development corpus, 2,401 of one annual report's 14,686 blocks —
every block on its 78 rotated pages — fell outside their own page rectangle;
applying ``page.rotation_matrix`` brought all of them inside, and changed
nothing on documents with no rotated pages, where the matrix is the identity.

The transform is applied **before** reading order is decided, because ordering
raw coordinates on a rotated page sorts along the wrong axis. Boxes are
normalised afterwards, since rotation can swap which corner is which and
:class:`BlockLocation` requires a top-left to bottom-right box.

**Verbatim text.** Block text is stored exactly as PyMuPDF returns it, including
the newline it appends to every block. That newline is an artefact of this
producer rather than a character in the document, and it is kept anyway:
citations are offsets into the stored string, ``extraction_method`` records
which producer built it, and trimming "obvious" noise is where normalisation
starts. A different producer may legitimately return different text for the same
page, which is why the method is recorded per element.
"""

from dataclasses import replace
from typing import IO, Final

import pymupdf

from finsight.domain.representations.source import (
    BlockLocation,
    ElementType,
    ExtractedElement,
    PageLocation,
)
from finsight.extraction.contracts import DocumentUnreadableError
from finsight.extraction.pdf.geometry import displayed_bbox
from finsight.extraction.pdf.pymupdf_tables import (
    STRATEGY_LINES,
    PyMuPdfTableDetector,
)
from finsight.extraction.pdf.quality_signals import NO_TEXT_EXTRACTED, PageSignals
from finsight.extraction.pdf.reading_order import reading_order_key
from finsight.extraction.tables.elements import to_element
from finsight.extraction.tables.structure import DerivedTable, derive

_TEXT_BLOCK: Final = 0
"""PyMuPDF's block-type marker for text. Image blocks carry 1."""


class PyMuPdfProducer:
    """Produces page, block, table and cell elements from PDF bytes using PyMuPDF.

    **The table strategy is provisional and deliberately narrow.** ``lines`` is
    used because it is the conservative one: measured across 150 real corpus pages
    it proposed a table on 31% of them, while ``text`` proposed one on 97% —
    implausible for a filing, and the kind of over-detection that turns narrative
    prose into something that presents as a financial table. Neither is selected
    under §12.12; the comparison and its ground truth decide that, and this
    producer takes the one less likely to fabricate structure in the meantime.
    """

    METHOD: Final = "pymupdf"

    def __init__(self, table_strategy: str = STRATEGY_LINES) -> None:
        self._tables = PyMuPdfTableDetector(strategy=table_strategy)

    @property
    def method(self) -> str:
        return self.METHOD

    @property
    def table_strategy(self) -> str:
        """Which table strategy this producer is configured with."""
        return self._tables.strategy

    @property
    def method_version(self) -> str:
        return str(pymupdf.__version__)

    def produce(self, source: IO[bytes]) -> tuple[ExtractedElement, ...]:
        """Read every page and return it with its blocks attached.

        Takes a stream to satisfy the producer protocol, but PyMuPDF parses from
        a buffer, so the document is read into memory here. That is a real limit
        worth naming: peak memory tracks document size, and §30.10's resource
        bounds stay unenforceable until the restricted parser worker exists.

        Raises:
            DocumentUnreadableError: the document is encrypted or unparsable.
        """
        data = source.read()
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except (RuntimeError, ValueError) as error:
            raise DocumentUnreadableError("the document could not be parsed") from error

        with document:
            if document.needs_pass:
                raise DocumentUnreadableError("the document is password protected")
            return tuple(
                self._page(document, index) for index in range(document.page_count)
            )

    def _page(self, document: pymupdf.Document, index: int) -> ExtractedElement:
        page = document[index]
        page_number = index + 1
        locator = f"p. {page_number}"

        entries = page.get_text("blocks")
        text_entries = [entry for entry in entries if entry[6] == _TEXT_BLOCK]

        placed = [
            (_displayed_bbox(entry, page.rotation_matrix), str(entry[4]))
            for entry in text_entries
        ]
        placed.sort(key=lambda item: reading_order_key(item[0]))

        tables = [
            derive(detected)
            for detected in self._tables.tables_on_page(page, page_number=page_number)
        ]

        children = self._ordered_children(placed, tables, locator=locator)
        blocks = [
            child for child in children if child.element_type is ElementType.BLOCK
        ]
        signals = PageSignals(
            text_block_count=len(text_entries),
            image_block_count=len(entries) - len(text_entries),
            char_count=sum(block.char_count or 0 for block in blocks),
        )

        return ExtractedElement(
            element_type=ElementType.PAGE,
            ordinal=index,
            locator=locator,
            location=PageLocation(
                page_number=page_number,
                width=page.rect.width,
                height=page.rect.height,
                rotation=page.rotation,
            ),
            extraction_method=self.method,
            extraction_method_version=self.method_version,
            failure_reason=NO_TEXT_EXTRACTED if signals.yielded_nothing else None,
            children=children,
        )

    def _ordered_children(
        self,
        placed: list[tuple[tuple[float, float, float, float], str]],
        tables: list[DerivedTable],
        *,
        locator: str,
    ) -> tuple[ExtractedElement, ...]:
        """Order a page's blocks and tables together by position.

        Blocks and tables share one ordinal sequence because they share a page,
        and ``ordinal`` records reading order as the producer judged it. Appending
        tables after the blocks would have been easier and would have asserted that
        every table sits below every paragraph.

        **Text inside a table is emitted twice**, once as the blocks PyMuPDF
        reports for the page and once as cells. That is deliberate and it is not
        free. Suppressing the overlapping blocks would mean a false-positive table
        deletes narrative prose from the block stream — and §18 is explicit that
        boilerplate-style exclusion is a reversible *ranking* decision, never a
        destructive one. So both representations are stored, both are separately
        citable, and §18.4's table-aware chunking is what must avoid retrieving the
        same sentence twice. Recorded as a risk rather than resolved here.
        """
        blocks: list[tuple[tuple[float, float, float, float], ExtractedElement]] = [
            (bbox, self._block(0, bbox, text, locator=locator))
            for bbox, text in placed
        ]
        table_elements = [
            (
                derived.bbox,
                to_element(
                    derived,
                    ordinal=0,
                    method=self.method,
                    method_version=self.method_version,
                ),
            )
            for derived in tables
        ]

        combined = blocks + table_elements
        combined.sort(key=lambda item: reading_order_key(item[0]))
        return tuple(
            _with_ordinal(element, ordinal)
            for ordinal, (_, element) in enumerate(combined)
        )

    def _block(
        self,
        ordinal: int,
        bbox: tuple[float, float, float, float],
        text: str,
        *,
        locator: str,
    ) -> ExtractedElement:
        return ExtractedElement(
            element_type=ElementType.BLOCK,
            ordinal=ordinal,
            locator=locator,
            location=BlockLocation(bbox=bbox),
            extraction_method=self.method,
            extraction_method_version=self.method_version,
            text=text,
        )


def _displayed_bbox(
    entry: tuple[float, float, float, float, str, int, int],
    rotation_matrix: pymupdf.Matrix,
) -> tuple[float, float, float, float]:
    """Move a block's box from unrotated page space into displayed space."""
    return displayed_bbox(entry[0], entry[1], entry[2], entry[3], rotation_matrix)


def _with_ordinal(element: ExtractedElement, ordinal: int) -> ExtractedElement:
    """Return the element with its page ordinal set.

    Elements are frozen, so position is applied once the full set of a page's
    children is known and sorted. A table's own cells keep the ordinals they were
    built with, since those index a grid rather than a page.
    """
    return replace(element, ordinal=ordinal)
