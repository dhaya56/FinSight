"""The answer record against a live PostgreSQL: parity, constraints, and one write.

Two of these exist because the migration is hand-written and nothing else would notice a drift:
``compare_metadata`` reporting zero differences is the only evidence the applied schema and the
model agree, and a CHECK nobody has watched reject anything is a comment rather than a control.

Both caught real faults when first run. Parity found an index the migration created and the model
did not declare; before that, the constraint names were doubled on the model side
(``ck_answers_ck_answers_...``) while the migration created the single-prefixed form.

Every write is rolled back, so the suite leaves no rows behind.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Imported for the side effect: the package registers every table with
# ``Base.metadata``, which is what the parity check below compares against. This used
# to be a hand-maintained list of table modules, and a third copy of the same list.
import finsight.persistence.tables  # noqa: F401
from finsight.generation.decision import (
    AnswerDecision,
    Decision,
    ReleasedClaim,
    SupportBand,
    WithheldClaim,
)
from finsight.generation.resolution import ResolvedCitation
from finsight.generation.validation import (
    REASON_MIXED_PERIOD,
    REASON_UNSUPPORTED_NUMERAL,
    Finding,
    Severity,
)
from finsight.persistence.database import get_engine, session_scope
from finsight.persistence.repositories.answers import AnswerRepository
from finsight.persistence.tables.base import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def session() -> Iterator[Session]:
    """A session whose work is always rolled back."""
    with session_scope() as open_session:
        yield open_session
        open_session.rollback()


VALID_SQL = text(
    """
    INSERT INTO answers (
      question, decision, support_band, model,
      released_claims, withheld_claims, evidence_passages,
      prompt_tokens, completion_tokens, elapsed_ms
    ) VALUES (
      :question, :decision, :support_band, :model,
      :released_claims, :withheld_claims, :evidence_passages, 0, 0, 0
    )
    """
)

VALID = {
    "question": "a question",
    "decision": "answered",
    "support_band": "strong",
    "model": "llama3.1:8b",
    "released_claims": 1,
    "withheld_claims": 0,
    "evidence_passages": 3,
}


def insert(session: Session, **overrides: object) -> None:
    session.execute(VALID_SQL, {**VALID, **overrides})
    session.flush()


class TestSchemaParity:
    def test_the_applied_schema_matches_the_model(self) -> None:
        """Zero differences, or the migration and the model have drifted."""
        with get_engine().connect() as connection:
            context = MigrationContext.configure(connection)
            differences = compare_metadata(context, Base.metadata)

        assert differences == [], f"schema drift: {differences}"


class TestConstraintsReject:
    """Each CHECK, watched rejecting what it claims to."""

    def test_an_unknown_decision(self, session: Session) -> None:
        with pytest.raises(IntegrityError, match="decision_known"):
            insert(session, decision="maybe")

    def test_an_unknown_support_band(self, session: Session) -> None:
        with pytest.raises(IntegrityError, match="support_band_known"):
            insert(session, support_band="excellent")

    def test_abstaining_while_releasing_content(self, session: Session) -> None:
        with pytest.raises(IntegrityError, match="abstention_releases_nothing"):
            insert(session, decision="abstained", released_claims=2)

    def test_answering_while_releasing_nothing(self, session: Session) -> None:
        """The other direction of the same equivalence."""
        with pytest.raises(IntegrityError, match="abstention_releases_nothing"):
            insert(session, released_claims=0, support_band="none")

    def test_a_none_band_with_released_content(self, session: Session) -> None:
        with pytest.raises(IntegrityError, match="band_none_matches_abstention"):
            insert(session, support_band="none")

    def test_an_answered_answer_that_withheld_something(self, session: Session) -> None:
        with pytest.raises(IntegrityError, match="answered_withholds_nothing"):
            insert(session, withheld_claims=1)

    def test_a_partial_answer_that_withheld_nothing(self, session: Session) -> None:
        with pytest.raises(IntegrityError, match="partial_withholds_something"):
            insert(session, decision="partial", withheld_claims=0)

    def test_a_valid_row_is_accepted(self, session: Session) -> None:
        """So the probes above are rejecting their own condition, not everything."""
        insert(session)

    def test_a_released_claim_must_cite_something(self, session: Session) -> None:
        answer_id = session.execute(
            text(
                "INSERT INTO answers (question, decision, support_band, model,"
                " released_claims, withheld_claims, evidence_passages,"
                " prompt_tokens, completion_tokens, elapsed_ms)"
                " VALUES ('q', 'answered', 'strong', 'm', 1, 0, 1, 0, 0, 0)"
                " RETURNING id"
            )
        ).scalar_one()

        with pytest.raises(IntegrityError, match="released_claim_cites_something"):
            session.execute(
                text(
                    "INSERT INTO answer_claims"
                    " (answer_id, position, text, released, cited_source_element_ids)"
                    " VALUES (:answer_id, 0, 'a claim', true, '{}')"
                ),
                {"answer_id": answer_id},
            )
            session.flush()


class TestRecording:
    def test_a_partial_answer_is_written_whole(self, session: Session) -> None:
        element = uuid4()
        decision = AnswerDecision(
            decision=Decision.PARTIAL,
            reason_codes=(REASON_UNSUPPORTED_NUMERAL,),
            support_band=SupportBand.WEAK,
            released=(
                ReleasedClaim(
                    text="A supported claim.",
                    citations=(
                        ResolvedCitation(
                            passage_id=1,
                            source_element_id=element,
                            locator="p. 12",
                            text="span",
                        ),
                    ),
                    disclosures=(
                        Finding(
                            claim_index=0,
                            code=REASON_MIXED_PERIOD,
                            severity=Severity.DISCLOSE,
                            detail="two periods",
                        ),
                    ),
                ),
            ),
            withheld=(
                WithheldClaim(
                    text="An invented figure of 99,999.",
                    findings=(
                        Finding(
                            claim_index=1,
                            code=REASON_UNSUPPORTED_NUMERAL,
                            severity=Severity.REMOVE,
                            detail="not in any cited span",
                        ),
                    ),
                ),
            ),
            degraded=("dense_unavailable",),
        )

        answer_id = AnswerRepository(session).record(
            question="what was revenue?",
            decision=decision,
            model="llama3.1:8b",
            evidence_passages=4,
            prompt_tokens=480,
            completion_tokens=52,
            elapsed_ms=16_000,
        )
        session.flush()

        header = session.execute(
            text(
                "SELECT decision, support_band, reason_codes, degraded,"
                " released_claims, withheld_claims, evidence_passages"
                " FROM answers WHERE id = :id"
            ),
            {"id": answer_id},
        ).one()
        assert header.decision == "partial"
        assert header.support_band == "weak"
        assert header.reason_codes == [REASON_UNSUPPORTED_NUMERAL]
        assert header.degraded == ["dense_unavailable"]
        assert (header.released_claims, header.withheld_claims) == (1, 1)
        assert header.evidence_passages == 4

        claims = session.execute(
            text(
                "SELECT position, released, reason_codes, cited_source_element_ids"
                " FROM answer_claims WHERE answer_id = :id ORDER BY position"
            ),
            {"id": answer_id},
        ).all()
        assert [row.released for row in claims] == [True, False]
        assert claims[0].cited_source_element_ids == [element]
        assert claims[0].reason_codes == [REASON_MIXED_PERIOD]
        assert claims[1].reason_codes == [REASON_UNSUPPORTED_NUMERAL]
        assert claims[1].cited_source_element_ids == []

    def test_an_abstention_records_no_claims_but_a_reason(self, session: Session) -> None:
        decision = AnswerDecision(
            decision=Decision.ABSTAINED,
            reason_codes=("model_reported_unanswerable",),
            support_band=SupportBand.NONE,
            released=(),
            withheld=(),
        )

        answer_id = AnswerRepository(session).record(
            question="what is the chief executive's pay?",
            decision=decision,
            model="llama3.1:8b",
            evidence_passages=5,
        )
        session.flush()

        header = session.execute(
            text("SELECT decision, support_band, reason_codes FROM answers WHERE id = :id"),
            {"id": answer_id},
        ).one()
        count = session.execute(
            text("SELECT count(*) FROM answer_claims WHERE answer_id = :id"),
            {"id": answer_id},
        ).scalar_one()

        assert header.decision == "abstained"
        assert header.support_band == "none"
        assert header.reason_codes == ["model_reported_unanswerable"]
        assert count == 0
