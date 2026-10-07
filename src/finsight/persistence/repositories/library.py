"""Reading the corpus as a reader sees it: one row per document version.

**The distinction this exists to surface is §11.12's.** A document version records what
*exists*; a generation records what is *queryable*. A version can be fully extracted and still
invisible to a query, which is the correct state while its chunks are being built — and a
listing that showed only "ingested" would hide exactly that.

One query, not one per document. A listing that issued a count per row would scale with the
corpus, and the counts here are aggregates the database is far better at than Python.

**No filename and no object key.** §10 keeps storage locations out of anything a reader sees,
and a filing is identified here by issuer, type and period — which is what a reader recognises
anyway.
"""

import datetime
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from finsight.domain.representations.retrieval import ChunkRole
from finsight.domain.representations.source import ElementType, Verdict
from finsight.persistence.repositories.chunks import ChunkRepository
from finsight.persistence.tables.chunks import Chunk
from finsight.persistence.tables.document_metadata import DocumentMetadata
from finsight.persistence.tables.documents import DocumentVersion
from finsight.persistence.tables.generations import Generation
from finsight.persistence.tables.source import ExtractionRun, SourceElement, SourceTable

__all__ = ["LibraryEntry", "LibraryRepository"]


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    """One document version, and the state of what was derived from it."""

    document_version_id: UUID
    issuer_name: str | None
    document_type: str | None
    fiscal_period: str | None
    reporting_basis: str | None

    generation_state: str | None
    """``None`` when no generation exists yet — extracted but never chunked."""

    chunking_config_version: str | None
    activated_at: datetime.datetime | None
    ingested_at: datetime.datetime

    pages: int
    blocks: int
    tables: int
    footnotes: int
    chunks: int
    """Chunks in the **active** generation, so the number is what a query can reach."""

    byte_size: int

    extraction_state: str | None
    """``succeeded`` or ``partial``. Partial is the common case and not a failure: some
    region was not extractable and was recorded as a gap rather than silently dropped."""

    extraction_config_version: str | None
    extraction_seconds: float | None

    unreadable_regions: int
    """Elements that carry a failure reason instead of text (§12).

    The honest counterpart to the page count: a document can be fully paginated and still
    have regions nothing could read, and those regions are not in the index.
    """

    tables_accepted: int
    tables_rejected: int
    """A rejected region is one the page's own ruling lines do not support.

    Neither number is a count of *retrievable* tables: ADR-003 admits no detector, so
    table cells are excluded from retrieval entirely whatever the verdict.
    """

    child_chunks: int
    parent_chunks: int
    median_child_tokens: int
    """§18.5's two unit sizes made visible: children for precision, parents for context."""

    sections: tuple[tuple[str, int], ...] = ()
    """Top-level sections with their retrievable passage counts, largest first.

    What the filing actually contains, which is the question a page count cannot answer.
    """


