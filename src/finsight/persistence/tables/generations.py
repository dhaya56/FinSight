"""Generations: the unit of reprocessing, and the only thing that gates retrieval.

PROJECT_BLUEPRINT.md §11.12 defines a generation as spanning extraction,
normalization, chunking and index synchronization **for one document version**,
assembled from component runs that each record their own producer, configuration
version and outcome. §20.2 makes it one of the hard filters every retrieval must
apply, so this table is what stands between a half-built index and a reader.

**The distinction this table exists to make.** ``document_versions.current_extraction_run_id``
already points at the elements a stage produced. §11.12 is explicit that such a
pointer is not activation: "a component run completing does not make a generation
active", and §11.13 adds that "the active pointer switches only after required
indexing and validation succeed. Failed shadow runs never replace active evidence."

So there are two pointers with two different jobs:

====================================  =====================================
``current_extraction_run_id``         what **exists** for that stage
``active_generation_id``              what is **queryable**
====================================  =====================================

A stage may complete correctly while the generation it belongs to remains
incomplete. ``documents.py`` reserved the word "active" for exactly this moment.

**Why activation is a database constraint rather than application care.** An
application that forgets to check completeness before flipping the pointer exposes
a partially indexed filing as evidence, and nothing downstream could tell. The
CHECK below makes the unsafe state unrepresentable instead.
"""

import datetime
import uuid
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from finsight.persistence.tables.base import Base

STATE_SHADOW: Final = "shadow"
"""Built, not yet queryable. Every generation starts here (§11.12)."""

STATE_ACTIVE: Final = "active"
"""Indexing and validation succeeded; §20.2 retrieval may see it."""

STATE_SUPERSEDED: Final = "superseded"
"""A later generation took over. Retained: §27.7 keeps old citations resolvable."""

STATE_FAILED: Final = "failed"
"""Abandoned. §11.13: a failed shadow run never replaces active evidence."""

GENERATION_STATES: Final[tuple[str, ...]] = (
    STATE_SHADOW,
    STATE_ACTIVE,
    STATE_SUPERSEDED,
    STATE_FAILED,
)

_STATE_LIST: Final = ", ".join(f"'{state}'" for state in GENERATION_STATES)


class Generation(Base):
    """One reprocessing of one document version, across every stage.

    Assembled from component runs. Extraction is the only stage that exists today,
    so ``extraction_run_id`` is the only component column; chunking and indexing
    runs join it as those stages are built, each by migration. The columns are
    nullable because a generation is created before its components complete — it
    is a container that fills, not a record written at the end.
    """

    __tablename__ = "generations"
    __table_args__ = (
        CheckConstraint(f"state IN ({_STATE_LIST})", name="state_known"),
        CheckConstraint(
            "(state = 'active') = (activated_at IS NOT NULL)",
            name="activated_at_matches_state",
        ),
        CheckConstraint(
            "state <> 'active' OR extraction_run_id IS NOT NULL",
            name="active_requires_components",
        ),
        Index(
            "uq_generations_document_version_id",
            "document_version_id",
            unique=True,
            postgresql_where=sql_text("state = 'active'"),
        ),
    )
    """Three constraints and an index, each refusing a specific wrong state.

    ``activated_at_matches_state`` is an equivalence, not an implication: an active
    generation must carry its activation time, and a non-active one must not. A
    timestamp left behind by a generation that was later superseded would read as
    evidence it is still live.

    ``active_requires_components`` is §11.13 in the schema. A generation with no
    extraction run has no evidence behind it, so activating one would publish an
    empty filing as queryable. More component columns join this constraint as their
    stages arrive, which is the point of writing it as a condition on ``active``
    rather than as a NOT NULL on the column.

    The partial unique index allows **one** active generation per document version
    while leaving any number of shadow, superseded and failed ones. Enforced by the
    database rather than by reading first, for the same reason
    ``uq_extraction_runs_document_version_id`` is: two concurrent activations would
    both read "none active" and both proceed.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=sql_text("uuidv7()")
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False, index=True
    )

    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATE_SHADOW
    )

    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=True
    )
    """The extraction component of this generation (§11.12).

    Nullable because the generation is created first and its components attach as
    they complete. Null on an active generation is refused by
    ``active_requires_components``.
    """

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When this generation became queryable, from the database clock.

    Set with ``clock_timestamp()`` rather than ``now()`` for the reason recorded in
    ``repositories/source.py``: ``now()`` is the transaction start time, which can
    precede a row written moments earlier and make an ordering look impossible.
    """
