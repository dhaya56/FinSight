"""The record of what was answered, and why anything was withheld (§27.11, §31).

**Without this, a refusal has no history.** The Evidence Gate decides every release, and a
decision nobody can look up later is indistinguishable from the system having had nothing to
say. "Why did it decline that question in October" has to be answerable.

**The decision rules are enforced here, not only in code.** `_decision_of` says an abstention
releases nothing, an answered answer withholds nothing, and a partial does both — so CHECK
constraints say the same, and a row that contradicts the rules cannot be written. A constraint
is the only form of invariant that survives a refactor nobody tested.

**Citations are stored as source element ids, not passage numbers.** A passage number is an
ephemeral label for one answer's evidence set, and that set is not persisted here; recording
``[2]`` without it would record a label with no referent. A source element id is durable
provenance (§14.7) and resolves to a region of a document for as long as the document exists.

**The question is user input.** It is stored because §31 requires the query on the audit record,
and it must not be logged, exported, or included in any telemetry: §10 forbids exposing user
questions, and storing one for audit is not the same as emitting it.
"""

import datetime
import uuid

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from finsight.persistence.tables.base import Base

DECISION_ANSWERED = "answered"
DECISION_PARTIAL = "partial"
DECISION_ABSTAINED = "abstained"
DECISIONS = (DECISION_ANSWERED, DECISION_PARTIAL, DECISION_ABSTAINED)

BAND_STRONG = "strong"
BAND_MODERATE = "moderate"
BAND_WEAK = "weak"
BAND_NONE = "none"
BANDS = (BAND_STRONG, BAND_MODERATE, BAND_WEAK, BAND_NONE)


class Answer(Base):
    """One released, partial or abstained answer."""

    __tablename__ = "answers"
    __table_args__ = (
        # Names carry no ``ck_answers_`` prefix: NAMING_CONVENTION adds it. Including it
        # here produced ``ck_answers_ck_answers_...`` in the model while the migration,
        # which uses ``op.f`` to mark a name final, created the single-prefixed form — a
        # mismatch that only a metadata comparison would have caught.
        CheckConstraint(
            "decision IN ('answered', 'partial', 'abstained')",
            name="decision_known",
        ),
        CheckConstraint(
            "support_band IN ('strong', 'moderate', 'weak', 'none')",
            name="support_band_known",
        ),
        CheckConstraint(
            "(decision = 'abstained') = (released_claims = 0)",
            name="abstention_releases_nothing",
        ),
        CheckConstraint(
            "(support_band = 'none') = (released_claims = 0)",
            name="band_none_matches_abstention",
        ),
        CheckConstraint(
            "decision <> 'answered' OR withheld_claims = 0",
            name="answered_withholds_nothing",
        ),
        CheckConstraint(
            "decision <> 'partial' OR withheld_claims > 0",
            name="partial_withholds_something",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.uuidv7()
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    """Indexed because an audit read is "what was answered recently", never "by id"."""

    question: Mapped[str] = mapped_column(Text, nullable=False)
    """The question as asked. User input: never log or export it (§10)."""

    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    support_band: Mapped[str] = mapped_column(String(16), nullable=False)

    reason_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default="{}"
    )
    degraded: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default="{}"
    )
    """Degradation flags, kept apart from reason codes (§27.9).

    A reader told only "abstained" cannot tell an unhealthy deployment from a corpus that does
    not hold the answer, and the two call for different actions.
    """

    model: Mapped[str] = mapped_column(String(128), nullable=False)

    released_claims: Mapped[int] = mapped_column(Integer, nullable=False)
    withheld_claims: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_passages: Mapped[int] = mapped_column(Integer, nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AnswerClaim(Base):
    """One claim, released or withheld, with the reasons that decided it."""

    __tablename__ = "answer_claims"
    __table_args__ = (
        # Named in full: the ``uq`` convention interpolates only the first column, so an
        # unnamed two-column constraint would be ``uq_answer_claims_answer_id`` and read as
        # though a claim were unique per answer.
        UniqueConstraint(
            "answer_id", "position", name="uq_answer_claims_answer_id_position"
        ),
        CheckConstraint(
            "NOT released OR cardinality(cited_source_element_ids) > 0",
            name="released_claim_cites_something",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.uuidv7()
    )
    answer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("answers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    """The claim's place in the model's output, so the order it was written survives."""

    text: Mapped[str] = mapped_column(Text, nullable=False)
    released: Mapped[bool] = mapped_column(Boolean, nullable=False)

    reason_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default="{}"
    )
    """Why this claim was withheld, or what was disclosed alongside it."""

    cited_source_element_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), nullable=False, server_default="{}"
    )
    """Durable provenance. A withheld claim may legitimately have none."""
