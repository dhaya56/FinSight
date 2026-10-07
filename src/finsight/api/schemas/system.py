"""Operational state: dependencies, index consistency, and what the system has answered.

**Authenticated, unlike the health probes.** §28.10 keeps per-dependency detail behind a
credential: readiness answers "can this serve" to anyone, including an orchestrator with no
token, while *which* component is down is information about the deployment.

The field worth reading twice is ``index_consistent``. §29.2 makes Qdrant a derived,
rebuildable index — which is only reassuring if someone checks that it still agrees with the
authority. A silent drift between PostgreSQL and the vector store is exactly the failure the
transactional outbox exists to prevent, so the comparison is reported rather than assumed.
"""

from pydantic import BaseModel, Field

__all__ = ["AnswerActivityModel", "DependencyModel", "IndexStateModel", "SystemResponse"]


class DependencyModel(BaseModel):
    """One classified dependency and whether it answered."""

    name: str
    classification: str = Field(
        description=(
            "essential, degradable or optional. **Essential** failing takes the service "
            "out of readiness; **degradable** does not, because losing it has a defined "
            "fallback — losing Qdrant costs dense retrieval, not retrieval."
        )
    )
    healthy: bool


class IndexStateModel(BaseModel):
    """What PostgreSQL holds, what Qdrant holds, and whether they agree."""

    indexed_chunks: int = Field(
        description=(
            "Chunks an active generation sends to the vector index — **children only**. "
            "Parents are stored for §20.8's context expansion and fetched by identifier, "
            "never searched, so they are not in the collection and must not be counted "
            "against it."
        )
    )
    context_chunks: int = Field(
        default=0, description="Parents, stored for expansion and deliberately unindexed."
    )
    index_points: int | None = Field(
        default=None,
        description="Points in the vector collection. Null when Qdrant was unreachable.",
    )
    index_consistent: bool | None = Field(
        default=None,
        description=(
            "Whether the derived index matches the authority. Null when Qdrant could not "
            "be reached, which is not the same as inconsistent."
        ),
    )

    pending_events: int = Field(
        default=0, description="Outbox work not yet done (§29.8)."
    )
    failed_events: int = Field(
        default=0,
        description="Work that will not happen without a retry: `index --retry`.",
    )
    completed_events: int = 0


class AnswerActivityModel(BaseModel):
    """What the system has been answering, from the audit record (§31).

    **How often it declines, and why, is the trust question.** A system that always answers
    is not more trustworthy than one that sometimes refuses; it is less, because the
    refusals are where the grounding is doing its work.
    """

    total: int = 0
    by_decision: dict[str, int] = Field(
        default_factory=dict, description="answered, partial, abstained."
    )
    by_support_band: dict[str, int] = Field(
        default_factory=dict,
        description="A rule over what survived, never a correctness probability (§27.13).",
    )
    by_reason: dict[str, int] = Field(
        default_factory=dict,
        description="Reason codes by frequency — why content was withheld or declined.",
    )
    median_elapsed_ms: int = 0
    slowest_elapsed_ms: int = Field(
        default=0,
        description="Percentiles rather than a mean: one long answer drags an average "
        "somewhere no request actually was.",
    )


class SystemResponse(BaseModel):
    """Everything the operations surface needs, in one call."""

    dependencies: tuple[DependencyModel, ...] = ()
    index: IndexStateModel
    answers: AnswerActivityModel
