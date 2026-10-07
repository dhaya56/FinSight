"""Reading the state of the running system: the index, the outbox, and what it answered.

Three questions, and only the first is the one an operator usually means by "health":

* **is the derived index consistent with the authority?** §29.2 makes Qdrant rebuildable
  from PostgreSQL, which is only reassuring if someone checks that the two agree. A count
  that has silently drifted is the failure the outbox exists to prevent.
* **is anything stuck?** A pending outbox event is work not yet done; a failed one is work
  that will not happen without a retry.
* **what has the system actually been answering?** The audit record holds every decision
  and its reasons. How often it declines, and why, is the question a reader deciding
  whether to trust it should ask first — and nothing surfaced it.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from finsight.domain.representations.retrieval import ChunkRole
from finsight.persistence.tables.answers import Answer
from finsight.persistence.tables.chunks import Chunk, IndexOutbox
from finsight.persistence.tables.documents import DocumentVersion

__all__ = ["AnswerActivity", "IndexState", "OperationsRepository"]


@dataclass(frozen=True, slots=True)
class IndexState:
    """What PostgreSQL believes is indexed, and what work remains."""

    indexed_chunks: int
    """Chunks an active generation sends to the vector index — **children only**.

    Measured, after getting it wrong: parents carry no outbox event at all. They exist for
    §20.8's context expansion and are fetched by identifier, never searched, so comparing
    every chunk against the collection reported a healthy index as drifted by exactly the
    parent count.
    """

    context_chunks: int
    """Parents, stored for expansion and deliberately not indexed."""

    pending_events: int
    failed_events: int
    completed_events: int


@dataclass(frozen=True, slots=True)
class AnswerActivity:
    """What the system has been answering, from the audit record (§31)."""

    total: int
    by_decision: dict[str, int] = field(default_factory=dict)
    by_support_band: dict[str, int] = field(default_factory=dict)
    by_reason: dict[str, int] = field(default_factory=dict)
    """Reason codes by frequency — why content was withheld or an answer declined."""

    median_elapsed_ms: int = 0
    slowest_elapsed_ms: int = 0

    @property
    def released(self) -> int:
        """Answers that put at least one claim in front of a reader."""
        return self.by_decision.get("answered", 0) + self.by_decision.get("partial", 0)


class OperationsRepository:
    """Reads operational state. Read-only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def index_state(self) -> IndexState:
        """Chunk and outbox counts, in one round trip each."""
        by_role = {
            row.role: row.tally
            for row in self._session.execute(
                select(Chunk.role, func.count().label("tally"))
                .join(
                    DocumentVersion,
                    DocumentVersion.active_generation_id == Chunk.generation_id,
                )
                .group_by(Chunk.role)
            )
        }

        # Labelled "tally", not "count": a Row exposes the tuple's own ``count`` method,
        # so ``row.count`` returns a bound method rather than the number — a mistake that
        # types as callable and reads as correct.
        by_state = {
            row.state: row.tally
            for row in self._session.execute(
                select(IndexOutbox.state, func.count().label("tally")).group_by(
                    IndexOutbox.state
                )
            )
        }
        return IndexState(
            indexed_chunks=int(by_role.get(ChunkRole.CHILD.value, 0)),
            context_chunks=int(by_role.get(ChunkRole.PARENT.value, 0)),
            pending_events=int(by_state.get("pending", 0)),
            failed_events=int(by_state.get("failed", 0)),
            completed_events=int(by_state.get("completed", 0)),
        )

    def answer_activity(self) -> AnswerActivity:
        """Decisions, bands, reasons and latency across every recorded answer.

        Percentiles rather than a mean: generation latency is dominated by prompt length
        and a single long answer drags an average somewhere no request actually was.
        """
        total = self._session.execute(
            select(func.count()).select_from(Answer)
        ).scalar_one()
        if not total:
            return AnswerActivity(total=0)

        latency = self._session.execute(
            select(
                func.percentile_cont(0.5).within_group(Answer.elapsed_ms.asc()),
                func.max(Answer.elapsed_ms),
            )
        ).one()

        reasons = self._session.execute(
            select(
                func.unnest(Answer.reason_codes).label("reason"),
                func.count().label("tally"),
            )
            .group_by(func.unnest(Answer.reason_codes))
            .order_by(func.count().desc())
        ).all()

        return AnswerActivity(
            total=int(total),
            by_decision=self._tally(Answer.decision),
            by_support_band=self._tally(Answer.support_band),
            by_reason={row.reason: int(row.tally) for row in reasons},
            median_elapsed_ms=int(latency[0] or 0),
            slowest_elapsed_ms=int(latency[1] or 0),
        )

    def _tally(self, column: InstrumentedAttribute[str]) -> dict[str, int]:
        """Count rows per distinct value of one column."""
        return {
            str(row[0]): int(row[1])
            for row in self._session.execute(
                select(column, func.count()).group_by(column)
            )
        }
