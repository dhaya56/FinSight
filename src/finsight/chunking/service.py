"""Turning a document version's source elements into chunks and index events.

The second arrow of the retrieval path: stored evidence becomes searchable units.
Mirrors ``extraction/service.py`` deliberately — a Recorder protocol that owns the
transactions, a Service that does no I/O of its own and so unit-tests with no
database, and a ``build_*`` factory that wires them.

**Reading and chunking happen outside any transaction.** The read is one query,
the chunking is pure computation, and only the write is transactional. §29.7 keeps
model and vector calls out of a transaction; the same discipline keeps a
long-running computation out of one.

**Re-chunking an unchanged document is a recorded no-op.** A partial unique index
on (document version, extraction run, chunking configuration) makes a repeat run
return the existing generation rather than building a second one — which would
re-embed every chunk, **measured at 522 seconds for a 1,301-chunk filing**, for a
byte-identical result. Extraction established this pattern; chunking inherits it
rather than rediscovering the cost in production.

(An earlier version of this note said 105 seconds, from ADR-004's throughput probe.
That probe embedded 34-character sentences; real chunks average 1,456 characters
enriched and embed 18x slower. ADR-004 carries the correction.)
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finsight.chunking.chunker import chunk_blocks, mark_table_derived
from finsight.chunking.contracts import Chunk, ChunkingConfig, SourceBlock
from finsight.chunking.tokens import TokenCounter, build_token_counter
from finsight.domain.errors import DomainError
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.chunks import ChunkRepository
from finsight.persistence.repositories.generations import GenerationRepository
from finsight.persistence.repositories.source import NarrativeBlock, SourceRepository
from finsight.persistence.tables.documents import DocumentVersion

CHUNKING_CONFIG_VERSION: Final = "4"
"""Bump when any value in :class:`ChunkingConfig` changes, or when the chunker's
output changes shape for the same values.

It keys the idempotency index, so leaving it alone after a size change would make
a re-chunk look like a repeat and quietly keep the old chunks.

Bumped to "2" when parents became windows that contain their children. No
configuration value moved; the *output* did — a long run now yields several parents
instead of one truncated one — and the index cannot tell those apart by
configuration alone.

Bumped to "4" when typographic leader lines stopped being indexed. Fewer blocks are
admitted, so chunk boundaries move corpus-wide and the populations are not comparable:
a config-3 chunk and a config-4 chunk over the same section are different text. ADR-007
carries the measurement and the §7 deviation.

