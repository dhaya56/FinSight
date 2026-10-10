"""Integration tests for the embedding cache, against real PostgreSQL.

Four claims the cache rests on cannot be proved without a database, and each of them
is the difference between a performance feature and a silent retrieval defect.

**A cached vector is bit-identical, not approximately equal.** The column is
``double precision`` precisely so the round trip loses nothing. A float32 column
would have been a quarter of the size and would have returned vectors that differ
from a fresh embedding in their last bits — enough to reorder two near-identical
candidates, and impossible to notice.

**A digest collision is a miss, not a wrong answer.** The stored text is compared
against the requested text on every hit. The test forces the case a hash function is
not expected to produce, by writing a row whose digest belongs to different text.

**A re-insert does not overwrite.** Two indexers embedding the same corpus is
ordinary, and a vector live points were already ranked against must not be replaced.

**The constraints hold.** A zero-length vector, or one whose width disagrees with the
recorded dimensionality, is refused by the database rather than indexed.
"""

import datetime
from collections.abc import Iterator

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finsight.persistence.database import dispose_engine, get_engine
from finsight.persistence.repositories.embedding_cache import (
    CacheKey,
    EmbeddingCacheRepository,
    digest_of,
)
from finsight.persistence.tables.embedding_cache import (
    KIND_DOCUMENT,
    KIND_QUERY,
    EmbeddingCacheEntry,
)

pytestmark = pytest.mark.integration

MODEL = "integration-fake"
VERSION = "test"

AWKWARD = (
    0.1234567890123456,
    -0.9876543210987654,
    1e-17,
    0.3333333333333333,
)
"""Values chosen to be unrepresentable in float32.

A ``real`` column would return 0.12345679 for the first of these. Asserting exact
equality against these four is what makes the precision claim testable rather than
asserted in a docstring.
"""


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
def repository(session: Session) -> EmbeddingCacheRepository:
    return EmbeddingCacheRepository(session)


def key(text: str, *, kind: str = KIND_DOCUMENT, version: str = VERSION) -> CacheKey:
    return CacheKey(text=text, model=MODEL, config_version=version, kind=kind)


