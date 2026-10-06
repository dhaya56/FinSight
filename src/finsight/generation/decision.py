"""Turning findings into one decision (§27.9 to §27.12).

Every earlier stage reports; this one decides, and it is the only place that does. §27.11 gives
an answer exactly one decision, so "why was this withheld" has exactly one answer.

**Removals strip claims, they do not fail answers.** A model producing four claims where one
asserts an unsupported figure has produced three usable claims and one that must go. Discarding
all four would throw away correct, cited work because of a neighbour — and it is the common case,
not the exception.

**The support band is a rule, not a score** (§27.10). It is computed from three facts a reader
can check: whether anything was removed, whether a conflict was disclosed, and whether retrieval
or the model was degraded. §27.13 forbids presenting a number as calibrated correctness, and a
band is offered here precisely because it cannot be mistaken for one.

**Degradation is reported apart from reason codes** (§27.9). "The vector index was unreachable"
and "the passages do not support this" are different facts, and merging them tells a reader the
corpus lacks something when the deployment was simply unhealthy.

**Refusal is absent on purpose.** §27.12 reserves it for a prohibited operation, which requires
knowing what operation was requested — query classification that does not exist yet. A request
for arithmetic currently surfaces as abstention, because the model declines and nothing has
classified the question. Adding an unreachable member would be the placeholder §11 forbids.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from finsight.generation.resolution import ResolvedAnswer, ResolvedCitation
from finsight.generation.validation import Finding, Severity

__all__ = [
    "REASON_MODEL_REPORTED_UNANSWERABLE",
    "REASON_NOTHING_SURVIVED",
    "REASON_NO_EVIDENCE",
    "AnswerDecision",
    "Decision",
    "ReleasedClaim",
    "SupportBand",
    "WithheldClaim",
    "decide",
]

REASON_MODEL_REPORTED_UNANSWERABLE: Final = "model_reported_unanswerable"
"""The model said the passages do not answer the question.

Recorded as a reason in its own right. It is the one outcome that is not a failure of anything:
§26.1's instructions explicitly permit it, and a reader told only "abstained" cannot tell this
apart from an answer the Gate dismantled.
"""

REASON_NOTHING_SURVIVED: Final = "no_claim_survived_validation"

REASON_NO_EVIDENCE: Final = "no_evidence_retrieved"
"""Retrieval returned nothing, so no claim could have been supported.

Distinct from the model declining: the corpus had nothing to offer, which points at the filters
or the corpus rather than at the question.
"""


class Decision(StrEnum):
    """What happened to the answer. Exactly one per answer (§27.11)."""

    ANSWERED = "answered"
    PARTIAL = "partial"
    ABSTAINED = "abstained"


class SupportBand(StrEnum):
    """How cleanly the released content survived. Not a probability (§27.10, §27.13)."""

    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ReleasedClaim:
    """A claim that may be shown, with any conflict that must be shown beside it."""

    text: str
    citations: tuple[ResolvedCitation, ...]
    disclosures: tuple[Finding, ...] = ()


@dataclass(frozen=True, slots=True)
class WithheldClaim:
    """A claim that may not be shown, and why.

    **Kept rather than dropped.** A reader who cannot see what was removed cannot judge whether
    the remaining answer is complete, and an invisible removal is indistinguishable from a model
    that never said anything — which is the more flattering reading and the wrong one.
    """

    text: str
    findings: tuple[Finding, ...]


@dataclass(frozen=True, slots=True)
class AnswerDecision:
    """The released answer, the withheld claims, and the one decision about them."""

    decision: Decision
    reason_codes: tuple[str, ...]
    support_band: SupportBand
    released: tuple[ReleasedClaim, ...]
    withheld: tuple[WithheldClaim, ...]
    degraded: tuple[str, ...] = ()

    @property
    def is_released(self) -> bool:
        """Whether any claim reaches the reader."""
        return bool(self.released)


def decide(
    answer: ResolvedAnswer,
    findings: tuple[Finding, ...],
    *,
    degraded: tuple[str, ...] = (),
) -> AnswerDecision:
    """Apply findings to claims and reach one decision.

    ``degraded`` carries retrieval and model degradation flags, which shape the support band and
    are reported separately from reason codes.
    """
    by_claim: dict[int, list[Finding]] = {}
    for finding in findings:
        by_claim.setdefault(finding.claim_index, []).append(finding)

    released: list[ReleasedClaim] = []
    withheld: list[WithheldClaim] = []
    for index, claim in enumerate(answer.claims):
        claim_findings = by_claim.get(index, [])
        removals = [f for f in claim_findings if f.severity is Severity.REMOVE]
        if removals:
            withheld.append(WithheldClaim(text=claim.text, findings=tuple(claim_findings)))
            continue
        released.append(
            ReleasedClaim(
                text=claim.text,
                citations=claim.citations,
                disclosures=tuple(
                    f for f in claim_findings if f.severity is Severity.DISCLOSE
                ),
            )
        )

    decision = _decision_of(answer, released=released, withheld=withheld)
    return AnswerDecision(
        decision=decision,
        reason_codes=_reason_codes(answer, released=released, withheld=withheld),
        support_band=_band(released=released, withheld=withheld, degraded=degraded),
        released=tuple(released),
        withheld=tuple(withheld),
        degraded=degraded,
    )


def _decision_of(
    answer: ResolvedAnswer,
    *,
    released: list[ReleasedClaim],
    withheld: list[WithheldClaim],
) -> Decision:
    """Answered, partial or abstained.

    The model's own ``answerable`` is not the decision: a model may say yes and produce nothing,
    or say yes and produce claims the Gate removes. What reaches the reader decides, which is why
    the flag is consulted only when nothing survived.
    """
    if not released:
        return Decision.ABSTAINED
    if withheld:
        return Decision.PARTIAL
    return Decision.ANSWERED


def _reason_codes(
    answer: ResolvedAnswer,
    *,
    released: list[ReleasedClaim],
    withheld: list[WithheldClaim],
) -> tuple[str, ...]:
    """Every code that bore on the outcome, de-duplicated, in a stable order.

    Structural reasons come first, then the finding codes that removed or qualified content. A
    reader scanning the list should see *why there is no answer* before *what was wrong with the
    parts*.
    """
    codes: list[str] = []

    def add(code: str) -> None:
        if code not in codes:
            codes.append(code)

    if not answer.answerable and not released:
        add(REASON_MODEL_REPORTED_UNANSWERABLE)
    if withheld and not released:
        add(REASON_NOTHING_SURVIVED)

    for withheld_claim in withheld:
        for finding in withheld_claim.findings:
            add(finding.code)
    for released_claim in released:
        for finding in released_claim.disclosures:
            add(finding.code)
    return tuple(codes)


def _band(
    *,
    released: list[ReleasedClaim],
    withheld: list[WithheldClaim],
    degraded: tuple[str, ...],
) -> SupportBand:
    """Three checkable facts, not a score.

    Removals weigh heaviest because they mean the model asserted something its own evidence did
    not support, which says something about the whole answer rather than only the removed part.
    """
    if not released:
        return SupportBand.NONE
    if withheld:
        return SupportBand.WEAK

    qualified = any(claim.disclosures for claim in released)
    if qualified and degraded:
        return SupportBand.WEAK
    if qualified or degraded:
        return SupportBand.MODERATE
    return SupportBand.STRONG
