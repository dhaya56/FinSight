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

import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, aliased

from finsight.chunking.contracts import Chunk as DerivedChunk
from finsight.domain.representations.retrieval import ChunkRole
from finsight.lexical.normalise import for_analysis
from finsight.persistence.tables.chunks import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_PENDING,
    Chunk,
    ChunkSource,
    IndexOutbox,
)
from finsight.persistence.tables.document_metadata import DocumentMetadata
from finsight.persistence.tables.documents import DocumentVersion
from finsight.persistence.tables.source import SourceElement


@dataclass(frozen=True, slots=True)
class Citation:
    """One source region a chunk was built from (§14.7).

    ``locator`` is the address shown to a reader — ``p. 12``, ``Sheet1!B7``. Stored on
    the element rather than derived here, so a change to how addresses are rendered
    cannot silently change what a citation claims.
    """

    source_element_id: UUID
    locator: str
    position: int


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

    period_end: datetime.date | None = None
    """The period's closing date, which is what makes periods orderable.

    Carried so the indexer can put a *year* in the filter payload.
    ``fiscal_period`` is text as the document states it and two filings in the
    development corpus share one string, so it cannot express "the last three
    years" or even reliably separate two issuers' filings.
    """


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
        self._analyse(ids, [chunk.text for chunk in chunks], text_search_config)
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

    def _analyse(
        self, ids: Sequence[UUID], bodies: Sequence[str], config: str
    ) -> None:
        """Fill the lexical vector for the rows just written.

        A second statement rather than a value in the insert, because the
        configuration name is an identifier to ``to_tsvector`` and parameterising
        it as data is the clean way to keep it out of the SQL text. The
        configuration is validated against the catalogue first, so an unknown name
        fails with a clear error instead of a PostgreSQL syntax complaint.

        **The analysed string is not the stored text.** It is the stored text with
        comma-grouped figures joined (``finsight.lexical.normalise``), because
        PostgreSQL's parser splits ``10,000`` into ``10`` and ``000`` and no
        configuration can prevent it. The normalised form is passed in rather than
        computed in SQL for a plain reason: PostgreSQL's ``regexp_replace`` is POSIX
        and has no lookaround, so the rule cannot be expressed there — and keeping
        it in one Python function is what lets the query path apply exactly the same
        rule.

        One statement for the batch, joining against ``unnest``, so a 2,000-chunk
        generation costs one round trip rather than one per chunk.
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
                "UPDATE chunks SET lexemes ="
                " to_tsvector(CAST(:cfg AS regconfig), source.body)"
                " FROM unnest(CAST(:ids AS uuid[]), CAST(:bodies AS text[]))"
                " AS source(id, body)"
                " WHERE chunks.id = source.id"
            ).bindparams(
                cfg=config,
                ids=list(ids),
                bodies=[for_analysis(body) for body in bodies],
            )
        )

    def analyse_query(self, *, query: str, text_search_config: str) -> str:
        """Analyse a query exactly as the stored lexemes were analysed.

        **This exists so the two sides cannot drift.** The stored lexemes are
        ``to_tsvector(config, for_analysis(text))``, and a query analysed any other
        way silently stops matching: a document holding ``10000`` is unreachable by a
        query for ``10,000`` unless the same normalisation ran on both. There is no
        error to raise when that happens — searches simply return less — so the
        defence is that one method produces both.

        Returns the tsvector's text form, which is what
        :func:`finsight.lexical.bm25.query_vector` reads.

        The configuration is the caller's explicit choice for the same reason it is
        in :meth:`record`: §9.7 makes it a recorded decision, and a query analysed
        under a different configuration than the corpus matches nothing.
        """
        return str(
            self._session.execute(
                sql_text(
                    "SELECT to_tsvector(CAST(:cfg AS regconfig), :body)::text"
                ).bindparams(cfg=text_search_config, body=for_analysis(query))
            ).scalar_one()
        )

    def search_full_text(
        self,
        *,
        query: str,
        text_search_config: str,
        generations: Sequence[UUID],
        limit: int,
        issuer_name: str | None = None,
        document_type: str | None = None,
        fiscal_period: str | None = None,
        reporting_basis: str | None = None,
        evidence_type: str | None = None,
        section: str | None = None,
        document_version_id: UUID | None = None,
        fiscal_year_low: int | None = None,
        fiscal_year_high: int | None = None,
    ) -> list[tuple[UUID, float]]:
        """§20.12's degradation path: lexical retrieval without Qdrant.

        ADR-005 makes BM25 the primary lexical route and keeps this one so that
        losing Qdrant costs dense retrieval rather than *all* retrieval. Until this
        existed the claim was aspirational — the GIN index was populated and nothing
        queried it.

        **This is not BM25 and must not be presented as it.** ``ts_rank_cd`` weights
        by term position and cover density: no term-frequency saturation, no document
        length normalisation, no inverse document frequency. On the same corpus it
        will order results differently, which is why the result carries a degradation
        flag rather than passing silently as an equivalent.

        **The filters are applied in SQL, not afterwards.** Filtering a top-k list
        after ranking is the post-filtering anti-pattern: a query restricted to one
        issuer would rank across the whole corpus, then discard most of what it
        found, and return far fewer than ``limit`` or nothing at all.

        Only children are searched. Parents repeat their children's text and are not
        indexed by the primary path either, so including them here would make the
        fallback return a different *population* from the retriever it stands in for.

        ``websearch_to_tsquery`` rather than ``plainto_tsquery``, so a quoted phrase
        and a negated term behave as a reader expects instead of being flattened to a
        conjunction of every word.
        """
        if not generations:
            # No active generation means nothing a reader may see (§11.13). An
            # unbounded query here would serve superseded evidence.
            return []

        # The predicate is assembled from only the filters that were supplied, from
        # a fixed set of literal fragments. Two reasons, and the first is not
        # optional: a ``:param IS NULL OR col = :param`` form leaves PostgreSQL
        # unable to infer the type of an unbound NULL and the statement fails to
        # prepare. The second is that an always-true OR per unused filter is
        # needless work for the planner on every query.
        conditions = [
            "c.generation_id = ANY(CAST(:generations AS uuid[]))",
            "c.role = 'child'",
            "c.lexemes @@ websearch_to_tsquery(CAST(:cfg AS regconfig), :query)",
        ]
        values: dict[str, object] = {
            "cfg": text_search_config,
            "query": for_analysis(query),
            "generations": [str(generation) for generation in generations],
            "limit": limit,
        }
        for fragment, name, value in (
            ("m.issuer_name = :issuer", "issuer", issuer_name),
            ("m.document_type = :document_type", "document_type", document_type),
            ("m.fiscal_period = :fiscal_period", "fiscal_period", fiscal_period),
            ("m.reporting_basis = :basis", "basis", reporting_basis),
            ("c.evidence_type = :evidence_type", "evidence_type", evidence_type),
            ("c.heading_path[1] = :section", "section", section),
            (
                "c.document_version_id = CAST(:version AS uuid)",
                "version",
                None if document_version_id is None else str(document_version_id),
            ),
            (
                "EXTRACT(YEAR FROM m.period_end) >= :year_low",
                "year_low",
                fiscal_year_low,
            ),
            (
                "EXTRACT(YEAR FROM m.period_end) <= :year_high",
                "year_high",
                fiscal_year_high,
            ),
        ):
            if value is not None:
                conditions.append(fragment)
                values[name] = value

        statement = sql_text(
            "SELECT c.id,"
            " ts_rank_cd(c.lexemes,"
            " websearch_to_tsquery(CAST(:cfg AS regconfig), :query)) AS score"
            " FROM chunks AS c"
            " JOIN generations AS g ON g.id = c.generation_id"
            " LEFT JOIN document_metadata AS m"
            " ON m.document_version_id = c.document_version_id"
            f" WHERE {' AND '.join(conditions)}"
            " ORDER BY score DESC, c.ordinal"
            " LIMIT :limit"
        )
        return [
            (row.id, float(row.score))
            for row in self._session.execute(statement, values)
        ]

    def parents_of(self, *, chunk_ids: Sequence[UUID]) -> dict[UUID, PendingChunk]:
        """Map each child to the parent that contains it, where one exists.

        **Why generation reads parents at all.** A retrieved child is often a fragment —
        measured on the active corpus, children average 200 tokens and 1,211 of 4,867 are
        under 48. A model given a fragment answers from a fragment. §18.5 keeps parents for
        interpretation and §20.8 expands to them; this is the read that makes that possible,
        and until now nothing used it.

        **A child with no parent is absent from the result, not an error.** The chunker drops
        a parent whose text is byte-identical to its only child, because expanding to it
        would return the same text twice. Those children are already whole runs, so the
        caller uses the child's own text and loses nothing.

        One query for the whole candidate set, joined through the child's ``parent_id``.
        Order is not meaningful: the caller holds the ranking.
        """
        if not chunk_ids:
            return {}
        parent = aliased(Chunk, name="parent")
        statement = (
            select(
                Chunk.id.label("child_id"),
                parent.id,
                parent.document_version_id,
                parent.text,
                parent.heading_path,
                parent.page_numbers,
                parent.evidence_type,
                DocumentMetadata.issuer_name,
                DocumentMetadata.document_type,
                DocumentMetadata.fiscal_period,
                DocumentMetadata.reporting_basis,
                DocumentMetadata.currency,
                DocumentMetadata.period_end,
            )
            .join(parent, parent.id == Chunk.parent_id)
            .outerjoin(
                DocumentMetadata,
                DocumentMetadata.document_version_id == parent.document_version_id,
            )
            .where(Chunk.id.in_(list(chunk_ids)))
        )
        return {
            row.child_id: PendingChunk(
                chunk_id=row.id,
                document_version_id=row.document_version_id,
                text=row.text,
                lexemes=None,
                heading_path=tuple(row.heading_path or ()),
                page_numbers=tuple(row.page_numbers or ()),
                evidence_type=row.evidence_type,
                issuer_name=row.issuer_name,
                document_type=row.document_type,
                fiscal_period=row.fiscal_period,
                reporting_basis=row.reporting_basis,
                currency=row.currency,
                period_end=row.period_end,
            )
            for row in self._session.execute(statement).all()
        }

    def load_context(self, *, chunk_ids: Sequence[UUID]) -> list[PendingChunk]:
        """Chunk text and its deterministic context, for reranking and for display.

        Returns :class:`PendingChunk` rather than a second near-identical type. The
        indexer needs exactly these fields to build the embedded string and the
        reranker needs exactly these to build its input — §23.6 permits "heading,
        period, basis, and table context" and nothing else — so one type keeps the two
        stages from drifting about what context a chunk has.

        One query for the whole candidate set. Resolving candidates one at a time would
        add a round trip per candidate to a path already dominated by model latency.

        Order is **not** meaningful: the caller holds the ranking. A missing id is
        simply absent from the result, which is the honest answer when a chunk was
        removed between retrieval and resolution.
        """
        if not chunk_ids:
            return []
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
                DocumentMetadata.period_end,
            )
            .outerjoin(
                DocumentMetadata,
                DocumentMetadata.document_version_id == Chunk.document_version_id,
            )
            .where(Chunk.id.in_(list(chunk_ids)))
        )
        return [
            PendingChunk(
                chunk_id=row.id,
                document_version_id=row.document_version_id,
                text=row.text,
                lexemes=None if row.lexemes is None else str(row.lexemes),
                heading_path=tuple(row.heading_path or ()),
                page_numbers=tuple(row.page_numbers or ()),
                evidence_type=row.evidence_type,
                issuer_name=row.issuer_name,
                document_type=row.document_type,
                fiscal_period=row.fiscal_period,
                reporting_basis=row.reporting_basis,
                currency=row.currency,
                period_end=row.period_end,
            )
            for row in self._session.execute(statement)
        ]

    def citations_for(
        self, *, chunk_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[Citation, ...]]:
        """The source regions each chunk was built from, in order (§14.7, §14.9).

        This is what makes a retrieved passage *citable* rather than merely readable: a
        chunk is a retrieval representation and §14.1 keeps evidence with the source
        representation, so a result that cannot name its source elements cannot be
        used. ``locator`` is carried because it is the address a reader is shown —
        "p. 12" — and §14.9 stores it rather than deriving it so that a rendering
        change cannot alter a citation.

        One query for the whole result set, ordered so a caller can rely on
        ``position`` without sorting.
        """
        if not chunk_ids:
            return {}
        statement = (
            select(
                ChunkSource.chunk_id,
                ChunkSource.source_element_id,
                ChunkSource.position,
                SourceElement.locator,
            )
            .join(SourceElement, SourceElement.id == ChunkSource.source_element_id)
            .where(ChunkSource.chunk_id.in_(list(chunk_ids)))
            .order_by(ChunkSource.chunk_id, ChunkSource.position)
        )
        grouped: dict[UUID, list[Citation]] = {}
        for row in self._session.execute(statement):
            grouped.setdefault(row.chunk_id, []).append(
                Citation(
                    source_element_id=row.source_element_id,
                    locator=row.locator,
                    position=row.position,
                )
            )
        return {chunk_id: tuple(items) for chunk_id, items in grouped.items()}

    def source_elements_for(
        self, *, chunk_ids: Sequence[UUID]
    ) -> dict[UUID, frozenset[UUID]]:
        """Which source elements each chunk covers, for §20.9's deduplication.

        A set rather than a sequence, because overlap is the only question being
        asked: two candidates built from the same blocks are the same evidence
        presented twice, however differently they were retrieved.
        """
        if not chunk_ids:
            return {}
        statement = select(ChunkSource.chunk_id, ChunkSource.source_element_id).where(
            ChunkSource.chunk_id.in_(list(chunk_ids))
        )
        grouped: dict[UUID, set[UUID]] = {}
        for row in self._session.execute(statement):
            grouped.setdefault(row.chunk_id, set()).add(row.source_element_id)
        return {chunk_id: frozenset(items) for chunk_id, items in grouped.items()}

    def top_level_sections(self, *, limit: int = 200) -> list[str]:
        """The section names a reader can filter on, from active generations only.

        ``heading_path[1]`` because PostgreSQL arrays are one-indexed and the indexer
        writes ``heading_path[0]`` into the ``section`` payload field — so this has to read
        the same element the filter is matched against, or the chooser would offer values
        that match nothing.

        Active generations only, for the same reason: a section that exists solely in a
        superseded generation is unreachable, and offering it would hand a reader a filter
        that silently returns nothing.

        Bounded, because this feeds a dropdown. A corpus with thousands of distinct
        headings needs a search box rather than a longer list, and that is a different
        design rather than a larger number.
        """
        statement = (
            select(Chunk.heading_path[1].label("section"))
            .join(
                DocumentVersion,
                DocumentVersion.active_generation_id == Chunk.generation_id,
            )
            .where(func.cardinality(Chunk.heading_path) > 0)
            .distinct()
            .order_by("section")
            .limit(limit)
        )
        return [row.section for row in self._session.execute(statement) if row.section]

    def sections_by_version(
        self, *, top: int = 10
    ) -> dict[UUID, list[tuple[str, int]]]:
        """What each filing contains, as top-level sections with their passage counts.

        **The question a reader of a filing library actually has is "what is in it".** A
        page count says how long a document is; this says whether it has a risk section,
        how much of it is notes to the accounts, and therefore whether a question about
        either can be answered at all.

        Counted over **retrieval children in active generations**, because a section with
        no retrievable passages cannot be reached by a question whatever the document
        contains. Parents are excluded so a passage is not counted twice.

        ``heading_path[1]`` is the first element — PostgreSQL arrays are one-indexed — and
        it is the same element the indexer writes into the ``section`` filter, so what is
        listed here is what that filter can match.
        """
        # Grouped by the output column *name*, not by a second copy of the subscript
        # expression: SQLAlchemy binds each ``heading_path[1]`` as its own parameter, and
        # PostgreSQL then refuses the statement because the grouped expression is not
        # textually the selected one.
        statement = (
            select(
                Chunk.document_version_id,
                Chunk.heading_path[1].label("section"),
                func.count().label("passages"),
            )
            .join(
                DocumentVersion,
                DocumentVersion.active_generation_id == Chunk.generation_id,
            )
            .where(
                func.cardinality(Chunk.heading_path) > 0,
                Chunk.role == ChunkRole.CHILD.value,
            )
            .group_by(Chunk.document_version_id, sql_text("section"))
            .order_by(Chunk.document_version_id, func.count().desc())
        )

        grouped: dict[UUID, list[tuple[str, int]]] = {}
        for row in self._session.execute(statement):
            if not row.section:
                continue
            sections = grouped.setdefault(row.document_version_id, [])
            if len(sections) < top:
                sections.append((row.section, row.passages))
        return grouped

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
                DocumentMetadata.period_end,
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
                period_end=row.period_end,
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
