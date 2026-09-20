"""The producer boundary.

A producer takes document bytes and yields :class:`ExtractedElement` trees. That
is the whole contract. No parser type crosses it — no PyMuPDF page, no Docling
document, no pdfplumber object — for the same reason no module outside
``s3_store.py`` imports boto3: the moment a library's own types appear in a
signature, replacing that library stops being a local change.

This matters more here than it does for storage. Financial filings vary too much
for one producer to be right forever, and PROJECT_BLUEPRINT.md §12.9 routes per
page, so a single run may legitimately use a fast path on page 3 and a
layout-aware path on page 47. Because ``extraction_method`` sits on each element
rather than on the run, that is representable today, with one registered
producer and no schema change waiting to happen.

What a producer must **not** do is interpret. It reports what it read and where
it sat. Reading order is a positional judgement recorded as ``ordinal``, not a
claim about document structure; a heading is a block that happens to be large,
not a heading. Everything downstream — chunking (§18), the ledger's numeric
parsing (§16), the retrieval representation (§14.2) — is built on the assumption
that extraction added nothing.
"""

from collections.abc import Sequence
from typing import IO, Protocol, runtime_checkable

from finsight.domain.errors import DomainError
from finsight.domain.representations.source import ExtractedElement, ExtractionState


class ExtractionError(DomainError):
    """A document could not be turned into source elements.

    Messages describe the failure, never the document's content, so an
    extraction failure cannot leak filing text into a log (CLAUDE.md §10).
    """


class DocumentUnreadableError(ExtractionError):
    """The document could not be opened at all.

    Encryption, a corrupt header, a truncated file. Distinct from a page that
    failed to yield text: that is a coverage gap recorded as a row (§11.11),
    while this leaves no usable output and fails the run.
    """


class UnsupportedFormatError(ExtractionError):
    """No producer is registered for this document's format.

    Intake accepts spreadsheets, HTML and XML alongside PDFs, and only the PDF
    path exists so far. Refusing is the honest outcome: handing a workbook to a
    PDF producer would record a failed run and blame the document.
    """


class UnknownDocumentVersionError(ExtractionError):
    """No document version exists with the requested identifier."""


@runtime_checkable
class PdfProducer(Protocol):
    """Produces source elements from PDF bytes.

    Implementations are stateless with respect to a document: the same bytes
    yield the same elements, because a run's output must be reproducible from
    its recorded method, version and configuration.
    """

    @property
    def method(self) -> str:
        """The value recorded as each element's ``extraction_method``."""
        ...

    @property
    def method_version(self) -> str:
        """The producing library's version, recorded per element.

        Separate from ``method`` because §16.5 requires provenance to record the
        extraction method *and* its version, and because a run may mix two
        producers at two versions.
        """
        ...

    def produce(self, source: IO[bytes]) -> tuple[ExtractedElement, ...]:
        """Read the document and return its page elements, each holding its blocks.

        Takes a stream to match the object-store port, so a caller never has to
        materialise a document just to hand it over. Whether an implementation
        can honour that is its own business: a producer backed by a library that
        requires the whole file in memory says so in its own documentation rather
        than forcing every caller to pass bytes.

        Raises:
            DocumentUnreadableError: the document could not be opened.
        """
        ...


def derive_state(elements: Sequence[ExtractedElement]) -> ExtractionState:
    """Decide how a run finished from what it actually produced.

    Derived rather than reported, so a producer cannot claim a clean run while
    emitting coverage gaps. An empty result is a failure: a document that yielded
    no elements at all has not been extracted, and recording it as success would
    make it indistinguishable from a genuinely empty document downstream.
    """
    if not elements:
        return ExtractionState.FAILED
    return (
        ExtractionState.PARTIAL
        if any(_has_gap(element) for element in elements)
        else ExtractionState.SUCCEEDED
    )


def _has_gap(element: ExtractedElement) -> bool:
    """True when this element or anything beneath it failed to yield text."""
    if element.failure_reason is not None:
        return True
    return any(_has_gap(child) for child in element.children)
