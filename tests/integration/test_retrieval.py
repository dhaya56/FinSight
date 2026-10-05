"""Lexical retrieval against real PostgreSQL and real Qdrant.

What cannot be proved without both:

**The filters are enforced in the engine, not after it.** Filtering a ranked list
afterwards is the post-filtering anti-pattern — a query restricted to one issuer
would rank across the whole corpus and then discard most of what it found. Only a
real engine shows which happened.

**The degradation path works.** ADR-005 promised PostgreSQL full-text search as
§20.12's fallback and, until this commit, nothing queried the GIN index. A fallback
that has never run is a claim rather than a path.

**Both retrievers are bounded to active generations.** The committed development
corpus has its own active generations, so these tests scope by document version — and
one test asserts the generation bound directly, because that is the filter retrieval
must never omit.
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
from finsight.indexing.service import IndexingService, TransactionalIndexingRecorder
from finsight.persistence.database import dispose_engine, get_engine
from finsight.persistence.repositories.chunks import ChunkRepository
from finsight.persistence.repositories.documents import DocumentRepository
from finsight.persistence.repositories.generations import GenerationRepository
from finsight.retrieval.contracts import (
    DEGRADED_LEXICAL_FALLBACK,
    Candidate,
    RetrievalFilters,
    Retriever,
)
from finsight.retrieval.lexical import BM25Retriever, FullTextRetriever
from finsight.retrieval.service import LexicalRetrievalService
from finsight.vector_index.port import Span, VectorIndexUnavailableError
from finsight.vector_index.qdrant_index import QdrantVectorIndex

pytestmark = pytest.mark.integration

CONFIG = "english"
DIMENSIONS = 32

BODIES = (
    "The Bank lends to retail customers and monitors credit risk closely.",
    "Total borrowings stood at 10,000 crore at the year end.",
    "Unrelated commentary about brand investment and cricket sponsorship.",
)


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
def scope(session: Session) -> Callable[[], Iterator[Session]]:
    @contextmanager
    def _scope() -> Iterator[Session]:
        yield session

    return _scope


@pytest.fixture
def index() -> Iterator[QdrantVectorIndex]:
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url, timeout=30)
    built = QdrantVectorIndex(
        client=client,
        model=f"test-retrieval-{uuid.uuid4().hex[:8]}",
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
def indexed(
    session: Session, index: QdrantVectorIndex, scope: Callable[[], Iterator[Session]]
) -> Callable[..., tuple[UUID, UUID]]:
    """Build an activated generation over ``BODIES``. Returns version and generation."""

    def _indexed(
        *,
        issuer: str = "Probe Retrieval Limited",
        period_end: str = "2025-03-31",
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
                " document_type, fiscal_period, period_end, reporting_basis,"
                " currency, source)"
                " VALUES (:v, :issuer, 'annual_report', 'FY2024-25', :period_end,"
                " 'both', 'INR', 'corpus_manifest')"
            ),
            {"v": version_id, "issuer": issuer, "period_end": period_end},
        )
        run_id = session.execute(
            text(
                "INSERT INTO extraction_runs (document_version_id, format,"
                " producer_policy, config_version, state, element_count)"
                " VALUES (:v, 'pdf', 'pdf-native', '2', 'succeeded', 0) RETURNING id"
            ),
            {"v": version_id},
        ).scalar_one()
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
            for n in range(len(BODIES))
        ]
        generation_id = GenerationRepository(session).open(
            document_version_id=version_id, extraction_run_id=run_id
        )
        ChunkRepository(session).record(
            generation_id=generation_id,
            document_version_id=version_id,
            chunks=[
                DerivedChunk(
                    text=body,
                    source_element_ids=(elements[n],),
                    page_numbers=(n + 1,),
                    heading_path=("7. Risk factors",),
                    evidence_type=EvidenceType.NARRATIVE,
                    role=ChunkRole.CHILD,
                    token_count=len(body.split()),
                    char_count=len(body),
                    ordinal=n,
                    config_version="3",
                )
                for n, body in enumerate(BODIES)
            ],
            text_search_config=CONFIG,
        )
        IndexingService(
            recorder=TransactionalIndexingRecorder(session_scope_factory=scope),
            embedder=FakeEmbedder(dimensions=DIMENSIONS),
            index=index,
            batch_size=8,
        ).index(generation_id)
        return version_id, generation_id

    return _indexed


@pytest.fixture
def bm25(
    index: QdrantVectorIndex, scope: Callable[[], Iterator[Session]]
) -> BM25Retriever:
    return BM25Retriever(
        index=index, text_search_config=CONFIG, session_scope_factory=scope
    )


@pytest.fixture
def fts(scope: Callable[[], Iterator[Session]]) -> FullTextRetriever:
    return FullTextRetriever(text_search_config=CONFIG, session_scope_factory=scope)


def texts_of(session: Session, candidates: tuple[Candidate, ...]) -> list[str]:
    """Resolve candidates to text through PostgreSQL, which is what §10.7 requires."""
    return [
        session.execute(
            text("SELECT text FROM chunks WHERE id = :c").bindparams(c=c.chunk_id)
        ).scalar_one()
        for c in candidates
    ]


class TestBM25:
    def test_a_stemmed_query_finds_the_right_chunk(
        self,
        bm25: BM25Retriever,
        session: Session,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        version_id, _generation_id = indexed()

        found = bm25.search(
            "lending",
            filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        )

        assert len(found) == 1
        assert "lends" in texts_of(session, found)[0]
        assert found[0].retriever == Retriever.BM25
        assert found[0].rank == 1

    def test_a_grouped_figure_is_found_by_either_spelling(
        self,
        bm25: BM25Retriever,
        session: Session,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """The normalisation reaches the query because the retriever uses it."""
        version_id, _generation_id = indexed()
        filters = RetrievalFilters(document_version_id=version_id)

        for spelling in ("10,000", "10000"):
            found = bm25.search(spelling, filters=filters, limit=5)
            assert len(found) == 1, spelling
            assert "10,000 crore" in texts_of(session, found)[0]

    def test_a_query_with_no_indexable_term_returns_nothing(
        self,
        bm25: BM25Retriever,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """Not an error, and not a degradation — the dense side still answers."""
        version_id, _generation_id = indexed()

        found = bm25.search(
            "the and of to", filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        )

        assert found == ()

    def test_ranks_are_dense_and_ordered_by_score(
        self,
        bm25: BM25Retriever,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        version_id, _generation_id = indexed()

        found = bm25.search(
            "credit risk customers borrowings crore",
            filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        )

        assert [c.rank for c in found] == list(range(1, len(found) + 1))
        assert list(found) == sorted(found, key=lambda c: -c.score)


class TestHardFilters:
    def test_an_issuer_filter_excludes_another_issuer(
        self,
        bm25: BM25Retriever,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        indexed(issuer="Alpha Issuer Limited")
        indexed(issuer="Beta Issuer Limited")

        alpha = bm25.search(
            "credit risk", filters=RetrievalFilters(issuer_name="Alpha Issuer Limited"),
            limit=10,
        )
        absent = bm25.search(
            "credit risk", filters=RetrievalFilters(issuer_name="Nobody Limited"),
            limit=10,
        )

        assert len(alpha) == 1
        assert absent == ()

    def test_a_year_span_selects_by_period(
        self,
        bm25: BM25Retriever,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """What ``fiscal_period`` cannot express, enforced in the engine."""
        indexed(issuer="Recent Filer Limited", period_end="2025-03-31")
        indexed(issuer="Older Filer Limited", period_end="2019-03-31")

        recent = bm25.search(
            "credit risk", filters=RetrievalFilters(fiscal_year=Span(low=2023)),
            limit=10,
        )
        ancient = bm25.search(
            "credit risk", filters=RetrievalFilters(fiscal_year=Span(high=2020)),
            limit=10,
        )

        assert len(recent) == 1
        assert len(ancient) == 1
        assert recent[0].chunk_id != ancient[0].chunk_id

    def test_a_section_filter_restricts_the_search(
        self,
        bm25: BM25Retriever,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        version_id, _generation_id = indexed()
        filters = RetrievalFilters(
            document_version_id=version_id, section="7. Risk factors"
        )

        assert bm25.search("credit risk", filters=filters, limit=5)
        assert (
            bm25.search(
                "credit risk",
                filters=RetrievalFilters(
                    document_version_id=version_id, section="Nonexistent"
                ),
                limit=5,
            )
            == ()
        )

    def test_a_superseded_generation_is_not_searched(
        self,
        bm25: BM25Retriever,
        session: Session,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """The bound retrieval must never omit (§20.2, §11.13).

        The points stay in the collection — superseding does not delete them — so if
        the generation filter were dropped, they would still be returned.
        """
        version_id, generation_id = indexed()
        assert bm25.search(
            "lending", filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        )

        session.execute(
            text(
                "UPDATE generations SET state = 'superseded', activated_at = NULL"
                " WHERE id = :g"
            ).bindparams(g=generation_id)
        )

        assert bm25.search(
            "lending", filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        ) == ()


class TestFullTextFallback:
    def test_full_text_search_finds_the_same_chunk(
        self,
        fts: FullTextRetriever,
        session: Session,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """§20.12's path, exercised rather than asserted.

        Not a claim that it ranks like BM25 — it has no IDF, no saturation and no
        length normalisation — only that it reaches the right chunk.
        """
        version_id, _generation_id = indexed()

        found = fts.search(
            "lending",
            filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        )

        assert len(found) == 1
        assert "lends" in texts_of(session, found)[0]
        assert found[0].retriever == Retriever.POSTGRES_FTS

    def test_the_fallback_applies_the_same_filters(
        self,
        fts: FullTextRetriever,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """In SQL, not after ranking — the post-filtering anti-pattern."""
        indexed(issuer="Gamma Issuer Limited")
        indexed(issuer="Delta Issuer Limited")

        gamma = fts.search(
            "credit risk", filters=RetrievalFilters(issuer_name="Gamma Issuer Limited"),
            limit=10,
        )

        assert len(gamma) == 1

    def test_the_fallback_honours_a_year_span(
        self,
        fts: FullTextRetriever,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        indexed(issuer="Span FTS Recent Limited", period_end="2025-03-31")
        indexed(issuer="Span FTS Old Limited", period_end="2018-03-31")

        found = fts.search(
            "credit risk", filters=RetrievalFilters(fiscal_year=Span(high=2020)),
            limit=10,
        )

        assert len(found) == 1

    def test_the_fallback_searches_only_active_generations(
        self,
        fts: FullTextRetriever,
        session: Session,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        version_id, generation_id = indexed()
        session.execute(
            text(
                "UPDATE generations SET state = 'superseded', activated_at = NULL"
                " WHERE id = :g"
            ).bindparams(g=generation_id)
        )

        assert fts.search(
            "lending", filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        ) == ()

    def test_the_fallback_finds_a_grouped_figure(
        self,
        fts: FullTextRetriever,
        session: Session,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """Both lexical paths share the normalisation, so both must behave."""
        version_id, _generation_id = indexed()

        found = fts.search(
            "10000",
            filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        )

        assert len(found) == 1
        assert "10,000 crore" in texts_of(session, found)[0]


class TestServiceDegradation:
    def test_an_outage_falls_back_to_full_text_and_flags_it(
        self,
        fts: FullTextRetriever,
        session: Session,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        """The whole point of keeping the GIN index populated."""
        version_id, _generation_id = indexed()

        class DownRetriever:
            name = Retriever.BM25

            def search(
                self, query: str, *, filters: RetrievalFilters, limit: int
            ) -> tuple[Candidate, ...]:
                raise VectorIndexUnavailableError("qdrant is unreachable")

        service = LexicalRetrievalService(primary=DownRetriever(), fallback=fts)

        result = service.search(
            "lending",
            filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        )

        assert result.degraded == (DEGRADED_LEXICAL_FALLBACK,)
        assert result.retriever == Retriever.POSTGRES_FTS
        assert len(result.candidates) == 1
        assert "lends" in texts_of(session, result.candidates)[0]

    def test_the_healthy_path_uses_bm25_and_flags_nothing(
        self,
        bm25: BM25Retriever,
        fts: FullTextRetriever,
        indexed: Callable[..., tuple[UUID, UUID]],
    ) -> None:
        version_id, _generation_id = indexed()
        service = LexicalRetrievalService(primary=bm25, fallback=fts)

        result = service.search(
            "lending",
            filters=RetrievalFilters(document_version_id=version_id),
            limit=5,
        )

        assert result.is_degraded is False
        assert result.retriever == Retriever.BM25
