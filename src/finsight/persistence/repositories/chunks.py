"""Writing chunks, their sources and their index events in one transaction.

§29.8 requires chunk and index-event records to commit together, and this is the
only place that happens. The reason is not tidiness: writing chunks first and
enqueueing them afterwards leaves a window where a crash produces chunks nothing
will ever index, which are invisible to retrieval and indistinguishable from
chunks that were indexed and found nothing.

**No embedding or vector call belongs in here.** ``session_scope``'s docstring
states the rule and §29.7 is the reason — a transaction must not span a model or
vector call. The outbox rows written here are what let the indexer do that work
outside any transaction.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from finsight.chunking.contracts import Chunk as DerivedChunk
from finsight.domain.representations.retrieval import ChunkRole
from finsight.persistence.tables.chunks import (
    EVENT_PENDING,
    Chunk,
    ChunkSource,
    IndexOutbox,
)


class ChunkRepository:
    """Record and read chunks. Opens no transaction of its own."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        generation_id: UUID,
        document_version_id: UUID,
        chunks: Sequence[DerivedChunk],
        text_search_config: str,
    ) -> int:
        """Write a generation's chunks, their sources and their index events.

        Identifiers are pre-allocated in one round trip rather than returned from
        the insert, which ENV-006 measured at 4,539 cells/s against 1,302 for
        insert-returning. The same reasoning applies here: parents must be
        referenced by their children in the *same* statement batch, and that is
        only possible if the ids exist before the rows do.

        ``text_search_config`` is passed in rather than read from settings, so the
        value that analysed the text is the caller's explicit choice and appears in
        its call site (§9.7).

        Returns the number of chunks written.
        """
        if not chunks:
            return 0

        ids = self._reserve(len(chunks))
        rows: list[dict[str, object]] = []
        sources: list[dict[str, object]] = []
        events: list[dict[str, object]] = []

        for index, chunk in enumerate(chunks):
            chunk_id = ids[index]
            parent_id = (
                ids[chunk.parent_index] if chunk.parent_index is not None else None
            )
            rows.append(
                {
                    "id": chunk_id,
                    "generation_id": generation_id,
                    "document_version_id": document_version_id,
                    "parent_id": parent_id,
                    "ordinal": chunk.ordinal,
                    "role": chunk.role.value,
                    "evidence_type": chunk.evidence_type.value,
                    "text": chunk.text,
                    "heading_path": list(chunk.heading_path),
                    "page_numbers": list(chunk.page_numbers),
                    "token_count": chunk.token_count,
                    "char_count": chunk.char_count,
                    "config_version": chunk.config_version,
                    "notes": list(chunk.notes),
                }
            )
            sources.extend(
                {
                    "chunk_id": chunk_id,
                    "source_element_id": element_id,
                    "position": position,
                }
                for position, element_id in enumerate(
                    _distinct(chunk.source_element_ids)
                )
            )
            # Only children are indexed. A parent repeats its children's text, and
            # embedding both would make a section outrank its own best paragraph
            # on every query that matches it (§18.5 keeps parents for context).
            if chunk.role is ChunkRole.CHILD:
                events.append(
                    {
                        "chunk_id": chunk_id,
                        "generation_id": generation_id,
                        "state": EVENT_PENDING,
                        "attempts": 0,
                    }
                )

        self._session.execute(insert(Chunk), rows)
        self._analyse(ids, text_search_config)
        if sources:
            self._session.execute(insert(ChunkSource), sources)
        if events:
            self._session.execute(insert(IndexOutbox), events)
        return len(rows)

    def _reserve(self, count: int) -> list[UUID]:
        """Allocate identifiers before the rows exist, in one round trip."""
        statement = sql_text("SELECT uuidv7() FROM generate_series(1, :count)")
        return list(
            self._session.execute(statement.bindparams(count=count)).scalars()
        )

    def _analyse(self, ids: Sequence[UUID], config: str) -> None:
        """Fill the lexical vector for the rows just written.

        A second statement rather than a value in the insert, because the
        configuration name is an identifier to ``to_tsvector`` and parameterising
        it as data is the clean way to keep it out of the SQL text. The
        configuration is validated against the catalogue first, so an unknown name
        fails with a clear error instead of a PostgreSQL syntax complaint.
        """
        known = self._session.execute(
            sql_text(
                "SELECT count(*) FROM pg_ts_config WHERE cfgname = :name"
            ).bindparams(name=config)
        ).scalar_one()
        if not known:
            raise UnknownTextSearchConfigError(
                f"PostgreSQL has no text-search configuration named {config!r}"
            )

        self._session.execute(
            sql_text(
                "UPDATE chunks SET lexemes = to_tsvector(CAST(:cfg AS regconfig), text)"
                " WHERE id = ANY(:ids)"
            ).bindparams(cfg=config, ids=list(ids))
        )

    def count_for_generation(self, *, generation_id: UUID) -> int:
        return self._session.execute(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.generation_id == generation_id)
        ).scalar_one()

    def pending_events(self, *, generation_id: UUID, limit: int) -> Sequence[UUID]:
        """Chunks still awaiting indexing, oldest first (§29.10)."""
        return list(
            self._session.execute(
                select(IndexOutbox.chunk_id)
                .where(
                    IndexOutbox.generation_id == generation_id,
                    IndexOutbox.state == EVENT_PENDING,
                )
                .order_by(IndexOutbox.created_at)
                .limit(limit)
            ).scalars()
        )


class UnknownTextSearchConfigError(RuntimeError):
    """The configured text-search configuration does not exist in PostgreSQL.

    Raised rather than falling back to a default. §9.7 makes this a recorded
    choice, and silently analysing a corpus with a different configuration than
    the one requested would produce an index whose queries never match it.
    """


def _distinct(ids: Sequence[UUID]) -> list[UUID]:
    """The element ids in order, without repeats.

    A chunk built from an oversized block that was split carries the same element
    id on every piece, and ``chunk_sources`` has a composite primary key.
    """
    seen: set[UUID] = set()
    ordered: list[UUID] = []
    for element_id in ids:
        if element_id not in seen:
            seen.add(element_id)
            ordered.append(element_id)
    return ordered
