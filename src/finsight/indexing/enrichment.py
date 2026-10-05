"""Composing the string that gets embedded, from the chunk and its context.

**This is where §14.2 and §14.4 are reconciled, and the reconciliation is the whole
module.** §14.2 says a retrieval representation "adds deterministic context useful
for lexical and dense search" and §14.6 permits issuer, document type, heading path,
page, period, basis, currency and scale. §14.4 forbids presenting enriched text as
original evidence. Both hold at once only if the enriched form exists *for search*
and never for citation:

* ``chunks.text`` stays verbatim in PostgreSQL. A citation resolves through
  ``chunk_sources`` to source elements and returns their text, never this.
* the string built here is embedded and discarded. Nothing stores it, so nothing
  can accidentally quote it back to a reader.

The limitation register recorded the decision before this was written: without it
"This was primarily due to increased cost of goods sold" embeds with no indication
of which company, filing, period or section it belongs to — and a corpus of three
filings makes that ambiguity immediate, not theoretical.

**Everything here is a projection of stored columns.** No value is inferred,
normalised against an alias table, or filled in when absent. A missing field is
omitted rather than written as "unknown", because "unknown" is a token that would
appear in every incomplete document and make them measurably similar to each other
for no reason.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

__all__ = ["ChunkContext", "embedded_text"]

_SEPARATOR: Final = " > "
"""Joins the heading path, outermost first (§18.2)."""


@dataclass(frozen=True, slots=True)
class ChunkContext:
    """The deterministic context §14.6 permits, as the database holds it.

    Every field is optional because ``document_metadata`` is populated from corpus
    governance and is absent for anything ingested outside the corpus. A document
    with no recorded issuer is a real state, not a defect to paper over.
    """

    issuer_name: str | None = None
    document_type: str | None = None
    fiscal_period: str | None = None
    reporting_basis: str | None = None
    currency: str | None = None
    heading_path: Sequence[str] = ()
    page_numbers: Sequence[int] = ()


def embedded_text(text: str, context: ChunkContext) -> str:
    """The string to embed: labelled context, a blank line, then the chunk verbatim.

    Labelled lines rather than a prose sentence. A label makes each value
    unambiguous to the model without this module having to interpret it — "Basis:
    both" carries the stored value exactly, where "consolidated and standalone"
    would be this module deciding what "both" means. §16.9 treats basis as a hard
    context dimension, and a dimension is not something an embedding helper should
    be paraphrasing.

    The blank line matters: it keeps the context and the body separable by eye when
    a trace is read, and keeps the body's first sentence from running into a label.

    ``units_as_presented`` is **deliberately excluded** although §14.6 permits
    scale. One corpus entry records it as "INR crore, lakh and million mixed within
    one document" — a description of a hazard rather than a unit. Embedding that
    sentence into every chunk of the document would add 60 characters of noise to
    each one and assert a scale the document does not have. Currency is included
    because it is a single stable token.
    """
    lines = [
        f"{label}: {value}"
        for label, value in (
            ("Issuer", context.issuer_name),
            ("Document", _readable(context.document_type)),
            ("Period", context.fiscal_period),
            ("Basis", context.reporting_basis),
            ("Currency", context.currency),
            ("Section", _section(context.heading_path)),
            (_page_label(context.page_numbers), _pages(context.page_numbers)),
        )
        if value
    ]
    if not lines:
        # Nothing is known about the document and the chunk has no heading or page.
        # Embedding the body alone is correct: a leading blank line would shift
        # every vector for a document whose metadata was never recorded.
        return text
    return "\n".join(lines) + "\n\n" + text


def _readable(document_type: str | None) -> str | None:
    """``annual_report`` as ``annual report``.

    The stored value is an enum member and underscores tokenize as punctuation, so
    the model sees one odd token where two ordinary words belong. A character
    substitution, not a mapping — nothing here decides what a document type means.
    """
    return document_type.replace("_", " ") if document_type else None


def _section(heading_path: Sequence[str]) -> str | None:
    """The heading path, or None when no heading was recognised.

    Empty is common: the heading rule is biased to precision and misses real
    headings by design, so an absent section means "none found above this chunk"
    and not "this chunk belongs to no section".
    """
    return _SEPARATOR.join(heading_path) if heading_path else None


def _page_label(page_numbers: Sequence[int]) -> str:
    return "Page" if len(page_numbers) == 1 else "Pages"


def _pages(page_numbers: Sequence[int]) -> str | None:
    """``42``, or ``42-45`` for a span.

    The endpoints, not the enumeration. ``page_numbers`` is a sorted set, so a
    chunk crossing a page boundary holds two or three values and the span says the
    same thing in fewer tokens. A chunk spanning non-adjacent pages cannot arise —
    blocks are joined in document order — and if one did, the span would still
    bound it correctly.
    """
    if not page_numbers:
        return None
    lowest, highest = min(page_numbers), max(page_numbers)
    return str(lowest) if lowest == highest else f"{lowest}-{highest}"
