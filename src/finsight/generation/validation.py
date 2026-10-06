"""Checking a resolved answer against its evidence (§27.3, §27.6, §27.7, §27.8).

Every check produces a **finding**; none produces a decision. §27.11 gives an answer exactly one
decision and the next stage makes it, so a module that both found problems and acted on them
would make "why was this withheld" answerable in two places.

Findings carry one of two severities, and the distinction is the substance of this module:

* **remove** — the claim cannot be released. Its numbers are not in its sources, or its sources
  are not in the evidence set. Releasing it would state something no document states.
* **disclose** — the claim may be released, with the conflict said out loud. Two cited passages
  disagreeing on period or basis does not make the claim false; it makes an unqualified reading
  of it misleading, and §27.8 requires the qualification rather than the removal.

**Scale and currency are checked here, and that closes a gap the numeral check leaves open.**
``412.00 crore`` against a span reading ``412.00 lakh`` passes a value comparison — the numeral
matches and the scale is a separate token — while being wrong by a factor of a hundred. So a
claim may not introduce a scale word or a currency its cited spans do not carry.

**Equivalent spellings are not conflicts.** ``₹``, ``INR`` and ``Rs`` are one currency; ``crore``
and ``crores`` are one scale. Flagging those would make the Gate reject correct answers, which
is its own kind of failure and a harder one to notice.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from finsight.generation.evidence import EvidenceSet
from finsight.generation.numerals import unsupported_numerals
from finsight.generation.resolution import ResolvedAnswer, ResolvedClaim

__all__ = [
    "REASON_CITATION_NOT_IN_EVIDENCE",
    "REASON_CITED_SPAN_HAS_NO_TEXT",
    "REASON_CURRENCY_NOT_IN_SPAN",
    "REASON_MIXED_BASIS",
    "REASON_MIXED_ISSUER",
    "REASON_MIXED_PERIOD",
    "REASON_NO_CITATION",
    "REASON_SCALE_NOT_IN_SPAN",
    "REASON_UNSUPPORTED_NUMERAL",
    "Finding",
    "Severity",
    "validate_answer",
]


class Severity(StrEnum):
    """What a finding means for release."""

    REMOVE = "remove"
    DISCLOSE = "disclose"


REASON_UNSUPPORTED_NUMERAL: Final = "unsupported_numeral"
REASON_NO_CITATION: Final = "no_citation"
REASON_CITATION_NOT_IN_EVIDENCE: Final = "citation_not_in_evidence"
REASON_CITED_SPAN_HAS_NO_TEXT: Final = "cited_span_has_no_text"
REASON_SCALE_NOT_IN_SPAN: Final = "scale_not_in_cited_span"
REASON_CURRENCY_NOT_IN_SPAN: Final = "currency_not_in_cited_span"
REASON_MIXED_ISSUER: Final = "mixed_issuer"
REASON_MIXED_PERIOD: Final = "mixed_period"
REASON_MIXED_BASIS: Final = "mixed_basis"

_SCALES: Final = ("crore", "lakh", "thousand", "million", "billion", "trillion")
"""Scale words a filing attaches to a figure.

Bounded deliberately. An open-ended list would eventually include a word that is also ordinary
prose — "hundred" appears in "several hundred employees" — and a false conflict is worse here
than a missed one, because the numeral check still guards the value itself.
"""

_CURRENCIES: Final = {
    "inr": "INR",
    "rs": "INR",
    "₹": "INR",
    "usd": "USD",
    "$": "USD",
    "eur": "EUR",
    "€": "EUR",
    "gbp": "GBP",
    "£": "GBP",
}
"""Spellings mapped to one code, so an equivalent form is not read as a conflict."""

_WORD: Final = re.compile(r"[a-z₹$€£]+")


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem with one claim, and what it means for release."""

    claim_index: int
    code: str
    severity: Severity
    detail: str
    """Plain wording, for a reader deciding whether to trust what survived."""


def validate_answer(
    answer: ResolvedAnswer, evidence: EvidenceSet
) -> tuple[Finding, ...]:
    """Every finding across the answer, in claim order then check order."""
    findings: list[Finding] = []
    for index, claim in enumerate(answer.claims):
        findings.extend(_validate_claim(claim, index, evidence))
    return tuple(findings)


def _validate_claim(
    claim: ResolvedClaim, index: int, evidence: EvidenceSet
) -> list[Finding]:
    """Run every check against one claim."""
    findings: list[Finding] = []

    for identifier in claim.unresolved_ids:
        findings.append(
            Finding(
                claim_index=index,
                code=REASON_CITATION_NOT_IN_EVIDENCE,
                severity=Severity.REMOVE,
                detail=(
                    f"cites passage {identifier}, which is not in the evidence set; the "
                    f"reference was invented"
                ),
            )
        )

    for identifier in claim.textless_ids:
        findings.append(
            Finding(
                claim_index=index,
                code=REASON_CITED_SPAN_HAS_NO_TEXT,
                severity=Severity.REMOVE,
                detail=(
                    f"cites passage {identifier}, whose source regions carry no text, so "
                    f"nothing can be checked against it"
                ),
            )
        )

    if not claim.has_support:
        findings.append(
            Finding(
                claim_index=index,
                code=REASON_NO_CITATION,
                severity=Severity.REMOVE,
                detail="rests on no resolvable source region",
            )
        )
        # Every remaining check compares the claim against its spans, and there are none.
        return findings

    findings.extend(_check_numerals(claim, index))
    findings.extend(_check_units(claim, index))
    findings.extend(_check_context(claim, index, evidence))
    return findings


