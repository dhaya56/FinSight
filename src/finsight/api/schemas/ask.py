"""Request and response contracts for the answer route.

**The response carries the decision, not just the prose.** A client that receives only claim
text cannot tell an answer from a partial answer, and §27.11 gives every answer exactly one
decision. So ``decision``, ``support_band``, ``reason_codes`` and ``degraded`` are first-class
fields, and a client rendering the text without them is rendering something the server never
claimed.

**What was withheld is part of the response.** A reader who cannot see what the Evidence Gate
removed cannot judge whether what remains is complete, and an omission is indistinguishable from
a model that never said it — which is the more flattering reading and the wrong one.

**``support_band`` is not a probability** (§27.10, §27.13). It is computed from three checkable
facts: whether anything was removed, whether a conflict was disclosed, and whether retrieval or
the model was degraded. Nothing here may be presented as a calibrated likelihood that the answer
is correct.

**Citation text is read from the source representation, never written by the model** (ADR-009).
``ClaimCitationModel.text`` is the stored span the claim was checked against, so a client can
show a reader exactly what the figure rests on.
"""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AskRequest",
    "AskResponse",
    "ClaimCitationModel",
    "EvidencePassageModel",
    "FindingModel",
    "ReleasedClaimModel",
    "WithheldClaimModel",
]

_FORBID_EXTRA = ConfigDict(extra="forbid")
"""Reject unknown request fields rather than ignoring them.

A misspelled filter that is silently dropped widens the evidence instead of narrowing it, so an
answer could rest on passages from outside the scope the caller asked for — a §20.2 violation
that looks exactly like a working request.
"""


class AskRequest(BaseModel):
    """A question and the §20.2 filters the evidence is scoped by."""

    model_config = _FORBID_EXTRA

    question: Annotated[str, Field(min_length=1, max_length=2000)] = Field(
        description="The question, as typed."
    )
    limit: Annotated[int, Field(ge=1, le=25)] = Field(
        default=8,
        description=(
            "Passages considered as evidence. The character budget may admit fewer. "
            "Bounded well below the search route's 50: every passage admitted is prompt "
            "the model must read, and prompt evaluation is the dominant cost (see the "
            "route's own note on latency)."
        ),
    )

    issuer_name: str | None = None
    document_type: str | None = None
    fiscal_period: str | None = None
    fiscal_year: int | None = None
    reporting_basis: str | None = None
    evidence_type: str | None = None
    section: str | None = None


class ClaimCitationModel(BaseModel):
    """One source span a claim rests on."""

    passage_id: int = Field(
        description=(
            "The evidence passage this reference names, matching "
            "EvidencePassageModel.id. Assigned by rank, so 1 is the strongest evidence "
            "wherever it sits in the prompt."
        )
    )
    source_element_id: UUID = Field(
        description="Durable provenance (§14.7). Resolves while the document exists."
    )
    locator: str = Field(description="The address a reader is shown, such as 'p. 12'.")
    text: str = Field(
        description=(
            "The span's verbatim text, read from the source representation. Never "
            "written or transcribed by the model (ADR-009)."
        )
    )


class FindingModel(BaseModel):
    """One problem the Evidence Gate found with one claim."""

    code: str = Field(description="Stable reason code, for a client to branch on.")
    severity: str = Field(description="remove, or disclose.")
    detail: str = Field(description="Plain wording, for a reader to weigh.")


class ReleasedClaimModel(BaseModel):
    """A claim that may be shown, with anything that must be shown beside it."""

    text: str
    citations: tuple[ClaimCitationModel, ...]
    disclosures: tuple[FindingModel, ...] = Field(
        default=(),
        description=(
            "Conflicts that must be rendered alongside this claim (§27.8), such as two "
            "cited passages describing different periods. Not reasons for removal."
        ),
    )


