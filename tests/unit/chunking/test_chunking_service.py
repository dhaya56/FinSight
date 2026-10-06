"""Tests for the chunking stage's orchestration.

No database. The Recorder protocol is the whole database surface, so a fake one
exercises every path the service can take — including the two that are awkward to
provoke against real infrastructure: a configuration that was already chunked, and
two runs racing to chunk the same version.

:class:`TestIdempotency` is the one that matters. Re-chunking an unchanged document
re-embeds every chunk, measured at 522 seconds for a 1,301-chunk filing, for a
byte-identical result.
"""

import uuid
from collections.abc import Sequence
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from finsight.chunking.contracts import Chunk, ChunkingConfig
from finsight.chunking.service import (
    ChunkingError,
    ChunkingService,
    NothingToChunkError,
)
from finsight.persistence.repositories.source import NarrativeBlock


def words(text: str) -> int:
    return len(text.split())


def block(
    text: str,
    *,
    ordinal: int = 0,
    page: int = 1,
    bbox: tuple[float, float, float, float] | None = (0.0, 0.0, 10.0, 10.0),
    tables: tuple[tuple[UUID, tuple[float, float, float, float]], ...] = (),
) -> NarrativeBlock:
    return NarrativeBlock(
        element_id=uuid.uuid4(),
        text=text,
        page_number=page,
        ordinal=ordinal,
        bbox=bbox,
        page_tables=tables,
    )


class FakeRecorder:
    """Stands in for every database interaction the stage performs."""

    def __init__(
        self,
        *,
        run_id: UUID | None = None,
        blocks: list[NarrativeBlock] | None = None,
        existing: UUID | None = None,
        raise_on_record: bool = False,
        settles_to: UUID | None = None,
    ) -> None:
        self.run_id = run_id or uuid.uuid4()
        self.blocks = blocks if blocks is not None else [block("Some narrative text")]
        self.existing = existing
        self.raise_on_record = raise_on_record
        self.settles_to = settles_to
        self.recorded: list[Chunk] = []
        self.prepare_calls = 0
        self.text_search_config: str | None = None

    def prepare(self, document_version_id: UUID) -> tuple[UUID, UUID | None]:
        self.prepare_calls += 1
        if self.prepare_calls > 1 and self.settles_to is not None:
            return self.run_id, self.settles_to
        return self.run_id, self.existing

    def read_blocks(self, run_id: UUID) -> list[NarrativeBlock]:
        return self.blocks

    def record(
        self,
        *,
        document_version_id: UUID,
        extraction_run_id: UUID,
        chunks: Sequence[Chunk],
        text_search_config: str,
    ) -> tuple[UUID, int]:
        if self.raise_on_record:
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        self.recorded = list(chunks)
        self.text_search_config = text_search_config
        return uuid.uuid4(), len(chunks)


def service(recorder: FakeRecorder, **kwargs: object) -> ChunkingService:
    return ChunkingService(
        recorder=recorder,  # type: ignore[arg-type]
        count_tokens=words,
        text_search_config="english",
        config=ChunkingConfig(child_max_tokens=10, child_min_tokens=1, version="1"),
        **kwargs,  # type: ignore[arg-type]
    )


class TestChunking:
    def test_blocks_become_chunks(self) -> None:
        recorder = FakeRecorder(blocks=[block("alpha beta"), block("gamma", ordinal=1)])

        result = service(recorder).chunk(uuid.uuid4())

        assert result.chunk_count > 0
        assert result.already_existed is False

    def test_the_configured_text_search_configuration_is_used(self) -> None:
        """§9.7 makes this a recorded choice, so it must reach the write."""
        recorder = FakeRecorder()

        service(recorder).chunk(uuid.uuid4())

        assert recorder.text_search_config == "english"

    def test_a_block_inside_a_table_region_is_marked(self) -> None:
        """Geometry lives here, not in the chunker (§18.4)."""
        recorder = FakeRecorder(
            blocks=[
                block("Narrative", bbox=(0.0, 0.0, 10.0, 10.0)),
                block(
                    "ROW DATA",
                    ordinal=1,
                    bbox=(100.0, 100.0, 110.0, 110.0),
                    tables=((uuid.uuid4(), (90.0, 90.0, 200.0, 200.0)),),
                ),
            ]
        )

        service(recorder).chunk(uuid.uuid4())
        kinds = {chunk.evidence_type.value for chunk in recorder.recorded}

        assert kinds == {"narrative", "table_derived"}

    def test_a_block_without_geometry_is_not_marked(self) -> None:
        """A missing bbox is unknown position, not "inside a table"."""
        recorder = FakeRecorder(
            blocks=[
                block(
                    "Text",
                    bbox=None,
                    tables=((uuid.uuid4(), (0.0, 0.0, 999.0, 999.0)),),
                )
            ]
        )

        service(recorder).chunk(uuid.uuid4())

        assert all(
            chunk.evidence_type.value == "narrative" for chunk in recorder.recorded
        )


class TestIdempotency:
    def test_an_already_chunked_configuration_is_a_no_op(self) -> None:
        """Re-embedding a byte-identical result costs ~105 s for one filing."""
        settled = uuid.uuid4()
        recorder = FakeRecorder(existing=settled)

        result = service(recorder).chunk(uuid.uuid4())

        assert result.already_existed is True
        assert result.generation_id == settled
        assert recorder.recorded == []

    def test_a_losing_race_adopts_the_winner_s_generation(self) -> None:
        """Two runs both read "none" and both proceed; the index decides.

        A prior read cannot prevent this, which is why the database enforces it
        and why the loser must resolve to the winner rather than failing.
        """
        winner = uuid.uuid4()
        recorder = FakeRecorder(raise_on_record=True, settles_to=winner)

        result = service(recorder).chunk(uuid.uuid4())

        assert result.already_existed is True
        assert result.generation_id == winner

    def test_a_conflict_that_settles_to_nothing_is_an_error(self) -> None:
        """An integrity error with no surviving generation is a real failure."""
        recorder = FakeRecorder(raise_on_record=True, settles_to=None)

        with pytest.raises(ChunkingError, match="left no generation"):
            service(recorder).chunk(uuid.uuid4())


class TestNothingToChunk:
    def test_a_version_with_no_blocks_is_refused(self) -> None:
        """A scanned filing legitimately yields none (§15.7 discloses the gap)."""
        recorder = FakeRecorder(blocks=[])

        with pytest.raises(NothingToChunkError, match="no narrative blocks"):
            service(recorder).chunk(uuid.uuid4())

    def test_blank_blocks_do_not_count_as_content(self) -> None:
        recorder = FakeRecorder(blocks=[block("   "), block("\n", ordinal=1)])

        with pytest.raises(NothingToChunkError):
            service(recorder).chunk(uuid.uuid4())
