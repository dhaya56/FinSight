"""Integration tests for storing chunks, their sources and their index events.

Two things are worth proving here and neither can be proved without a database.

**§29.8 atomicity.** Chunks and their index events commit together. Written
separately, a crash between them leaves chunks nothing will ever index — invisible
to retrieval and indistinguishable from chunks that were indexed and matched
nothing.

**The lexical vector is real.** ``lexemes`` is written by the application using a
configured text-search configuration (§9.7), so the test that matters is that a
stemmed query actually matches it: searching "lending" must find a chunk that says
"lends".
"""

import uuid
from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finsight.chunking.contracts import Chunk as DerivedChunk
from finsight.domain.representations.retrieval import ChunkRole, EvidenceType
from finsight.persistence.database import dispose_engine, get_engine
from finsight.persistence.repositories.chunks import (
    ChunkRepository,
    UnknownTextSearchConfigError,
)
from finsight.persistence.repositories.documents import DocumentRepository
from finsight.persistence.repositories.generations import GenerationRepository
from finsight.persistence.tables.chunks import (
    EVENT_PENDING,
    Chunk,
    ChunkSource,
    IndexOutbox,
)

pytestmark = pytest.mark.integration

CONFIG = "english"


@pytest.fixture(scope="module", autouse=True)
def _release_pool() -> Iterator[None]:
    yield
    dispose_engine()


@pytest.fixture
def session() -> Iterator[Session]:
    connection = get_engine().connect()
    transaction = connection.begin()
    opened = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield opened
    finally:
        opened.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def context(session: Session) -> tuple[UUID, UUID, list[UUID]]:
    """A document version, a shadow generation, and three real source elements."""
    content_hash = f"{uuid.uuid4().hex}{uuid.uuid4().hex}"
    version_id = DocumentRepository(session).record_version(
        hash_algorithm="sha256",
        content_hash=content_hash,
        byte_size=1024,
        detected_content_type="application/pdf",
        object_key=f"originals/sha256/ab/cd/{content_hash}",
        original_filename="probe.pdf",
    ).id
    run_id = session.execute(
        text(
            "INSERT INTO extraction_runs (document_version_id, format,"
            " producer_policy, config_version, state, element_count)"
            " VALUES (:v, 'pdf', 'pdf-native', '1', 'succeeded', 0) RETURNING id"
        ),
        {"v": version_id},
    ).scalar_one()
    generation_id = GenerationRepository(session).open(
        document_version_id=version_id, extraction_run_id=run_id
    )
    elements = [
        session.execute(
            text(
                "INSERT INTO source_elements (extraction_run_id, ordinal,"
                " element_type, extraction_method, extraction_method_version,"
                " locator, location)"
                " VALUES (:r, :o, 'block', 'pymupdf', '1.28.2', :loc,"
                " '{\"bbox\": [0,0,1,1]}'::jsonb) RETURNING id"
            ),
            {"r": run_id, "o": n, "loc": f"p. 1, block {n}"},
        ).scalar_one()
        for n in range(3)
    ]
    return version_id, generation_id, elements


def derived(
    text_value: str,
    sources: list[UUID],
    *,
    ordinal: int = 0,
    role: ChunkRole = ChunkRole.CHILD,
    parent_index: int | None = None,
) -> DerivedChunk:
    return DerivedChunk(
        text=text_value,
        source_element_ids=tuple(sources),
        page_numbers=(1,),
        heading_path=("7. Risk factors",),
        evidence_type=EvidenceType.NARRATIVE,
        role=role,
        parent_index=parent_index,
        token_count=len(text_value.split()),
        char_count=len(text_value),
        ordinal=ordinal,
        config_version="1",
    )


class TestWriting:
    def test_chunks_are_stored(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        version_id, generation_id, elements = context

        written = ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[derived("The Bank lends to retail customers.", elements[:1])],
            text_search_config=CONFIG,
        )

        assert written == 1
        assert ChunkRepository(session).count_for_generation(
            generation_id=generation_id
        ) == 1

    def test_sources_are_recorded_in_order(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        """§14.7. Without the order a citation says which blocks, not which first."""
        version_id, generation_id, elements = context

        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[derived("Joined text", elements)],
            text_search_config=CONFIG,
        )

        rows = session.execute(
            select(ChunkSource.source_element_id)
            .join(Chunk, Chunk.id == ChunkSource.chunk_id)
            .where(Chunk.generation_id == generation_id)
            .order_by(ChunkSource.position)
        ).scalars().all()
        assert list(rows) == elements

    def test_a_repeated_source_is_recorded_once(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        """A split oversized block carries the same element id on every piece."""
        version_id, generation_id, elements = context
        repeated = [elements[0], elements[0], elements[0]]

        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[derived("A piece of a long block", repeated)],
            text_search_config=CONFIG,
        )

        assert session.execute(
            select(func.count())
            .select_from(ChunkSource)
            .join(Chunk, Chunk.id == ChunkSource.chunk_id)
            .where(Chunk.generation_id == generation_id)
        ).scalar_one() == 1

    def test_a_child_points_at_its_parent(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        version_id, generation_id, elements = context
        chunks = [
            derived("Parent text", elements, ordinal=0, role=ChunkRole.PARENT),
            derived("Child text", elements[:1], ordinal=1, parent_index=0),
        ]

        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=chunks,
            text_search_config=CONFIG,
        )

        child = session.execute(
            select(Chunk).where(
                Chunk.generation_id == generation_id,
                Chunk.role == ChunkRole.CHILD.value,
            )
        ).scalar_one()
        parent = session.execute(
            select(Chunk).where(
                Chunk.generation_id == generation_id,
                Chunk.role == ChunkRole.PARENT.value,
            )
        ).scalar_one()
        assert child.parent_id == parent.id

    def test_writing_nothing_is_a_no_op(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        version_id, generation_id, _ = context

        assert ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[],
            text_search_config=CONFIG,
        ) == 0


