"""Indexing against real PostgreSQL and real Qdrant.

What cannot be proved without both:

**The analysis chain closes.** A chunk's lexemes are produced by PostgreSQL's
``to_tsvector``, weighted here, stored in Qdrant, and matched by a query analysed
by the same configuration. Searching "lending" has to find a chunk that says
"lends", and every link in that chain is somebody else's code. The unit tests prove
the arithmetic; only this proves the chain.

**Server-side IDF actually ranks.** ADR-005 splits BM25 so that Qdrant supplies
IDF, and a split that silently did nothing would look identical in every unit test.

Ollama is not used. The deterministic fake stands in, so these run in CI where no
model exists — which means nothing here asserts retrieval *quality*, only that the
pipeline carries vectors end to end.
"""

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from finsight.chunking.contracts import Chunk as DerivedChunk
from finsight.config.settings import get_settings
from finsight.domain.representations.retrieval import ChunkRole, EvidenceType
from finsight.embedding.fake import FakeEmbedder
from finsight.indexing.service import (
    GenerationNotIndexableError,
    IndexingService,
    TransactionalIndexingRecorder,
)
from finsight.lexical.bm25 import query_vector
from finsight.persistence.database import dispose_engine, get_engine
from finsight.persistence.repositories.chunks import ChunkRepository
from finsight.persistence.repositories.documents import DocumentRepository
from finsight.persistence.repositories.generations import GenerationRepository
from finsight.persistence.tables.generations import STATE_ACTIVE
from finsight.vector_index.qdrant_index import QdrantVectorIndex

pytestmark = pytest.mark.integration

CONFIG = "english"
DIMENSIONS = 32


@pytest.fixture(scope="module", autouse=True)
def _release_pool() -> Iterator[None]:
    yield
    dispose_engine()


@pytest.fixture
def session() -> Iterator[Session]:
    """One transaction, rolled back. The recorder is pointed at it."""
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
def index() -> Iterator[QdrantVectorIndex]:
    """A collection of its own, dropped afterwards.

    Qdrant has no transaction to roll back, so isolation is by name. The naming
    scheme derives the collection from the model, so a test model cannot collide
    with the real one.
    """
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url, timeout=30)
    built = QdrantVectorIndex(
        client=client,
        model=f"test-index-{uuid.uuid4().hex[:8]}",
        dimensions=DIMENSIONS,
        config_version="1",
    )
    try:
        yield built
    finally:
        if client.collection_exists(built.collection):
            client.delete_collection(built.collection)
        client.close()


@pytest.fixture
def service(
    session: Session, index: QdrantVectorIndex
) -> IndexingService:
    @contextmanager
    def scope() -> Iterator[Session]:
        # Deliberately does not commit. Every recorder call joins the test's own
        # transaction, so the real SQL runs and nothing survives the rollback.
        yield session

    return IndexingService(
        recorder=TransactionalIndexingRecorder(session_scope_factory=scope),
        embedder=FakeEmbedder(dimensions=DIMENSIONS),
        index=index,
        batch_size=4,
    )


def derived(
    body: str,
    sources: list[UUID],
    *,
    ordinal: int = 0,
    role: ChunkRole = ChunkRole.CHILD,
    parent_index: int | None = None,
) -> DerivedChunk:
    return DerivedChunk(
        text=body,
        source_element_ids=tuple(sources),
        page_numbers=(1,),
        heading_path=("7. Risk factors",),
        evidence_type=EvidenceType.NARRATIVE,
        role=role,
        parent_index=parent_index,
        token_count=len(body.split()),
        char_count=len(body),
        ordinal=ordinal,
        config_version="1",
    )