Bumped to "3" when comma-grouped figures began being joined before analysis, which
changes every chunk's ``lexemes``. §9.7 already states the consequence of changing
how text is analysed: it "requires re-chunking under a new generation, because the
stored lexemes were analysed with the old one". The chunk *text* is identical, so the
dense vectors are rebuilt for nothing — an avoidable cost that would need lexemes
versioned separately from chunking to avoid, which is not worth a second version
column for the gain.
"""


class ChunkingError(DomainError):
    """A document version could not be chunked."""


class NothingToChunkError(ChunkingError):
    """The version has no completed extraction, or extraction yielded no blocks.

    Distinct from a failure: a document of scanned pages legitimately produces no
    narrative blocks, and §15.7 requires such gaps to be disclosed rather than
    presented as an empty result.
    """


@dataclass(frozen=True, slots=True)
class RecordedChunking:
    """What one chunking run produced."""

    generation_id: UUID
    document_version_id: UUID
    chunk_count: int
    child_count: int
    already_existed: bool
    """True when an identical configuration had already chunked this version."""


class ChunkingRecorder(Protocol):
    """All database interaction for the chunking stage."""

    def prepare(self, document_version_id: UUID) -> tuple[UUID, UUID | None]:
        """Return the extraction run to chunk and any generation already built."""
        ...

    def read_blocks(self, run_id: UUID) -> list[NarrativeBlock]: ...

    def record(
        self,
        *,
        document_version_id: UUID,
        extraction_run_id: UUID,
        chunks: Sequence[Chunk],
        text_search_config: str,
    ) -> tuple[UUID, int]:
        """Write the generation, its chunks and their outbox events in one go."""
        ...


class TransactionalChunkingRecorder:
    """The recorder that owns the transactions."""

    def __init__(
        self,
        session_scope_factory: Callable[
            [], AbstractContextManager[Session]
        ] = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    def prepare(self, document_version_id: UUID) -> tuple[UUID, UUID | None]:
        with self._session_scope() as session:
            version = session.get(DocumentVersion, document_version_id)
            if version is None:
                raise NothingToChunkError(
                    f"no document version {document_version_id}"
                )
            if version.current_extraction_run_id is None:
                raise NothingToChunkError(
                    f"document version {document_version_id} has no completed "
                    "extraction to chunk"
                )
            run_id = version.current_extraction_run_id
            existing = GenerationRepository(session).for_configuration(
                document_version_id=document_version_id,
                extraction_run_id=run_id,
                chunking_config_version=CHUNKING_CONFIG_VERSION,
            )
            return run_id, existing

    def read_blocks(self, run_id: UUID) -> list[NarrativeBlock]:
        with self._session_scope() as session:
            return SourceRepository(session).narrative_blocks(run_id=run_id)

    def record(
        self,
        *,
        document_version_id: UUID,
        extraction_run_id: UUID,
        chunks: Sequence[Chunk],
        text_search_config: str,
    ) -> tuple[UUID, int]:
        """One transaction: the generation, its chunks, its outbox events.

        §29.8 requires chunk and index-event records to commit together, and the
        generation they belong to has to exist first — so all three are one unit.
        A crash between them would leave chunks belonging to no generation, which
        §20.2 could never filter and nothing would ever index.
        """
        with self._session_scope() as session:
            generations = GenerationRepository(session)
            generation_id = generations.open(
                document_version_id=document_version_id,
                extraction_run_id=extraction_run_id,
                chunking_config_version=CHUNKING_CONFIG_VERSION,
            )
            written = ChunkRepository(session).record(
                generation_id=generation_id,
                document_version_id=document_version_id,
                chunks=chunks,
                text_search_config=text_search_config,
            )
            return generation_id, written


class ChunkingService:
    """Chunks a stored document version. Performs no I/O of its own."""

    def __init__(
        self,
        *,
        recorder: ChunkingRecorder,
        count_tokens: TokenCounter,
        text_search_config: str,
        config: ChunkingConfig | None = None,
    ) -> None:
        self._recorder = recorder
        self._count = count_tokens
        self._text_search_config = text_search_config
        self._config = config or ChunkingConfig(version=CHUNKING_CONFIG_VERSION)

    def chunk(self, document_version_id: UUID) -> RecordedChunking:
        """Chunk a version, or report that an identical run already did.

        Raises:
            NothingToChunkError: no completed extraction, or no narrative blocks.
        """
        run_id, existing = self._recorder.prepare(document_version_id)
        if existing is not None:
            return RecordedChunking(
                generation_id=existing,
                document_version_id=document_version_id,
                chunk_count=0,
                child_count=0,
                already_existed=True,
            )

        blocks = self._recorder.read_blocks(run_id)
        if not blocks:
            raise NothingToChunkError(
                f"extraction run {run_id} yielded no narrative blocks"
            )

        chunks = chunk_blocks(
            [self._as_source_block(block) for block in blocks],
            config=self._config,
            count_tokens=self._count,
        )
        if not chunks:
            # Blocks existed but none held text — a page of whitespace, or of
            # content the producer could not read. Writing the generation anyway
            # would record one with no chunks and no index events: never
            # activatable, and indistinguishable from a document nobody asked
            # about. §15.7 requires the gap to be disclosed.
            raise NothingToChunkError(
                f"extraction run {run_id} produced {len(blocks)} block(s) but no "
                "chunkable text"
            )

        try:
            generation_id, written = self._recorder.record(
                document_version_id=document_version_id,
                extraction_run_id=run_id,
                chunks=chunks,
                text_search_config=self._text_search_config,
            )
        except IntegrityError as error:
            # Another run reached the index first. Idempotency is enforced by the
            # database rather than by the prepare() read, because two concurrent
            # runs would both read "none" and both proceed.
            _, settled = self._recorder.prepare(document_version_id)
            if settled is None:
                raise ChunkingError(
                    "chunking conflicted and left no generation"
                ) from error
            return RecordedChunking(
                generation_id=settled,
                document_version_id=document_version_id,
                chunk_count=0,
                child_count=0,
                already_existed=True,
            )

        return RecordedChunking(
            generation_id=generation_id,
            document_version_id=document_version_id,
            chunk_count=written,
            child_count=sum(1 for chunk in chunks if chunk.parent_index is not None)
            or len(chunks),
            already_existed=False,
        )

    def _as_source_block(self, block: NarrativeBlock) -> SourceBlock:
        """Mark table overlap here, where page geometry is still available."""
        return SourceBlock(
            element_id=block.element_id,
            text=block.text,
            page_number=block.page_number,
            ordinal=block.ordinal,
            table_derived=block.bbox is not None
            and mark_table_derived(
                block.bbox,
                block.page_tables,
                threshold=self._config.table_overlap,
            ),
        )


def build_chunking_service() -> ChunkingService:
    """Wire chunking to the configured tokenizer and text-search configuration."""
    from finsight.config.settings import get_settings

    settings = get_settings()
    return ChunkingService(
        recorder=TransactionalChunkingRecorder(),
        count_tokens=build_token_counter(),
        text_search_config=settings.text_search_config,
    )