class TestOutbox:
    def test_an_index_event_is_written_with_each_child(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        """§29.8: chunk and index-event records commit together."""
        version_id, generation_id, elements = context

        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[derived("Indexable text", elements[:1])],
            text_search_config=CONFIG,
        )

        event = session.execute(
            select(IndexOutbox).where(IndexOutbox.generation_id == generation_id)
        ).scalar_one()
        assert event.state == EVENT_PENDING
        assert event.completed_at is None

    def test_a_parent_is_not_queued_for_indexing(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        """A parent repeats its children's text.

        Embedding both would let a section outrank its own best paragraph on every
        query that matches it; §18.5 keeps parents for context, not for matching.
        """
        version_id, generation_id, elements = context
        chunks = [
            derived("Parent text", elements, ordinal=0, role=ChunkRole.PARENT),
            derived("Child text", elements[:1], ordinal=1, parent_index=0),
        ]

        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=chunks,
            text_search_config=CONFIG,
        )

        assert session.execute(
            select(func.count())
            .select_from(IndexOutbox)
            .where(IndexOutbox.generation_id == generation_id)
        ).scalar_one() == 1

    def test_pending_events_are_listed_for_the_indexer(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        version_id, generation_id, elements = context
        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[derived("One", elements[:1]), derived("Two", elements[1:2], ordinal=1)],
            text_search_config=CONFIG,
        )

        pending = ChunkRepository(session).pending_events(
            generation_id=generation_id, limit=10
        )

        assert len(pending) == 2


class TestLexicalVector:
    def test_a_stemmed_query_matches_the_stored_lexemes(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        """The point of the column: "lending" must find a chunk that says "lends"."""
        version_id, generation_id, elements = context
        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[derived("The Bank lends to retail customers.", elements[:1])],
            text_search_config=CONFIG,
        )

        found = session.execute(
            text(
                "SELECT count(*) FROM chunks WHERE generation_id = :g"
                " AND lexemes @@ websearch_to_tsquery(CAST(:cfg AS regconfig), :q)"
            ),
            {"g": generation_id, "cfg": CONFIG, "q": "lending"},
        ).scalar_one()

        assert found == 1

    def test_an_unrelated_query_does_not_match(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        version_id, generation_id, elements = context
        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[derived("The Bank lends to retail customers.", elements[:1])],
            text_search_config=CONFIG,
        )

        found = session.execute(
            text(
                "SELECT count(*) FROM chunks WHERE generation_id = :g"
                " AND lexemes @@ websearch_to_tsquery(CAST(:cfg AS regconfig), :q)"
            ),
            {"g": generation_id, "cfg": CONFIG, "q": "aviation"},
        ).scalar_one()

        assert found == 0

    def test_an_unknown_configuration_is_refused_rather_than_defaulted(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        """Analysing with a different configuration than requested would build an
        index whose queries never match it."""
        version_id, generation_id, elements = context

        with pytest.raises(UnknownTextSearchConfigError, match="no text-search"):
            ChunkRepository(session).record(
                generation_id=generation_id,
                document_version_id=version_id,
                chunks=[derived("Text", elements[:1])],
                text_search_config="klingon",
            )


class TestTheDatabaseRefusesUnsafeChunks:
    def test_a_blank_chunk_is_refused(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        version_id, generation_id, _ = context
        session.add(
            Chunk(
                generation_id=generation_id,
                document_version_id=version_id,
                ordinal=0,
                role=ChunkRole.CHILD.value,
                evidence_type=EvidenceType.NARRATIVE.value,
                text="",
                token_count=0,
                char_count=0,
                config_version="1",
            )
        )

        with pytest.raises(IntegrityError, match="text_not_blank"):
            session.flush()

    def test_a_char_count_that_disagrees_with_its_text_is_refused(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        version_id, generation_id, _ = context
        session.add(
            Chunk(
                generation_id=generation_id,
                document_version_id=version_id,
                ordinal=0,
                role=ChunkRole.CHILD.value,
                evidence_type=EvidenceType.NARRATIVE.value,
                text="four",
                token_count=1,
                char_count=999,
                config_version="1",
            )
        )

        with pytest.raises(IntegrityError, match="char_count_matches_text"):
            session.flush()

    def test_a_parent_with_a_parent_is_refused(
        self, session: Session, context: tuple[UUID, UUID, list[UUID]]
    ) -> None:
        """A parent pointing at a parent is a cycle §20.8 would follow forever."""
        version_id, generation_id, elements = context
        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[derived("Parent", elements, role=ChunkRole.PARENT)],
            text_search_config=CONFIG,
        )
        existing = session.execute(
            select(Chunk.id).where(Chunk.generation_id == generation_id)
        ).scalar_one()

        session.add(
            Chunk(
                generation_id=generation_id,
                document_version_id=version_id,
                parent_id=existing,
                ordinal=1,
                role=ChunkRole.PARENT.value,
                evidence_type=EvidenceType.NARRATIVE.value,
                text="another parent",
                token_count=2,
                char_count=14,
                config_version="1",
            )
        )

        with pytest.raises(IntegrityError, match="only_a_child_has_a_parent"):
            session.flush()