@pytest.fixture
def chunked(session: Session) -> Callable[..., tuple[UUID, UUID]]:
    """Build a version, a generation and its chunks. Returns both identifiers."""

    def _chunked(
        *bodies: str,
        issuer: str = "Probe Limited",
        roles: tuple[ChunkRole, ...] | None = None,
    ) -> tuple[UUID, UUID]:
        content_hash = f"{uuid.uuid4().hex}{uuid.uuid4().hex}"
        version_id = DocumentRepository(session).record_version(
            hash_algorithm="sha256",
            content_hash=content_hash,
            byte_size=2048,
            detected_content_type="application/pdf",
            object_key=f"originals/sha256/ab/cd/{content_hash}",
            original_filename="probe.pdf",
        ).id
        session.execute(
            text(
                "INSERT INTO document_metadata (document_version_id, issuer_name,"
                " document_type, fiscal_period, reporting_basis, currency, source)"
                " VALUES (:v, :issuer, 'annual_report', 'FY2024-25', 'both',"
                " 'INR', 'corpus_manifest')"
            ),
            {"v": version_id, "issuer": issuer},
        )
        run_id = session.execute(
            text(
                "INSERT INTO extraction_runs (document_version_id, format,"
                " producer_policy, config_version, state, element_count)"
                " VALUES (:v, 'pdf', 'pdf-native', '2', 'succeeded', 0)"
                " RETURNING id"
            ),
            {"v": version_id},
        ).scalar_one()
        element_ids = [
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
            for n in range(len(bodies))
        ]
        generation_id = GenerationRepository(session).open(
            document_version_id=version_id, extraction_run_id=run_id
        )
        assigned = roles or tuple(ChunkRole.CHILD for _ in bodies)
        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[
                derived(body, [element_ids[n]], ordinal=n, role=assigned[n])
                for n, body in enumerate(bodies)
            ],
            text_search_config=CONFIG,
        )
        return version_id, generation_id

    return _chunked


def analysed(session: Session, query: str) -> str:
    """The query through the same configuration that produced the lexemes.

    Not a tokenizer written in the test. The point of using PostgreSQL's analysis
    on both sides is that the two agree, and a test that stemmed the query itself
    would prove nothing about the pair.
    """
    return session.execute(
        text("SELECT to_tsvector(CAST(:cfg AS regconfig), :q)::text").bindparams(
            cfg=CONFIG, q=query
        )
    ).scalar_one()