def _check_numerals(claim: ResolvedClaim, index: int) -> list[Finding]:
    """§27.3: a numeral the cited spans do not state."""
    unsupported = unsupported_numerals(claim.text, claim.supported_text)
    return [
        Finding(
            claim_index=index,
            code=REASON_UNSUPPORTED_NUMERAL,
            severity=Severity.REMOVE,
            detail=(
                f"states {numeral.as_written}, which appears in none of the passages it "
                f"cites"
            ),
        )
        for numeral in unsupported
    ]


def _check_units(claim: ResolvedClaim, index: int) -> list[Finding]:
    """Scale and currency the claim introduces but its spans do not carry.

    Closes the gap :mod:`finsight.generation.numerals` documents: a matching numeral under the
    wrong scale is wrong by a factor of a hundred and passes a value comparison.

    Only *introduced* tokens are flagged. A claim that omits a scale the span carries is
    imprecise rather than wrong, and removing it would punish a correct summary.
    """
    findings: list[Finding] = []
    claim_scales = _scales_in(claim.text)
    span_scales = _scales_in(claim.supported_text)
    for scale in sorted(claim_scales - span_scales):
        findings.append(
            Finding(
                claim_index=index,
                code=REASON_SCALE_NOT_IN_SPAN,
                severity=Severity.REMOVE,
                detail=(
                    f"states a figure in {scale}, a scale none of the passages it cites "
                    f"uses; the same numeral under a different scale is a different amount"
                ),
            )
        )

    claim_currencies = _currencies_in(claim.text)
    span_currencies = _currencies_in(claim.supported_text)
    for currency in sorted(claim_currencies - span_currencies):
        findings.append(
            Finding(
                claim_index=index,
                code=REASON_CURRENCY_NOT_IN_SPAN,
                severity=Severity.REMOVE,
                detail=(
                    f"states an amount in {currency}, a currency none of the passages it "
                    f"cites uses"
                ),
            )
        )
    return findings


def _check_context(
    claim: ResolvedClaim, index: int, evidence: EvidenceSet
) -> list[Finding]:
    """§27.7 and §27.8: cited passages that disagree about what they describe.

    Disclosed rather than removed. A claim resting on two periods is not false — the reader
    simply cannot tell which period it describes, and §27.8 requires that said rather than the
    claim discarded. §25.8 is the same reasoning for reporting basis: bases are compared only
    when a comparison was asked for, and nothing here knows what was asked.
    """
    passages = [
        passage
        for passage in (
            evidence.by_id(citation.passage_id) for citation in claim.citations
        )
        if passage is not None
    ]
    if len(passages) < 2:
        return []

    findings: list[Finding] = []
    for code, label, values in (
        (
            REASON_MIXED_ISSUER,
            "issuers",
            {passage.issuer_name for passage in passages if passage.issuer_name},
        ),
        (
            REASON_MIXED_PERIOD,
            "periods",
            {passage.fiscal_period for passage in passages if passage.fiscal_period},
        ),
        (
            REASON_MIXED_BASIS,
            "reporting bases",
            {
                passage.reporting_basis
                for passage in passages
                if passage.reporting_basis
            },
        ),
    ):
        if len(values) > 1:
            named = ", ".join(sorted(values))
            findings.append(
                Finding(
                    claim_index=index,
                    code=code,
                    severity=Severity.DISCLOSE,
                    detail=f"rests on passages from two or more {label}: {named}",
                )
            )
    return findings


def _scales_in(text: str) -> set[str]:
    """Scale words present, singular and lowercased so ``Crores`` matches ``crore``."""
    words = {word.rstrip("s") for word in _WORD.findall(text.lower())}
    return {scale for scale in _SCALES if scale in words}


def _currencies_in(text: str) -> set[str]:
    """Currency codes present, with equivalent spellings mapped to one code.

    Symbols are searched for directly rather than tokenised, because ``₹412.00`` has no word
    boundary between the symbol and the digits.
    """
    lowered = text.lower()
    found = {
        code for spelling, code in _CURRENCIES.items() if not spelling.isalpha()
        and spelling in lowered
    }
    words = {word.rstrip(".") for word in _WORD.findall(lowered)}
    found.update(
        code
        for spelling, code in _CURRENCIES.items()
        if spelling.isalpha() and spelling in words
    )
    return found
