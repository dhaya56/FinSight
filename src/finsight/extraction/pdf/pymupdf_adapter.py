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

import io
from collections.abc import Sequence
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
from finsight.extraction.pdf.quality_signals import (
    NO_TEXT_EXTRACTED,
    TABLE_CELLS_DROPPED,
    PageSignals,
)
from finsight.extraction.pdf.reading_order import reading_order_key
from finsight.extraction.tables.contracts import DetectedTable, TableDetector
from finsight.extraction.tables.elements import to_element
from finsight.extraction.tables.structure import DerivedTable, derive
from finsight.extraction.tables.validation import TableQuality, assess

_TEXT_BLOCK: Final = 0
"""PyMuPDF's block-type marker for text. Image blocks carry 1."""


class PyMuPdfProducer:
    """Produces page, block, table and cell elements from PDF bytes.

    **Two engines, each doing what it is best at.** Pages, blocks, narrative text
    and geometry come from PyMuPDF, which is correct and roughly two orders of
    magnitude faster. Tables come from an injected detector, because measurement
    showed PyMuPDF cannot bound them: across ten hand-reviewed pages its best
    configuration merged four tables on one page into a single grid with a
    paragraph sliced across eleven columns, while Docling returned the four
    separately and matched the human count on 8 of 10 pages against 6.

    That is the per-page routing §12.9 describes, and the schema already carries
    it: ``extraction_method`` sits on each element, so one run may legitimately
    record blocks from one producer and cells from another.

    The detector is injected rather than chosen here. Admission of a production
    detector is ADR-003's, and a producer that hard-coded one would make that
    decision by default.
    """

    METHOD: Final = "pymupdf"

    def __init__(self, table_detector: TableDetector | None = None) -> None:
        self._tables: TableDetector = (
            table_detector
            if table_detector is not None
            else PyMuPdfTableDetector(strategy=STRATEGY_LINES)
        )

    @property
    def method(self) -> str:
        return self.METHOD

    @property
    def table_strategy(self) -> str:
        """Which table detector and strategy this producer is configured with."""
        return f"{self._tables.method}:{self._tables.strategy}"

    @property
    def method_version(self) -> str:
        return str(pymupdf.__version__)

    def produce(self, source: IO[bytes]) -> tuple[ExtractedElement, ...]:
        """Read every page and return it with its blocks and tables attached.

        Table detection runs **once for the whole document** rather than per page.
        A layout model loads its weights on first use and holds them, so calling it
        per page would pay that repeatedly; and a detector may legitimately need
        the document to resolve a table continued across a page break.

        Takes a stream to satisfy the producer protocol, but PyMuPDF parses from
        a buffer, so the document is read into memory here. That is a real limit
        worth naming: peak memory tracks document size, and §30.10's resource
        bounds stay unenforceable until the restricted parser worker exists.

        Raises:
            DocumentUnreadableError: the document is encrypted or unparsable.
        """
        data = source.read()
        detected = self._tables.detect(io.BytesIO(data))

        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except (RuntimeError, ValueError) as error:
            raise DocumentUnreadableError("the document could not be parsed") from error

        with document:
            if document.needs_pass:
                raise DocumentUnreadableError("the document is password protected")
            return tuple(
                self._page(document, index, detected.get(index + 1, ()))
                for index in range(document.page_count)
            )

    def _page(
        self,
        document: pymupdf.Document,
        index: int,
        detected: Sequence[DetectedTable],
    ) -> ExtractedElement:
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

        tables = [derive(candidate) for candidate in detected]
        gaps = [_coverage_gap(candidate) for candidate in detected]
        quality = [
            assess(candidate, derived)
            for candidate, derived in zip(detected, tables, strict=True)
        ]

        children = self._ordered_children(
            placed, tables, gaps, quality, locator=locator
        )
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
        gaps: list[str | None],
        quality: list[TableQuality],
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
                    method=self._tables.method,
                    method_version=self._tables.method_version,
                    failure_reason=gap,
                    quality=verdict,
                ),
            )
            for derived, gap, verdict in zip(tables, gaps, quality, strict=True)
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


def _coverage_gap(detected: DetectedTable) -> str | None:
    """Why a table should be treated as incomplete, or None when it is whole.

    A detector that discards cells it cannot place reports that loss nowhere in its
    return value — measured at 4 of 35 cells on one real table, 11% of a financial
    table gone. §11.11 exists for exactly this: a region that failed to yield its
    content is a recorded gap, not a missing row, and ``derive_state`` turns a run
    containing one into ``partial`` rather than ``succeeded``.

    Reported rather than repaired. Nothing here can recover a cell the detector
    threw away, and presenting an 11%-incomplete balance sheet as whole is the
    failure this guards against.
    """
    if detected.dropped_cells >= 1.0:
        return str(TABLE_CELLS_DROPPED)
    return None


def _with_ordinal(element: ExtractedElement, ordinal: int) -> ExtractedElement:
    """Return the element with its page ordinal set.

    Elements are frozen, so position is applied once the full set of a page's
    children is known and sorted. A table's own cells keep the ordinals they were
    built with, since those index a grid rather than a page.
    """
    return replace(element, ordinal=ordinal)
