"""Source-representation tables: extraction runs and the elements they produce.

These are the tables every later phase points at. Chunks cite the regions they
were built from (§14.7), citations resolve to a character range inside one
element (§14.9), facts resolve through provenance to a cell or span (§14.8), and
the Evidence Gate checks that a cited element is one the query was permitted to
see (§27.6). All four get the same permanent foreign-key target, which is the
property this schema exists to buy.

Three decisions are worth stating where they can be read next to the columns.

**The hierarchy is format-neutral.** ``source_elements`` is one self-referencing
table, not ``source_pages`` plus ``source_blocks``. A PDF gives page → block; a
workbook gives sheet → cell; HTML gives document → section → block; an XBRL
instance gives instance → fact node. A new format widens the ``element_type``
CHECK by one line and introduces a new ``location`` shape, which needs no
migration at all.

**``location`` holds addresses only.** Semantics arrive as one-to-one extension
tables — ``source_table_cells`` with its header and row-label paths,
``source_xbrl_facts`` with concept, context, unit, scale and sign — so they land
as typed, constrained, indexable columns rather than as untyped JSON keys.

**The pointer is ``current``, not ``active``.** §11.12 makes a *generation* the
unit that stays shadow until indexing succeeds and that §20.2 filters retrieval
on; a generation spans extraction, chunking and indexing, and is assembled from
component runs. An extraction run is one such component. Naming its pointer
"active" would claim a word that gates retrieval, for a pointer that gates
nothing, and would leave two competing activation concepts the moment chunks
carry generation identity.
"""

import datetime
import uuid
from typing import Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from finsight.domain.representations.source import ElementType, ExtractionState
from finsight.persistence.tables.base import Base

FORMAT_PDF: Final = "pdf"

EXTRACTION_FORMATS: Final[tuple[str, ...]] = (FORMAT_PDF,)
"""Formats an extraction run can currently describe.

Text with a CHECK constraint rather than a native enum, for the reason given in
``documents.py``: PostgreSQL enums cannot drop values and are awkward to extend.
"""


def _quoted_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


_FORMAT_LIST: Final = _quoted_list(EXTRACTION_FORMATS)
_STATE_LIST: Final = _quoted_list(tuple(state.value for state in ExtractionState))
_ELEMENT_TYPE_LIST: Final = _quoted_list(tuple(kind.value for kind in ElementType))


class ExtractionRun(Base):
    """One attempt to turn a stored document version into source elements."""

    __tablename__ = "extraction_runs"
    __table_args__ = (
        CheckConstraint(f"format IN ({_FORMAT_LIST})", name="format_known"),
        CheckConstraint(f"state IN ({_STATE_LIST})", name="state_known"),
        CheckConstraint("element_count >= 0", name="element_count_non_negative"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="completed_after_started",
        ),
        Index(
            "uq_extraction_runs_document_version_id",
            "document_version_id",
            "producer_policy",
            "config_version",
            unique=True,
            postgresql_where=sql_text("state <> 'failed'"),
        ),
    )
    """The partial unique index is what makes re-extraction idempotent.

    Without it, idempotency would rest on a prior read, and two workers polling
    the same version would both see "no run" and both extract. Excluding failed
    runs from the index keeps a failed attempt retryable and keeps its diagnostic
    row, which is the evidence for why extraction did not work.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=sql_text("uuidv7()")
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False, index=True
    )

    format: Mapped[str] = mapped_column(String(32), nullable=False)

    producer_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    """Which selection policy orchestrated this run.

    Distinct from an element's ``extraction_method``, which records what actually
    produced that element. One run may route different pages to different
    producers (§12.9), so the policy and the outcome are separate facts.
    """

    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    """The versioned extraction configuration, so a change produces a new run
    rather than silently altering the meaning of stored elements (§35)."""

    state: Mapped[str] = mapped_column(String(32), nullable=False)
    element_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SourceElement(Base):
    """One structural element of a document version, exactly as extracted."""

    __tablename__ = "source_elements"
    __table_args__ = (
        CheckConstraint(
            f"element_type IN ({_ELEMENT_TYPE_LIST})", name="element_type_known"
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint(
            "text IS NULL OR failure_reason IS NULL",
            name="text_or_failure_reason",
        ),
        CheckConstraint(
            "(text IS NULL AND char_count IS NULL)"
            " OR (text IS NOT NULL AND char_count IS NOT NULL"
            " AND char_count = char_length(text))",
            name="char_count_matches_text",
        ),
        CheckConstraint("jsonb_typeof(location) = 'object'", name="location_is_object"),
        Index(
            "ix_source_elements_extraction_run_id",
            "extraction_run_id",
            "parent_id",
            "ordinal",
        ),
    )
    """``char_count_matches_text`` is deliberately not a trust exercise.

    A stored count that disagrees with its text would corrupt every offset-based
    citation built on it, so the database recomputes the length rather than
    accepting the application's word for it.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=sql_text("uuidv7()")
    )
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_elements.id"), nullable=True
    )
    """The containing element: a block's page, a cell's sheet. NULL at the root."""

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    """Position among siblings, fixing reading order as the producer determined it.

    Reading order is a producer decision, not a fact about the file, and
    multi-column layouts defeat every candidate library. Recording it as data
    means a better producer can disagree in a later run without rewriting this
    one.
    """

    element_type: Mapped[str] = mapped_column(String(32), nullable=False)

    extraction_method: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_method_version: Mapped[str] = mapped_column(String(64), nullable=False)

    locator: Mapped[str] = mapped_column(String(255), nullable=False)
    """The human-readable citation address: ``p. 12``, ``Sheet1!B7``, ``§3.2``.

    Stored rather than derived, because deriving it would require every consumer
    to know each format's addressing rules, and a citation shown to a reader must
    not change shape when a rendering helper is refactored.
    """

    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Verbatim extracted text. Never normalised in place — citations are
    character offsets into this exact string (§14.9). NULL for a container
    element, which holds no text of its own, and for a failed element."""

    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Why this element has no text, recorded as a coverage gap (§11.11).

    A page that could not be read is a row, not a missing row. Silence would be
    indistinguishable from a blank page, and §27 has to tell a reader that a
    region was not searched rather than implying it held nothing.
    """

    location: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    """Where this element sits in its source. An address, never a claim."""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