class WithheldClaimModel(BaseModel):
    """A claim the Gate removed, and why.

    Returned rather than dropped: §27.3 removes a claim whose figures its own sources do not
    state, and a client that never sees the removal cannot tell a complete answer from a
    dismantled one.
    """

    text: str = Field(
        description=(
            "The claim as the model wrote it. It was NOT released and must not be "
            "rendered as an answer; it is here so a reader can see what was removed."
        )
    )
    findings: tuple[FindingModel, ...]


class EvidencePassageModel(BaseModel):
    """One passage the model was shown, and everything needed to cite it back."""

    id: int = Field(description="The identifier the model cited, assigned by rank.")
    chunk_id: UUID
    text: str = Field(
        description=(
            "The passage body verbatim as extracted. Not a summary and not enriched "
            "retrieval text: §7 forbids citing either as source evidence."
        )
    )
    heading_path: tuple[str, ...] = ()
    page_numbers: tuple[int, ...] = ()
    evidence_type: str = "narrative"
    issuer_name: str | None = None
    document_type: str | None = None
    fiscal_period: str | None = None
    reporting_basis: str | None = None
    rerank_score: float | None = Field(
        default=None,
        description=(
            "Cross-encoder logit. Null when the reranker did not run. Unbounded, often "
            "negative, and not a probability (§27.13)."
        ),
    )
    expanded: bool = Field(
        default=False,
        description="True when a parent's text replaced a retrieved fragment (§20.8).",
    )
    stands_for: tuple[UUID, ...] = Field(
        default=(),
        description=(
            "The retrieved chunks this passage covers. More than one when several "
            "candidates shared a parent and were merged."
        ),
    )


class AskResponse(BaseModel):
    """One answered, partial or abstained question, with everything behind it."""

    question: str = Field(description="Echoed back, so a stored response stands alone.")

    decision: str = Field(
        description=(
            "answered, partial, or abstained. Exactly one per answer (§27.11). "
            "'partial' means some claims were released and others removed; 'abstained' "
            "means nothing was released, and an empty claims list is then expected "
            "rather than an error."
        )
    )
    support_band: str = Field(
        description=(
            "strong, moderate, weak, or none. A rule over three checkable facts, NOT a "
            "probability that the answer is correct (§27.10, §27.13)."
        )
    )
    reason_codes: tuple[str, ...] = Field(
        default=(),
        description=(
            "Why content was withheld or qualified. Kept separate from 'degraded': the "
            "corpus not holding an answer and the deployment being unhealthy are "
            "different facts calling for different actions (§27.9)."
        ),
    )
    degraded: tuple[str, ...] = Field(
        default=(),
        description=(
            "Degradation flags from retrieval and generation (§20.12, §23.8, §27.9). "
            "Non-empty means this answer is not comparable with an undegraded one."
        ),
    )

    claims: tuple[ReleasedClaimModel, ...] = Field(
        default=(), description="The claims that may be shown, in the model's order."
    )
    withheld: tuple[WithheldClaimModel, ...] = Field(
        default=(), description="Claims the Gate removed. Never render these as answers."
    )
    passages: tuple[EvidencePassageModel, ...] = Field(
        default=(),
        description=(
            "The evidence the answer was composed from, in rank order. Present even "
            "when nothing was released: §26.10's fallback returns the evidence without "
            "prose, and five cited passages are more use than an error."
        ),
    )

    model: str = Field(description="The generation model, recorded on every answer.")
    answer_id: UUID | None = Field(
        default=None,
        description=(
            "The audit record (§31), so a reader can quote it when asking why something "
            "was withheld."
        ),
    )

    evidence_budget_chars: int
    evidence_used_chars: int
    passages_considered: int = Field(
        description="Candidates retrieved before merging and the character budget."
    )
    passages_dropped_for_budget: int = 0
    passages_merged: int = 0
    passages_expanded: int = 0

    prompt_tokens: int = 0
    completion_tokens: int = 0
    timings_ms: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Per-stage server-side wall time. Separated because generation dominates by "
            "an order of magnitude, and one total cannot distinguish a slow index from a "
            "cold model."
        ),
    )

    @property
    def is_released(self) -> bool:
        """Whether any claim reaches the reader."""
        return bool(self.claims)