class TestRoundTrip:
    def test_a_stored_vector_comes_back_bit_identical(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        wanted = key("Revenue from operations rose during the year.")
        repository.store({wanted: AWKWARD}, dimensions=len(AWKWARD))

        found, _ids = repository.fetch([wanted])

        assert found[wanted] == AWKWARD

    def test_nothing_stored_is_nothing_found(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        found, ids = repository.fetch([key("never embedded")])

        assert found == {}
        assert ids == ()

    def test_fetching_nothing_touches_the_database_for_nothing(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        assert repository.fetch([]) == ({}, ())
        assert repository.store({}, dimensions=4) == 0

    def test_one_lookup_serves_a_whole_batch(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        """Thirty-two round trips to save thirty-two model calls is not a cache."""
        wanted = [key(f"chunk {index}") for index in range(32)]
        repository.store(dict.fromkeys(wanted, AWKWARD), dimensions=len(AWKWARD))

        found, ids = repository.fetch(wanted)

        assert len(found) == 32
        assert len(ids) == 32

    def test_a_long_passage_is_cached_despite_the_btree_key_limit(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        """The index is on the digest, so the text may be any length a chunk can be."""
        wanted = key("x" * 6_000)
        repository.store({wanted: AWKWARD}, dimensions=len(AWKWARD))

        found, _ids = repository.fetch([wanted])

        assert found[wanted] == AWKWARD


class TestTheKeyIsEverythingThatDeterminesTheVector:
    def test_a_document_and_a_query_are_separate_entries(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        document = key("credit risk", kind=KIND_DOCUMENT)
        query = key("credit risk", kind=KIND_QUERY)
        repository.store({document: AWKWARD}, dimensions=len(AWKWARD))

        found, _ids = repository.fetch([query])

        assert found == {}

    def test_a_different_configuration_version_is_a_miss(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        repository.store({key("text"): AWKWARD}, dimensions=len(AWKWARD))

        found, _ids = repository.fetch([key("text", version="next")])

        assert found == {}

    def test_a_different_model_is_a_miss(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        repository.store({key("text"): AWKWARD}, dimensions=len(AWKWARD))

        found, _ids = repository.fetch(
            [CacheKey(text="text", model="other", config_version=VERSION, kind=KIND_DOCUMENT)]
        )

        assert found == {}

    def test_both_kinds_of_the_same_text_can_coexist(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        document = key("credit risk", kind=KIND_DOCUMENT)
        query = key("credit risk", kind=KIND_QUERY)
        other = (0.5, 0.5, 0.5, 0.5)

        repository.store(
            {document: AWKWARD, query: other}, dimensions=len(AWKWARD)
        )
        found, _ids = repository.fetch([document, query])

        assert found[document] == AWKWARD
        assert found[query] == other


class TestCollisionsBecomeMisses:
    def test_a_digest_whose_text_differs_is_ignored(
        self, session: Session, repository: EmbeddingCacheRepository
    ) -> None:
        """The case SHA-256 is not expected to produce, forced by hand.

        A row is written carrying the digest of one passage and the text of
        another. A cache that trusted its digest would return this vector for the
        first passage — a well-formed vector, plausible distances, and a passage
        ranked as though it said something else. Verifying the text makes it a miss.
        """
        asked_for = key("Revenue from operations rose during the year.")
        session.execute(
            insert(EmbeddingCacheEntry).values(
                input_digest=digest_of(asked_for.text),
                input_text="Finance costs fell during the year.",
                model=MODEL,
                config_version=VERSION,
                kind=KIND_DOCUMENT,
                vector=list(AWKWARD),
                dimensions=len(AWKWARD),
            )
        )

        found, ids = repository.fetch([asked_for])

        assert found == {}
        assert ids == ()

    def test_a_collided_entry_does_not_poison_its_batch(
        self, session: Session, repository: EmbeddingCacheRepository
    ) -> None:
        good = key("a genuine passage")
        collided = key("Revenue from operations rose during the year.")
        repository.store({good: AWKWARD}, dimensions=len(AWKWARD))
        session.execute(
            insert(EmbeddingCacheEntry).values(
                input_digest=digest_of(collided.text),
                input_text="something else entirely",
                model=MODEL,
                config_version=VERSION,
                kind=KIND_DOCUMENT,
                vector=list(AWKWARD),
                dimensions=len(AWKWARD),
            )
        )

        found, _ids = repository.fetch([good, collided])

        assert set(found) == {good}


class TestNothingIsOverwritten:
    def test_storing_an_existing_key_again_changes_nothing(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        wanted = key("text")
        repository.store({wanted: AWKWARD}, dimensions=len(AWKWARD))

        landed = repository.store(
            {wanted: (0.0, 0.0, 0.0, 1.0)}, dimensions=len(AWKWARD)
        )
        found, _ids = repository.fetch([wanted])

        assert landed == 0
        assert found[wanted] == AWKWARD

    def test_the_count_returned_is_what_was_genuinely_new(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        repository.store({key("one"): AWKWARD}, dimensions=len(AWKWARD))

        landed = repository.store(
            {key("one"): AWKWARD, key("two"): AWKWARD}, dimensions=len(AWKWARD)
        )

        assert landed == 1


class TestConstraints:
    def test_an_empty_vector_is_refused(self, session: Session) -> None:
        with pytest.raises(IntegrityError, match="vector_not_empty"):
            session.execute(
                insert(EmbeddingCacheEntry).values(
                    input_digest=digest_of("text"),
                    input_text="text",
                    model=MODEL,
                    config_version=VERSION,
                    kind=KIND_DOCUMENT,
                    vector=[],
                    dimensions=0,
                )
            )

    def test_a_width_disagreeing_with_the_vector_is_refused(
        self, session: Session
    ) -> None:
        """A model that changed width fails here, not at the vector store."""
        with pytest.raises(IntegrityError, match="dimensions_match_vector"):
            session.execute(
                insert(EmbeddingCacheEntry).values(
                    input_digest=digest_of("text"),
                    input_text="text",
                    model=MODEL,
                    config_version=VERSION,
                    kind=KIND_DOCUMENT,
                    vector=list(AWKWARD),
                    dimensions=768,
                )
            )

    def test_an_unknown_kind_is_refused(self, session: Session) -> None:
        """``passage`` and not something longer: the column is varchar(16), and a
        longer probe is rejected for its length before the CHECK is consulted, which
        would leave the constraint unwatched while the test still passed."""
        with pytest.raises(IntegrityError, match="kind_known"):
            session.execute(
                insert(EmbeddingCacheEntry).values(
                    input_digest=digest_of("text"),
                    input_text="text",
                    model=MODEL,
                    config_version=VERSION,
                    kind="passage",
                    vector=list(AWKWARD),
                    dimensions=len(AWKWARD),
                )
            )


class TestPruning:
    def test_use_is_recorded_so_disuse_can_be_measured(
        self, session: Session, repository: EmbeddingCacheRepository
    ) -> None:
        wanted = key("text")
        repository.store({wanted: AWKWARD}, dimensions=len(AWKWARD))
        session.execute(
            EmbeddingCacheEntry.__table__.update()
            .where(EmbeddingCacheEntry.input_digest == digest_of(wanted.text))
            .values(last_used_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC))
        )

        _found, ids = repository.fetch([wanted])
        repository.touch(ids)

        used = session.execute(
            select(EmbeddingCacheEntry.last_used_at).where(
                EmbeddingCacheEntry.input_digest == digest_of(wanted.text)
            )
        ).scalar_one()
        assert used.year > 2020

    def test_entries_unused_since_a_cutoff_are_counted_then_removed(
        self, session: Session, repository: EmbeddingCacheRepository
    ) -> None:
        repository.store(
            {key("stale"): AWKWARD, key("fresh"): AWKWARD},
            dimensions=len(AWKWARD),
        )
        session.execute(
            EmbeddingCacheEntry.__table__.update()
            .where(EmbeddingCacheEntry.input_text == "stale")
            .values(last_used_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC))
        )
        cutoff = datetime.datetime(2021, 1, 1, tzinfo=datetime.UTC)

        counted = repository.count_unused_since(cutoff)
        removed = repository.prune_unused_since(cutoff)

        assert (counted, removed) == (1, 1)
        assert repository.fetch([key("fresh")])[0]

    def test_pruning_nothing_removes_nothing(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        repository.store({key("fresh"): AWKWARD}, dimensions=len(AWKWARD))

        removed = repository.prune_unused_since(
            datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
        )

        assert removed == 0

    def test_a_census_reports_each_kind_without_printing_any_text(
        self, repository: EmbeddingCacheRepository
    ) -> None:
        """Asserted as a delta, because a real run may have filled this table.

        An absolute count would pass on a clean database and fail on the developer's,
        which is the kind of test that gets deleted rather than read.
        """
        before = repository.statistics()
        repository.store(
            {
                key("a document"): AWKWARD,
                key("a question", kind=KIND_QUERY): AWKWARD,
            },
            dimensions=len(AWKWARD),
        )

        after = repository.statistics()

        assert after.entries - before.entries == 2
        assert after.document_entries - before.document_entries == 1
        assert after.query_entries - before.query_entries == 1
        assert MODEL in after.models
        assert after.oldest_use is not None