class TestIndexing:
    def test_a_generation_is_indexed_and_activated(
        self,
        service: IndexingService,
        session: Session,
        chunked: Callable[..., tuple[UUID, UUID]],
        index: QdrantVectorIndex,
    ) -> None:
        _version_id, generation_id = chunked(
            "The Bank lends to retail customers.",
            "Deposits grew during the year.",
        )

        result = service.index(generation_id)

        assert result.indexed == 2
        assert result.activated is True
        assert index.count(filters={"generation_id": str(generation_id)}) == 2
        assert GenerationRepository(session).state_of(
            generation_id=generation_id
        ) == STATE_ACTIVE

    def test_activation_moves_the_pointer_on_the_version(
        self,
        service: IndexingService,
        session: Session,
        chunked: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """§11.12: the pointer is what records which generation is queryable."""
        version_id, generation_id = chunked("Capital adequacy remained strong.")

        service.index(generation_id)

        pointer = session.execute(
            text(
                "SELECT active_generation_id FROM document_versions WHERE id = :v"
            ).bindparams(v=version_id)
        ).scalar_one()
        assert pointer == generation_id

    def test_only_children_reach_the_index(
        self,
        service: IndexingService,
        chunked: Callable[..., tuple[UUID, UUID]],
        index: QdrantVectorIndex,
    ) -> None:
        """A parent repeats its children's text (§18.5).

        Indexing both would let a section outrank its own best paragraph on every
        query that matches it.
        """
        _version_id, generation_id = chunked(
            "Parent text covering the section.",
            "Child text about lending.",
            roles=(ChunkRole.PARENT, ChunkRole.CHILD),
        )

        result = service.index(generation_id)

        assert result.indexed == 1
        assert index.count(filters={"generation_id": str(generation_id)}) == 1

    def test_an_indexed_generation_is_refused_a_second_time(
        self,
        service: IndexingService,
        chunked: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """It is active, and re-indexing in place would mutate what a reader sees."""
        _version_id, generation_id = chunked("Revenue rose.")
        service.index(generation_id)

        with pytest.raises(GenerationNotIndexableError):
            service.index(generation_id)


class TestPayload:
    def test_a_filter_on_issuer_finds_the_chunk(
        self,
        service: IndexingService,
        chunked: Callable[..., tuple[UUID, UUID]],
        index: QdrantVectorIndex,
    ) -> None:
        """§20.2's hard filters have to reach the payload to be enforceable."""
        _version_id, generation_id = chunked(
            "Revenue rose.", issuer="Specific Issuer Limited"
        )
        service.index(generation_id)

        assert index.count(filters={"issuer_name": "Specific Issuer Limited"}) == 1
        assert index.count(filters={"issuer_name": "Someone Else Limited"}) == 0

    def test_a_filter_on_the_wrong_generation_excludes_everything(
        self,
        service: IndexingService,
        chunked: Callable[..., tuple[UUID, UUID]],
        index: QdrantVectorIndex,
    ) -> None:
        """Generation is the filter §20.2 bounds a search by; it must be exact."""
        _version_id, generation_id = chunked("Revenue rose.")
        service.index(generation_id)

        assert index.count(filters={"generation_id": str(uuid.uuid4())}) == 0


class TestLexicalChain:
    def test_a_stemmed_query_matches_a_differently_inflected_chunk(
        self,
        service: IndexingService,
        session: Session,
        chunked: Callable[..., tuple[UUID, UUID]],
        index: QdrantVectorIndex,
    ) -> None:
        """"lending" must find "lends".

        The whole reason for analysing both sides with PostgreSQL: the stems agree
        because the same configuration produced them. This is the test that fails
        if a second tokenizer is ever introduced on one side.
        """
        _version_id, generation_id = chunked(
            "The Bank lends to retail customers.",
            "Unrelated text about cricket sponsorship.",
        )
        service.index(generation_id)
        query = query_vector(analysed(session, "lending"))

        matches = index.search_sparse(
            query.indices,
            query.values,
            limit=5,
            filters={"generation_id": str(generation_id)},
        )

        assert len(matches) == 1
        assert matches[0].score > 0

    def test_a_query_term_absent_from_the_corpus_matches_nothing(
        self,
        service: IndexingService,
        session: Session,
        chunked: Callable[..., tuple[UUID, UUID]],
        index: QdrantVectorIndex,
    ) -> None:
        _version_id, generation_id = chunked("The Bank lends to retail customers.")
        service.index(generation_id)
        query = query_vector(analysed(session, "photosynthesis"))

        matches = index.search_sparse(
            query.indices,
            query.values,
            limit=5,
            filters={"generation_id": str(generation_id)},
        )

        assert matches == ()

    def test_a_rare_term_outranks_a_common_one_through_server_side_idf(
        self,
        service: IndexingService,
        session: Session,
        chunked: Callable[..., tuple[UUID, UUID]],
        index: QdrantVectorIndex,
    ) -> None:
        """ADR-005's split, tested where it actually happens.

        "deposit" appears in every chunk and "debenture" in one. A query for both
        must rank the chunk holding the rare term first. Nothing in this process
        knows that — the weights sent to Qdrant carry no IDF at all — so if this
        passes, server-side IDF is doing the work it was configured for.
        """
        _version_id, generation_id = chunked(
            "Deposits grew and debentures were issued during the year.",
            "Deposits grew steadily across the retail franchise.",
            "Deposits grew in the corporate segment as well.",
            "Deposits grew because of the branch expansion programme.",
        )
        service.index(generation_id)
        query = query_vector(analysed(session, "deposit debenture"))

        matches = index.search_sparse(
            query.indices,
            query.values,
            limit=4,
            filters={"generation_id": str(generation_id)},
        )

        assert len(matches) == 4
        ranked = session.execute(
            text("SELECT text FROM chunks WHERE id = :c").bindparams(
                c=matches[0].chunk_id
            )
        ).scalar_one()
        assert "debenture" in ranked

    def test_a_term_free_chunk_is_indexed_and_dense_searchable(
        self,
        service: IndexingService,
        chunked: Callable[..., tuple[UUID, UUID]],
        index: QdrantVectorIndex,
    ) -> None:
        """Every token a stopword. Real, and not a failure (§20.12)."""
        _version_id, generation_id = chunked("the and of to a")

        result = service.index(generation_id)

        assert result.indexed == 1
        assert index.count(filters={"generation_id": str(generation_id)}) == 1


class TestReplay:
    def test_replaying_rewrites_rather_than_duplicates(
        self,
        service: IndexingService,
        session: Session,
        chunked: Callable[..., tuple[UUID, UUID]],
        index: QdrantVectorIndex,
    ) -> None:
        """§29.9: point identifiers are derived, so a re-run overwrites.

        Driven by returning the generation to shadow and the events to pending,
        which is what a crash mid-run leaves behind.
        """
        _version_id, generation_id = chunked("Revenue rose.", "Costs fell.")
        service.index(generation_id)
        session.execute(
            text(
                "UPDATE index_outbox SET state = 'pending', completed_at = NULL"
                " WHERE generation_id = :g"
            ).bindparams(g=generation_id)
        )
        session.execute(
            text(
                "UPDATE generations SET state = 'shadow', activated_at = NULL"
                " WHERE id = :g"
            ).bindparams(g=generation_id)
        )

        result = service.index(generation_id)

        assert result.indexed == 2
        assert index.count(filters={"generation_id": str(generation_id)}) == 2
