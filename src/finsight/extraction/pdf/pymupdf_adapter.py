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
increasing downward, already resolved for page rotation — not the PDF format's
own bottom-left user space. :class:`BlockLocation` fixes that same convention,
so the two agree by construction. The fixtures are written with ReportLab, which
*does* use bottom-left origin, precisely so that a flip in either library fails
a test rather than cancelling out.

**Verbatim text.** Block text is stored exactly as PyMuPDF returns it, including
the newline it appends to every block. That newline is an artefact of this
producer rather than a character in the document, and it is kept anyway:
citations are offsets into the stored string, ``extraction_method`` records
which producer built it, and trimming "obvious" noise is where normalisation
starts. A different producer may legitimately return different text for the same
page, which is why the method is recorded per element.
"""

from typing import IO, Final

import pymupdf

from finsight.domain.representations.source import (
    BlockLocation,
    ElementType,
    ExtractedElement,
    PageLocation,
)
from finsight.extraction.contracts import DocumentUnreadableError
from finsight.extraction.pdf.quality_signals import NO_TEXT_EXTRACTED, PageSignals
from finsight.extraction.pdf.reading_order import reading_order_key

_TEXT_BLOCK: Final = 0
"""PyMuPDF's block-type marker for text. Image blocks carry 1."""


class PyMuPdfProducer:
    """Produces page and block elements from PDF bytes using PyMuPDF."""

    METHOD: Final = "pymupdf"

    @property
    def method(self) -> str:
        return self.METHOD

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
        text_entries.sort(key=lambda entry: reading_order_key(entry[:4]))

        blocks = tuple(
            self._block(ordinal, entry, locator=locator)
            for ordinal, entry in enumerate(text_entries)
        )
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
            children=blocks,
        )

    def _block(
        self,
        ordinal: int,
        entry: tuple[float, float, float, float, str, int, int],
        *,
        locator: str,
    ) -> ExtractedElement:
        x0, y0, x1, y1, text, _number, _kind = entry
        return ExtractedElement(
            element_type=ElementType.BLOCK,
            ordinal=ordinal,
            locator=locator,
            location=BlockLocation(bbox=(x0, y0, x1, y1)),
            extraction_method=self.method,
            extraction_method_version=self.method_version,
            text=text,
        )
