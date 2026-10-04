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
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from finsight.chunking.contracts import Chunk as DerivedChunk
from finsight.domain.representations.retrieval import ChunkRole
from finsight.persistence.tables.chunks import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_PENDING,
    Chunk,
    ChunkSource,
    IndexOutbox,
)
from finsight.persistence.tables.document_metadata import DocumentMetadata


@dataclass(frozen=True, slots=True)
class PendingChunk:
    """One chunk awaiting indexing, with everything the indexer needs.

    Read in a single join rather than a query per chunk. The metadata fields come
    from ``document_metadata`` and are NULL for anything ingested outside the
    corpus, which the indexer handles by omitting them from the filter payload
    rather than inventing a value.
    """

    chunk_id: UUID
    document_version_id: UUID
    text: str
    lexemes: str | None
    """The analysed form. ``None`` means never analysed — a pipeline fault — while
    ``''`` means analysed to no terms, which is legitimate."""

    heading_path: tuple[str, ...]
    page_numbers: tuple[int, ...]
    evidence_type: str

    issuer_name: str | None = None
    document_type: str | None = None
    fiscal_period: str | None = None
    reporting_basis: str | None = None
    currency: str | None = None


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

    def pending_chunks(
        self, *, generation_id: UUID, limit: int
    ) -> list[PendingChunk]:
        """Pending chunks with their text, lexemes and document context.

        One query with two joins, rather than a list of ids followed by a read per
        chunk. The indexer processes the whole corpus in batches, so a per-chunk
        round trip would add one to every chunk — 4,816 of them for the development
        split alone.

        The metadata join is an outer join on purpose: a chunk whose document has no
        recorded issuer must still be indexed, with the filter fields absent rather
        than the chunk missing.
        """
        statement = (
            select(
                Chunk.id,
                Chunk.document_version_id,
                Chunk.text,
                Chunk.lexemes,
                Chunk.heading_path,
                Chunk.page_numbers,
                Chunk.evidence_type,
                DocumentMetadata.issuer_name,
                DocumentMetadata.document_type,
                DocumentMetadata.fiscal_period,
                DocumentMetadata.reporting_basis,
                DocumentMetadata.currency,
            )
            .join(IndexOutbox, IndexOutbox.chunk_id == Chunk.id)
            .outerjoin(
                DocumentMetadata,
                DocumentMetadata.document_version_id == Chunk.document_version_id,
            )
            .where(
                IndexOutbox.generation_id == generation_id,
                IndexOutbox.state == EVENT_PENDING,
            )
            .order_by(Chunk.ordinal)
            .limit(limit)
        )
        return [
            PendingChunk(
                chunk_id=row.id,
                document_version_id=row.document_version_id,
                text=row.text,
                # ``lexemes`` is TSVECTOR; SQLAlchemy hands it back as the text
                # form, which is what the BM25 parser reads. The cast is explicit
                # so a driver that returned a richer object fails here rather than
                # producing an empty sparse vector for every chunk.
                lexemes=None if row.lexemes is None else str(row.lexemes),
                heading_path=tuple(row.heading_path or ()),
                page_numbers=tuple(row.page_numbers or ()),
                evidence_type=row.evidence_type,
                issuer_name=row.issuer_name,
                document_type=row.document_type,
                fiscal_period=row.fiscal_period,
                reporting_basis=row.reporting_basis,
                currency=row.currency,
            )
            for row in self._session.execute(statement)
        ]

    def event_count(self, *, generation_id: UUID) -> int:
        """How many chunks this generation queued for indexing.

        The denominator for reconciliation (§29.11). Counted from the outbox rather
        than from ``chunks``, because only children are queued — a parent repeats
        its children's text and is deliberately not indexed.
        """
        return self._session.execute(
            select(func.count())
            .select_from(IndexOutbox)
            .where(IndexOutbox.generation_id == generation_id)
        ).scalar_one()

    def completed_event_count(self, *, generation_id: UUID) -> int:
        return self._session.execute(
            select(func.count())
            .select_from(IndexOutbox)
            .where(
                IndexOutbox.generation_id == generation_id,
                IndexOutbox.state == EVENT_COMPLETED,
            )
        ).scalar_one()

    def complete_events(self, *, chunk_ids: Sequence[UUID]) -> None:
        """Record that these chunks reached the index (§29.10).

        ``clock_timestamp()`` rather than ``now()``, for the reason recorded on
        ``GenerationRepository.activate``: ``now()`` is the transaction's start
        time, and a completion stamped before the upsert it describes reads as
        though the work happened out of order.
        """
        if not chunk_ids:
            return
        self._session.execute(
            update(IndexOutbox)
            .where(IndexOutbox.chunk_id.in_(list(chunk_ids)))
            .values(
                state=EVENT_COMPLETED,
                completed_at=func.clock_timestamp(),
                failure_reason=None,
                attempts=IndexOutbox.attempts + 1,
            )
        )

    def fail_events(self, *, chunk_ids: Sequence[UUID], reason: str) -> None:
        """Record that these chunks could not be indexed, and why.

        Marked failed rather than left pending. A permanent fault — a chunk the
        model refuses as too long — would otherwise be retried by every subsequent
        run forever, and the ``attempts`` column exists to make that visible rather
        than to permit it. :meth:`reset_failed_events` is how a retry is asked for.

        ``completed_at`` is set even though nothing completed. The
        ``completed_at_matches_state`` CHECK ties a NULL timestamp to ``pending``
        exactly, so the column means "no longer awaiting work" rather than
        "succeeded", and a failed event has to carry one. Worth knowing before
        reading the column as a success time.
        """
        if not chunk_ids:
            return
        self._session.execute(
            update(IndexOutbox)
            .where(IndexOutbox.chunk_id.in_(list(chunk_ids)))
            .values(
                state=EVENT_FAILED,
                completed_at=func.clock_timestamp(),
                failure_reason=reason[:128],
                attempts=IndexOutbox.attempts + 1,
            )
        )

    def reset_failed_events(self, *, generation_id: UUID) -> int:
        """Return a generation's failed events to pending, for a retry.

        Separate from the indexer so retrying is an explicit operator action. An
        indexer that silently reset failures on every run would turn the
        ``attempts`` count into noise and hide a chunk that can never be indexed.
        """
        reset = self._session.execute(
            update(IndexOutbox)
            .where(
                IndexOutbox.generation_id == generation_id,
                IndexOutbox.state == EVENT_FAILED,
            )
            .values(state=EVENT_PENDING, completed_at=None, failure_reason=None)
            .returning(IndexOutbox.chunk_id)
        ).scalars()
        return len(list(reset))


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
