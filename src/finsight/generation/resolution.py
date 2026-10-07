"""Resolving citation references into the source spans they name (§26.7).

**The model emits a number; code fetches the text.** A reference like ``[2]`` is resolved here
to the passage it names, then to the source elements that passage was built from, then to those
elements' verbatim text read from PostgreSQL. The model never writes a quotation, so a
quotation cannot be corrupted — the failure ADR-009 chose this design to make impossible rather
than to detect.

**Resolution decides nothing.** A reference naming no passage is recorded as unresolved and
carried forward; a claim whose cited spans have no text is recorded the same way. §27.11 gives
every answer exactly one decision and the Evidence Gate makes it, so this layer reports and the
Gate judges. Raising here would turn one bad reference into a failed answer.

**Why source elements rather than passage text.** A passage is a chunk, and a chunk is a
retrieval representation — blocks joined for search. §14.1 keeps citable content with the source
representation and §14.9 requires a citation to resolve to the source regions a chunk was built
from, so the span a claim is checked against is the element's own text, not the join.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from finsight.generation.contract import GeneratedAnswer, GeneratedClaim
from finsight.generation.evidence import EvidenceSet

__all__ = [
    "ResolvedAnswer",
    "ResolvedCitation",
    "ResolvedClaim",
    "resolve_answer",
    "source_element_ids",
]


@dataclass(frozen=True, slots=True)
class ResolvedCitation:
    """One source span a claim rests on, with the text read from the authoritative store."""

    passage_id: int
    source_element_id: UUID
    locator: str
    text: str
    """The element's verbatim text. Never written by the model."""


@dataclass(frozen=True, slots=True)
class ResolvedClaim:
    """One claim with its citations resolved, and a record of what did not resolve."""

    text: str
    citations: tuple[ResolvedCitation, ...]

    unresolved_ids: tuple[int, ...] = ()
    """Cited passage ids naming nothing in the evidence set.

    The model inventing a reference, which §27.6 removes. Carried rather than raised so the
    Gate can strip this claim and keep the others.
    """

    textless_ids: tuple[int, ...] = ()
    """Cited passages that resolved but whose source elements carry no text.

    Distinct from unresolved: the passage exists and was shown to the model, but nothing can be
    checked against it. A page element carries no text of its own, so this is reachable without
    anything being wrong.
    """

    @property
    def supported_text(self) -> str:
        """Every cited span concatenated, which is what a numeral is checked against.

        Joined with a newline rather than a space, so two spans cannot accidentally form a
        number that appears in neither — ``1,2`` from ``1`` and ``2`` would otherwise verify a
        figure no document states.
        """
        return "\n".join(citation.text for citation in self.citations)

    @property
    def has_support(self) -> bool:
        """Whether anything at all resolved for this claim."""
        return bool(self.citations)


@dataclass(frozen=True, slots=True)
class ResolvedAnswer:
    """The model's answer with every reference turned into text, nothing yet judged."""

    answerable: bool
    claims: tuple[ResolvedClaim, ...]

    @property
    def unresolved_ids(self) -> tuple[int, ...]:
        """Every invented reference across the answer, in first-seen order."""
        seen: list[int] = []
        for claim in self.claims:
            for identifier in claim.unresolved_ids:
                if identifier not in seen:
                    seen.append(identifier)
        return tuple(seen)

    @property
    def unsupported_claims(self) -> tuple[ResolvedClaim, ...]:
        """Claims for which nothing resolved. §27.3 removes these."""
        return tuple(claim for claim in self.claims if not claim.has_support)


def source_element_ids(
    answer: GeneratedAnswer, evidence: EvidenceSet
) -> tuple[UUID, ...]:
    """Every source element the answer's citations reach, for one batched read.

    Separate from :func:`resolve_answer` so the caller can fetch text in a single query rather
    than a round trip per citation — a path already dominated by model latency should not add
    one per reference.
    """
    found: list[UUID] = []
    for identifier in sorted(answer.cited_ids):
        passage = evidence.by_id(identifier)
        if passage is None:
            continue
        for citation in passage.citations:
            if citation.source_element_id not in found:
                found.append(citation.source_element_id)
    return tuple(found)


def resolve_answer(
    answer: GeneratedAnswer,
    evidence: EvidenceSet,
    *,
    element_text: Mapping[UUID, str],
) -> ResolvedAnswer:
    """Turn every citation reference into the source text it names.

    ``element_text`` maps source element ids to their verbatim text, as returned by
    ``SourceRepository.text_for``. An element missing from it is treated as carrying no text,
    which is recorded rather than inferred to be an error.
    """
    return ResolvedAnswer(
        answerable=answer.answerable,
        claims=tuple(
            _resolve_claim(claim, evidence, element_text) for claim in answer.claims
        ),
    )


def _resolve_claim(
    claim: GeneratedClaim,
    evidence: EvidenceSet,
    element_text: Mapping[UUID, str],
) -> ResolvedClaim:
    """Resolve one claim, keeping citation order and dropping duplicate references."""
    citations: list[ResolvedCitation] = []
    unresolved: list[int] = []
    textless: list[int] = []
    seen: set[tuple[int, UUID]] = set()

    for identifier in _distinct(claim.citations):
        passage = evidence.by_id(identifier)
        if passage is None:
            unresolved.append(identifier)
            continue

        resolved_any = False
        for citation in passage.citations:
            text = element_text.get(citation.source_element_id)
            if not text:
                continue
            key = (identifier, citation.source_element_id)
            if key in seen:
                continue
            seen.add(key)
            citations.append(
                ResolvedCitation(
                    passage_id=identifier,
                    source_element_id=citation.source_element_id,
                    locator=citation.locator,
                    text=text,
                )
            )
            resolved_any = True

        if not resolved_any:
            textless.append(identifier)

    return ResolvedClaim(
        text=claim.text,
        citations=tuple(citations),
        unresolved_ids=tuple(unresolved),
        textless_ids=tuple(textless),
    )


def _distinct(values: Sequence[int]) -> list[int]:
    """Citation ids in order, without repeats.

    A model citing the same passage twice for one claim is common and harmless, but resolving
    it twice would double its text in ``supported_text`` and make a numeral look better
    supported than it is.
    """
    seen: list[int] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen
