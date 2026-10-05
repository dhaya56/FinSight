"""Chunks: the retrieval representation, and the events that index them.

Three tables that are written together and must stay together.

``chunks`` holds the text retrieval searches. It is **not** evidence — §14.1 keeps
that role with the source representation — which is why ``chunk_sources`` exists:
§14.7 requires every chunk to identify the source regions it was built from, and a
chunk that cannot name them cannot be cited (§14.9).

``index_outbox`` is §29.8: "chunk and index-event records commit together." The
alternative — write chunks, then call an embedding model and a vector store — would
either hold a transaction open across a network call, which §29.7 forbids, or risk
committing chunks that are never indexed and so never retrievable. The outbox makes
the handoff a row rather than a hope.

**The lexical vector is written, not generated.** A PostgreSQL generated column
would bake the text-search configuration into the schema, and §9.7 is explicit that
the configuration, how per-document language is determined, the index type and any
field weighting are "chosen with recorded evidence when the lexical index is built,
not assumed when it is first written." Writing it from the chunking stage means
changing that choice is a re-chunk under a new generation — the §18.11
shadow-generation mechanism — rather than a migration.
"""

import datetime
import uuid
from typing import Final

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from finsight.domain.representations.retrieval import ChunkRole, EvidenceType
from finsight.persistence.tables.base import Base

EVENT_PENDING: Final = "pending"
EVENT_COMPLETED: Final = "completed"
EVENT_FAILED: Final = "failed"

OUTBOX_STATES: Final[tuple[str, ...]] = (
    EVENT_PENDING,
    EVENT_COMPLETED,
    EVENT_FAILED,
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


_ROLE_LIST: Final = _quoted(tuple(role.value for role in ChunkRole))
_TYPE_LIST: Final = _quoted(tuple(kind.value for kind in EvidenceType))
_EVENT_LIST: Final = _quoted(OUTBOX_STATES)


class Chunk(Base):
    """One unit of retrieval text belonging to one generation."""

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint(f"role IN ({_ROLE_LIST})", name="role_known"),
        CheckConstraint(f"evidence_type IN ({_TYPE_LIST})", name="evidence_type_known"),
        CheckConstraint("length(text) > 0", name="text_not_blank"),
        CheckConstraint(
            "char_count = char_length(text)", name="char_count_matches_text"
        ),
        CheckConstraint("token_count >= 0", name="token_count_non_negative"),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint(
            "array_position(heading_path, NULL) IS NULL",
            name="heading_path_has_no_nulls",
        ),
        CheckConstraint(
            "parent_id IS NULL OR role = 'child'", name="only_a_child_has_a_parent"
        ),
        Index("ix_chunks_generation_id", "generation_id", "ordinal"),
        Index(
            "ix_chunks_lexemes",
            "lexemes",
            postgresql_using="gin",
        ),
    )
    """``char_count_matches_text`` repeats the rule ``source_elements`` already has.

    A stored count that disagrees with its text would corrupt every budget decision
    built on it, so the database recomputes rather than trusting the application.

    ``only_a_child_has_a_parent`` catches an inversion that would otherwise be
    silent: a parent pointing at a parent builds a cycle that §20.8's expansion
    would follow forever.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=sql_text("uuidv7()")
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generations.id"), nullable=False
    )
    """The generation this chunk belongs to (§18.9).

    Not nullable, and the reason is §20.2: generation is a hard retrieval filter,
    so a chunk that belonged to none could never be correctly included or excluded.
    """

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False, index=True
    )
    """Denormalised from the generation so §20.2's document-scope filter and the
    issuer join do not need a second hop on every query."""

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id"), nullable=True
    )
    """The broader unit this child belongs to (§18.5), or NULL on a parent."""

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    """Position in document order, so reading order survives into retrieval."""

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    """``narrative`` or ``table_derived`` (§18.4), which §20.5 allocates on.

    ``narrative`` means "not known to be table-derived", never "known to be prose":
    it is derived from overlap with a region the detector found, and ADR-003
    measures that detector at 2 of 6 regions bounded correctly.
    """

    text: Mapped[str] = mapped_column(Text, nullable=False)
    """The chunk verbatim, as assembled from its blocks.

    No enrichment is folded in. §14.4 forbids presenting enriched text as original
    evidence, and the cheapest way to honour that is for the stored text to remain
    comparable to the source it came from. Deterministic context lives in the
    columns beside it and is prepended by whatever consumes the chunk.
    """

    lexemes: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    """The analysed form, for lexical retrieval and as the source of sparse vectors.

    Written by the chunking stage using a configured text-search configuration, not
    generated by the database — see the module docstring. NULL only if a chunk was
    written before the lexical path existed, which no migration path produces.
    """

    heading_path: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]")
    )
    """Section titles outermost first (§18.2), empty where none was recognised.

    Empty means "no heading found above this", never "belongs to no section". The
    heading rule is biased to precision and misses real headings by design.
    """

    page_numbers: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default=sql_text("'{}'::integer[]")
    )

    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)

    config_version: Mapped[str] = mapped_column(String(32), nullable=False)
    """Which chunking configuration produced this chunk (§18.10, §14.10).

    Without it two chunk populations built under different sizes are
    indistinguishable in one index, and no comparison between them is possible.
    """

    notes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]")
    )
    """Machine-readable facts about how the chunk was formed.

    ``split_oversize_block`` means a sentence may straddle this chunk and the next,
    which is otherwise an inexplicable retrieval miss.
    """

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChunkSource(Base):
    """Which source regions a chunk was built from (§14.7, §18.9).

    A separate table rather than an array of identifiers, because this is the
    relation citations traverse: §14.9 resolves a citation to exact source regions,
    and an array cannot be joined, indexed from the element side, or constrained by
    a foreign key. "Which chunks cite this block?" is a question the Evidence Gate
    will ask.
    """

    __tablename__ = "chunk_sources"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        Index("ix_chunk_sources_source_element_id", "source_element_id"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id"), primary_key=True
    )
    source_element_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_elements.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    """Order within the chunk, so the text can be related back to its blocks.

    A chunk joins its blocks in document order; without the position that ordering
    is lost and a citation can say which blocks but not which came first.
    """


class IndexOutbox(Base):
    """One chunk awaiting indexing, committed with the chunk itself (§29.8)."""

    __tablename__ = "index_outbox"
    __table_args__ = (
        CheckConstraint(f"state IN ({_EVENT_LIST})", name="state_known"),
        CheckConstraint(
            "(state = 'pending') = (completed_at IS NULL)",
            name="completed_at_matches_state",
        ),
        Index(
            "ix_index_outbox_pending",
            "generation_id",
            postgresql_where=sql_text("state = 'pending'"),
        ),
    )
    """The partial index is what makes "what is left to index?" cheap.

    A completed generation's events are retained — §29.10 has the indexer *record*
    completion, and deleting the evidence that indexing happened would make a
    reconciliation (§29.11) unable to tell a finished generation from one that
    never started.
    """

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id"), primary_key=True
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generations.id"), nullable=False
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EVENT_PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """How many times indexing has been tried.

    Recorded so a chunk that fails repeatedly is visible rather than retried
    forever in silence.
    """

    failure_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