class LibraryRepository:
    """Lists what has been ingested. Read-only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def entries(self, *, limit: int = 500) -> list[LibraryEntry]:
        """Every document version, newest first.

        Counts come from correlated subqueries rather than joins: joining the element and
        chunk tables would multiply rows before aggregation and inflate every count, which
        is the classic way a listing like this reports nonsense that looks plausible.
        """
        elements = (
            select(func.count())
            .select_from(SourceElement)
            .where(SourceElement.extraction_run_id == DocumentVersion.current_extraction_run_id)
        )

        def element_count(kind: ElementType):  # type: ignore[no-untyped-def]
            return elements.where(SourceElement.element_type == kind.value).scalar_subquery()

        chunks_of_active = select(func.count()).select_from(Chunk).where(
            Chunk.generation_id == DocumentVersion.active_generation_id
        )
        chunk_count = chunks_of_active.scalar_subquery()

        def role_count(role: ChunkRole):  # type: ignore[no-untyped-def]
            return chunks_of_active.where(Chunk.role == role.value).scalar_subquery()

        median_child = (
            select(
                func.percentile_cont(0.5).within_group(Chunk.token_count.asc())
            )
            .select_from(Chunk)
            .where(
                Chunk.generation_id == DocumentVersion.active_generation_id,
                Chunk.role == ChunkRole.CHILD.value,
            )
            .scalar_subquery()
        )

        unreadable = (
            select(func.count())
            .select_from(SourceElement)
            .where(
                SourceElement.extraction_run_id
                == DocumentVersion.current_extraction_run_id,
                SourceElement.failure_reason.is_not(None),
            )
            .scalar_subquery()
        )

        def verdict_count(verdict: Verdict):  # type: ignore[no-untyped-def]
            return (
                select(func.count())
                .select_from(SourceTable)
                .join(SourceElement, SourceElement.id == SourceTable.source_element_id)
                .where(
                    SourceElement.extraction_run_id
                    == DocumentVersion.current_extraction_run_id,
                    SourceTable.verdict == verdict.value,
                )
                .scalar_subquery()
            )

        statement = (
            select(
                DocumentVersion.id,
                DocumentVersion.byte_size,
                DocumentVersion.created_at,
                DocumentMetadata.issuer_name,
                DocumentMetadata.document_type,
                DocumentMetadata.fiscal_period,
                DocumentMetadata.reporting_basis,
                Generation.state,
                Generation.chunking_config_version,
                Generation.activated_at,
                func.coalesce(element_count(ElementType.PAGE), 0).cast(Integer).label("pages"),
                func.coalesce(element_count(ElementType.BLOCK), 0).cast(Integer).label("blocks"),
                func.coalesce(element_count(ElementType.TABLE), 0).cast(Integer).label("tables"),
                func.coalesce(element_count(ElementType.FOOTNOTE), 0)
                .cast(Integer)
                .label("footnotes"),
                func.coalesce(chunk_count, 0).cast(Integer).label("chunks"),
                func.coalesce(unreadable, 0).cast(Integer).label("unreadable"),
                func.coalesce(verdict_count(Verdict.ACCEPTED), 0)
                .cast(Integer)
                .label("tables_accepted"),
                func.coalesce(verdict_count(Verdict.REJECTED), 0)
                .cast(Integer)
                .label("tables_rejected"),
                func.coalesce(role_count(ChunkRole.CHILD), 0)
                .cast(Integer)
                .label("children"),
                func.coalesce(role_count(ChunkRole.PARENT), 0)
                .cast(Integer)
                .label("parents"),
                func.coalesce(median_child, 0).cast(Integer).label("median_child"),
                ExtractionRun.state.label("extraction_state"),
                ExtractionRun.config_version.label("extraction_config"),
                (
                    func.extract("epoch", ExtractionRun.completed_at)
                    - func.extract("epoch", ExtractionRun.started_at)
                ).label("extraction_seconds"),
            )
            .outerjoin(
                DocumentMetadata,
                DocumentMetadata.document_version_id == DocumentVersion.id,
            )
            .outerjoin(Generation, Generation.id == DocumentVersion.active_generation_id)
            .outerjoin(
                ExtractionRun,
                ExtractionRun.id == DocumentVersion.current_extraction_run_id,
            )
            .order_by(DocumentVersion.created_at.desc())
            .limit(limit)
        )

        sections = ChunkRepository(self._session).sections_by_version()
        return [
            LibraryEntry(
                document_version_id=row.id,
                issuer_name=row.issuer_name,
                document_type=row.document_type,
                fiscal_period=row.fiscal_period,
                reporting_basis=row.reporting_basis,
                generation_state=row.state,
                chunking_config_version=row.chunking_config_version,
                activated_at=row.activated_at,
                ingested_at=row.created_at,
                pages=row.pages,
                blocks=row.blocks,
                tables=row.tables,
                footnotes=row.footnotes,
                chunks=row.chunks,
                byte_size=row.byte_size,
                extraction_state=row.extraction_state,
                extraction_config_version=row.extraction_config,
                extraction_seconds=(
                    float(row.extraction_seconds)
                    if row.extraction_seconds is not None
                    else None
                ),
                unreadable_regions=row.unreadable,
                tables_accepted=row.tables_accepted,
                tables_rejected=row.tables_rejected,
                child_chunks=row.children,
                parent_chunks=row.parents,
                median_child_tokens=row.median_child,
                sections=tuple(sections.get(row.id, ())),
            )
            for row in self._session.execute(statement)
        ]
