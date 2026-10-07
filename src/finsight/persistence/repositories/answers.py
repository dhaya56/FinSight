"""Writing the answer record (§27.11, §31).

One transaction per answer, header and claims together. A header without its claims would record
that something was withheld and lose what, which is the half of the record that makes it worth
keeping.

There is no update and no delete. An answer is a statement made at a time; revising the record of
it would make the audit trail describe something that never happened, and §29.12 reserves removal
for tombstoning as it does everywhere else.
"""

from collections.abc import Iterable, Sequence
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.orm import Session

from finsight.generation.decision import AnswerDecision
from finsight.persistence.tables.answers import Answer, AnswerClaim

__all__ = ["AnswerRepository"]


class AnswerRepository:
    """Records answers. Write-only by design."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        question: str,
        decision: AnswerDecision,
        model: str,
        evidence_passages: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        elapsed_ms: int = 0,
    ) -> UUID:
        """Write one answer and its claims, returning the answer's identifier.

        The identifier is returned to the caller so a reader can quote it when asking why
        something was withheld — a decision nobody can reference is one nobody can question.
        """
        answer_id = self._session.execute(
            insert(Answer)
            .values(
                question=question,
                decision=decision.decision.value,
                support_band=decision.support_band.value,
                reason_codes=list(decision.reason_codes),
                degraded=list(decision.degraded),
                model=model,
                released_claims=len(decision.released),
                withheld_claims=len(decision.withheld),
                evidence_passages=evidence_passages,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_ms=elapsed_ms,
            )
            .returning(Answer.id)
        ).scalar_one()

        rows = list(self._claim_rows(answer_id, decision))
        if rows:
            self._session.execute(insert(AnswerClaim), rows)
        return answer_id

    def _claim_rows(
        self, answer_id: UUID, decision: AnswerDecision
    ) -> Sequence[dict[str, object]]:
        """Released claims first, then withheld, numbered in that order.

        ``position`` is the order within the record rather than the model's original index: the
        released set is what a reader reads, and renumbering keeps the stored order and the
        rendered order the same. The original index is recoverable from nothing and is not worth
        a column — what matters is which claims were released and which were not.
        """
        rows: list[dict[str, object]] = []
        for claim in decision.released:
            rows.append(
                {
                    "answer_id": answer_id,
                    "position": len(rows),
                    "text": claim.text,
                    "released": True,
                    "reason_codes": [
                        finding.code for finding in claim.disclosures
                    ],
                    "cited_source_element_ids": _distinct(
                        citation.source_element_id for citation in claim.citations
                    ),
                }
            )
        for withheld in decision.withheld:
            rows.append(
                {
                    "answer_id": answer_id,
                    "position": len(rows),
                    "text": withheld.text,
                    "released": False,
                    "reason_codes": _distinct(
                        finding.code for finding in withheld.findings
                    ),
                    "cited_source_element_ids": [],
                }
            )
        return rows


def _distinct[Value](values: Iterable[Value]) -> list[Value]:
    """Preserve order, drop repeats.

    A claim citing one passage twice, or carrying two findings with the same code, would
    otherwise store the same value twice and make a count of reasons misleading.
    """
    seen: list[Value] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen
