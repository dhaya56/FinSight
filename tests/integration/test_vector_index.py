"""Integration tests for the Qdrant adapter.

Against the real service, because every property under test is a property of
Qdrant rather than of our arithmetic: that server-side IDF ranks the way BM25
should, that a filter cannot be escaped by a close vector, that replaying a write
rewrites rather than duplicates.

The sharpest is :meth:`TestSearch.test_a_search_returns_chunk_ids_not_point_ids`.
Point identifiers are derived from chunk, model and configuration (§29.9) and
resolve to nothing in PostgreSQL. An earlier version did not request the payload
and returned those derived identifiers, so every search found matches that could
not be read back — and the symptom was an empty result set, not an error.
"""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from qdrant_client import QdrantClient, models

from finsight.config.settings import get_settings
from finsight.vector_index.port import (
    IndexedChunk,
    VectorIndex,
    VectorIndexShapeError,
)
from finsight.vector_index.qdrant_index import (
    FILTERED_FIELDS,
    QdrantVectorIndex,
    collection_name,
    point_id,
)

pytestmark = pytest.mark.integration

DIMENSIONS = 8


def vector(*leading: float) -> tuple[float, ...]:
    values = list(leading) + [0.0] * (DIMENSIONS - len(leading))
    return tuple(values[:DIMENSIONS])


A = vector(1.0)
B = vector(0.0, 1.0)


@pytest.fixture
def index() -> Iterator[QdrantVectorIndex]:
    """A collection of its own, dropped afterwards.

    Named for the test model so it cannot collide with the real one, which the
    naming scheme makes automatic.
    """
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url, timeout=30)
    built = QdrantVectorIndex(
        client=client,
        model=f"test-{uuid4().hex[:8]}",
        dimensions=DIMENSIONS,
        config_version="1",
    )
    built.ensure_collection()
    try:
        yield built
    finally:
        client.delete_collection(built.collection)
        client.close()


def chunk(
    chunk_id: UUID,
    dense: tuple[float, ...],
    *,
    sparse: tuple[tuple[int, ...], tuple[float, ...]] = ((), ()),
    **payload: object,
) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id,
        dense=dense,
        sparse_indices=sparse[0],
        sparse_values=sparse[1],
        payload=payload,
    )


class TestCollection:
    def test_the_adapter_satisfies_the_port(self, index: QdrantVectorIndex) -> None:
        assert isinstance(index, VectorIndex)

    def test_the_name_encodes_the_model_and_width(self) -> None:
        """A model change must target a different collection automatically.

        Mixing two vector spaces of the same width has no error to raise — the
        shapes agree and every distance is meaningless — so the naming removes the
        possibility instead of detecting it.
        """
        assert collection_name("nomic-embed-text", 768) != collection_name(
            "bge-m3", 768
        )
        assert collection_name("nomic-embed-text", 768) != collection_name(
            "nomic-embed-text", 1024
        )

    def test_preparing_twice_is_safe(self, index: QdrantVectorIndex) -> None:
        index.ensure_collection()
        index.ensure_collection()

    def test_an_existing_collection_of_the_wrong_width_is_refused(self) -> None:
        """Defence in depth behind the naming scheme.

        The name encodes the width, so the ordinary route cannot produce this —
        a different width simply targets a different collection. What this guards
        is a collection created by an older naming scheme, or mutated outside the
        application, which would otherwise be adopted and would accept writes
        into a space where every distance is meaningless.
        """
        model = f"test-{uuid4().hex[:8]}"
        client = QdrantClient(url=get_settings().qdrant_url, timeout=30)
        built = QdrantVectorIndex(
            client=client, model=model, dimensions=DIMENSIONS, config_version="1"
        )
        # Pre-create it under the expected name but with the wrong shape.
        client.create_collection(
            collection_name=built.collection,
            vectors_config={
                "dense": models.VectorParams(
                    size=DIMENSIONS + 1, distance=models.Distance.COSINE
                )
            },
        )
        try:
            with pytest.raises(VectorIndexShapeError, match="dense vectors of"):
                built.ensure_collection()
        finally:
            client.delete_collection(built.collection)
            client.close()

    def test_an_existing_collection_without_sparse_vectors_is_refused(self) -> None:
        """Lexical retrieval would silently return nothing."""
        model = f"test-{uuid4().hex[:8]}"
        client = QdrantClient(url=get_settings().qdrant_url, timeout=30)
        built = QdrantVectorIndex(
            client=client, model=model, dimensions=DIMENSIONS, config_version="1"
        )
        client.create_collection(
            collection_name=built.collection,
            vectors_config={
                "dense": models.VectorParams(
                    size=DIMENSIONS, distance=models.Distance.COSINE
                )
            },
        )
        try:
            with pytest.raises(VectorIndexShapeError, match="no 'bm25'"):
                built.ensure_collection()
        finally:
            client.delete_collection(built.collection)
            client.close()


class TestWriting:
    def test_chunks_are_stored(self, index: QdrantVectorIndex) -> None:
        assert index.upsert([chunk(uuid4(), A)]) == 1
        assert index.count() == 1

    def test_writing_nothing_touches_nothing(self, index: QdrantVectorIndex) -> None:
        assert index.upsert([]) == 0

    def test_replaying_a_write_rewrites_rather_than_duplicates(
        self, index: QdrantVectorIndex
    ) -> None:
        """§29.9. The indexer must be safe to re-run after a crash mid-batch."""
        same = uuid4()
        index.upsert([chunk(same, A)])
        index.upsert([chunk(same, A)])

        assert index.count() == 1

    def test_point_identifiers_are_deterministic(self) -> None:
        one = uuid4()

        assert point_id(one, model="m", config_version="1") == point_id(
            one, model="m", config_version="1"
        )

    def test_a_new_embedding_configuration_writes_new_points(self) -> None:
        """An active generation may still be serving the old vectors (§11.12)."""
        one = uuid4()

        assert point_id(one, model="m", config_version="1") != point_id(
            one, model="m", config_version="2"
        )

    def test_a_chunk_with_no_terms_is_still_stored(
        self, index: QdrantVectorIndex
    ) -> None:
        """A row of punctuation yields no lexemes. It stays dense-searchable."""
        only_dense = uuid4()
        index.upsert([chunk(only_dense, A)])

        assert index.search_dense(A, limit=1)[0].chunk_id == only_dense


class TestSearch:
    def test_a_search_returns_chunk_ids_not_point_ids(
        self, index: QdrantVectorIndex
    ) -> None:
        """Point ids resolve to nothing in PostgreSQL.

        Returning them made every search find matches that could not be read
        back, and the symptom was an empty result set rather than an error.
        """
        known = uuid4()
        index.upsert([chunk(known, A)])

        found = index.search_dense(A, limit=1)

        assert found[0].chunk_id == known

    def test_dense_search_ranks_by_similarity(
        self, index: QdrantVectorIndex
    ) -> None:
        near, far = uuid4(), uuid4()
        index.upsert([chunk(near, A), chunk(far, B)])

        assert [m.chunk_id for m in index.search_dense(A, limit=2)] == [near, far]

    def test_sparse_search_ranks_by_term_weight(
        self, index: QdrantVectorIndex
    ) -> None:
        """Server-side IDF, which is the half of BM25 needing corpus statistics."""
        heavy, light = uuid4(), uuid4()
        index.upsert(
            [
                chunk(heavy, A, sparse=((42,), (5.0,))),
                chunk(light, B, sparse=((42,), (0.5,))),
            ]
        )

        assert [m.chunk_id for m in index.search_sparse([42], [1.0], limit=2)] == [
            heavy,
            light,
        ]

    def test_a_query_with_no_terms_matches_nothing(
        self, index: QdrantVectorIndex
    ) -> None:
        """Correct, and not a failure: the dense side still answers."""
        index.upsert([chunk(uuid4(), A, sparse=((1,), (1.0,)))])

        assert index.search_sparse([], [], limit=5) == ()


class TestHardFilters:
    def test_a_filter_excludes_non_matching_chunks(
        self, index: QdrantVectorIndex
    ) -> None:
        mine, theirs = uuid4(), uuid4()
        index.upsert(
            [
                chunk(mine, A, generation_id="g1"),
                chunk(theirs, A, generation_id="g2"),
            ]
        )

        found = index.search_dense(A, limit=5, filters={"generation_id": "g1"})

        assert [m.chunk_id for m in found] == [mine]

    def test_an_identical_vector_cannot_escape_its_filter(
        self, index: QdrantVectorIndex
    ) -> None:
        """§7: similarity must never override a hard filter.

        Both chunks hold the *same* vector, so only the filter separates them.
        """
        wanted, excluded = uuid4(), uuid4()
        index.upsert(
            [
                chunk(wanted, A, issuer_name="Infosys Limited"),
                chunk(excluded, A, issuer_name="HDFC Bank Limited"),
            ]
        )

        found = index.search_dense(
            A, limit=5, filters={"issuer_name": "Infosys Limited"}
        )

        assert [m.chunk_id for m in found] == [wanted]

    def test_filters_are_a_conjunction(self, index: QdrantVectorIndex) -> None:
        """Every §20.2 field must match; an OR would let a close vector escape."""
        both, one = uuid4(), uuid4()
        index.upsert(
            [
                chunk(both, A, generation_id="g1", evidence_type="narrative"),
                chunk(one, A, generation_id="g1", evidence_type="table_derived"),
            ]
        )

        found = index.search_dense(
            A,
            limit=5,
            filters={"generation_id": "g1", "evidence_type": "narrative"},
        )

        assert [m.chunk_id for m in found] == [both]

    def test_the_same_filters_apply_to_sparse_search(
        self, index: QdrantVectorIndex
    ) -> None:
        """One collection means a filter cannot be enforced on one retriever only."""
        mine, theirs = uuid4(), uuid4()
        index.upsert(
            [
                chunk(mine, A, sparse=((42,), (1.0,)), generation_id="g1"),
                chunk(theirs, B, sparse=((42,), (9.0,)), generation_id="g2"),
            ]
        )

        found = index.search_sparse(
            [42], [1.0], limit=5, filters={"generation_id": "g1"}
        )

        assert [m.chunk_id for m in found] == [mine]

    def test_counting_honours_filters(self, index: QdrantVectorIndex) -> None:
        """Reconciliation against PostgreSQL needs a scoped count (§29.11)."""
        index.upsert(
            [
                chunk(uuid4(), A, generation_id="g1"),
                chunk(uuid4(), B, generation_id="g2"),
            ]
        )

        assert index.count(filters={"generation_id": "g1"}) == 1
        assert index.count() == 2


class TestProductionConfiguration:
    """Settings that fail silently when wrong, so each is asserted explicitly."""

    def test_every_filtered_field_has_a_payload_index(
        self, index: QdrantVectorIndex
    ) -> None:
        """The full-scan trap.

        Filtering on an unindexed payload field makes Qdrant scan every
        candidate's payload. Measured at 20,000 points: 23.5 ms without an index
        against 11.5 ms with one, and the gap widens because the scan is linear.
        Nothing errors — the query just gets slower as the corpus grows.
        """
        client = QdrantClient(url=get_settings().qdrant_url, timeout=30)
        schema = client.get_collection(index.collection).payload_schema
        client.close()

        assert set(FILTERED_FIELDS) <= set(schema)

    def test_hnsw_parameters_are_stated_not_inherited(
        self, index: QdrantVectorIndex
    ) -> None:
        """A future Qdrant changing its defaults would change recall silently."""
        client = QdrantClient(url=get_settings().qdrant_url, timeout=30)
        dense = client.get_collection(index.collection).config.params.vectors["dense"]
        client.close()

        assert dense.hnsw_config is not None
        assert dense.hnsw_config.m == 16
        assert dense.hnsw_config.ef_construct == 100

    def test_the_distance_metric_is_cosine(self, index: QdrantVectorIndex) -> None:
        """Dot measured 1.4x faster and is not used.

        The two are identical for unit vectors, which the embedding port
        guarantees — but Qdrant normalises on insert under cosine, so cosine stays
        correct if that guarantee is ever violated while dot would silently rank
        by vector magnitude.
        """
        client = QdrantClient(url=get_settings().qdrant_url, timeout=30)
        dense = client.get_collection(index.collection).config.params.vectors["dense"]
        client.close()

        assert dense.distance == models.Distance.COSINE

    def test_no_quantization_is_configured(self, index: QdrantVectorIndex) -> None:
        """Quantization trades recall for memory, and recall is the product here.

        A subtly different nearest neighbour is a subtly wrong answer about a
        filing. §22.6 has measured no recall cost, so there is nothing to justify
        the trade.
        """
        client = QdrantClient(url=get_settings().qdrant_url, timeout=30)
        config = client.get_collection(index.collection).config
        client.close()

        assert config.quantization_config is None

    def test_sparse_vectors_use_server_side_idf(
        self, index: QdrantVectorIndex
    ) -> None:
        client = QdrantClient(url=get_settings().qdrant_url, timeout=30)
        sparse = client.get_collection(index.collection).config.params.sparse_vectors
        client.close()

        assert sparse["bm25"].modifier == models.Modifier.IDF


class TestSparseValidation:
    def test_mismatched_sparse_indices_and_values_are_refused(
        self, index: QdrantVectorIndex
    ) -> None:
        """A shifted pairing scores nonsense on every lexical query.

        The point would look perfectly healthy: right dimensions, right payload,
        right id. Only its lexical scores would be meaningless.
        """
        with pytest.raises(VectorIndexShapeError, match="pairing"):
            index.upsert(
                [chunk(uuid4(), A, sparse=((1, 2, 3), (0.5, 0.5)))]
            )

    def test_a_chunk_with_no_terms_is_accepted_deliberately(
        self, index: QdrantVectorIndex
    ) -> None:
        """A row of punctuation yields no lexemes, which is a real outcome.

        It stays dense-searchable and is invisible to lexical search, which is
        correct — there is nothing to match lexically.
        """
        termless = uuid4()
        index.upsert([chunk(termless, A)])

        assert index.search_dense(A, limit=1)[0].chunk_id == termless
