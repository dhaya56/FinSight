"""The cache must change throughput and nothing else.

Everything here is provable without a database, which is why it is here: ordering,
deduplication, the shape of the key, the transaction boundary, and what happens when
the cache itself is broken. The claims that need real PostgreSQL — that a stored
vector comes back bit-identical, that a digest collision becomes a miss, that a
concurrent insert does not overwrite — are in
``tests/integration/test_embedding_cache_persistence.py``.

**The recording scope.** ``_RecordingScope`` holds the vectors a run stored and
hands them back on the next lookup, so a second call exercises the hit path with no
database. It also records *when* it was open, which is how the §29.7 test proves no
transaction was held across the model call.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from finsight.config.settings import Settings
from finsight.embedding.cache import (
    CacheCounters,
    CachingEmbedder,
    build_cached_embedder,
    cached,
    describe,
)
from finsight.embedding.fake import FakeEmbedder
from finsight.embedding.port import Vector
from finsight.persistence.repositories.embedding_cache import CacheKey

CONFIG_VERSION = "7"


@dataclass
class _Store:
    """What a cache would hold, keyed exactly as the table keys it."""

    vectors: dict[CacheKey, Vector] = field(default_factory=dict)
    opened: int = 0
    open_now: bool = False
    touched: list[int] = field(default_factory=list)


class _FakeRepository:
    """Enough of ``EmbeddingCacheRepository`` to exercise the embedder."""

    def __init__(self, store: _Store) -> None:
        self._store = store

    def fetch(
        self, keys: Sequence[CacheKey]
    ) -> tuple[dict[CacheKey, Vector], tuple[int, ...]]:
        found = {key: self._store.vectors[key] for key in keys if key in self._store.vectors}
        return found, tuple(range(len(found)))

    def store(self, vectors: dict[CacheKey, Vector], *, dimensions: int) -> int:
        self._store.vectors.update(vectors)
        return len(vectors)

    def touch(self, entry_ids: Sequence[int]) -> None:
        self._store.touched.extend(entry_ids)


@dataclass
class _Probe:
    """Records every call the embedder made to the model."""

    inner: FakeEmbedder = field(default_factory=FakeEmbedder)
    document_batches: list[tuple[str, ...]] = field(default_factory=list)
    query_calls: list[str] = field(default_factory=list)
    scope_open_during_call: bool = False
    store: _Store | None = None

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def dimensions(self) -> int:
        return self.inner.dimensions

    def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        self.document_batches.append(tuple(texts))
        self._note_scope()
        return self.inner.embed_documents(texts)

    def embed_query(self, text: str) -> Vector:
        self.query_calls.append(text)
        self._note_scope()
        return self.inner.embed_query(text)

    def _note_scope(self) -> None:
        if self.store is not None and self.store.open_now:
            self.scope_open_during_call = True

    @property
    def texts_embedded(self) -> int:
        return sum(len(batch) for batch in self.document_batches) + len(
            self.query_calls
        )


def _embedder(
    *, failing: bool = False
) -> tuple[CachingEmbedder, _Probe, _Store]:
    """A caching embedder over a fake store, with the probe that watched it."""
    store = _Store()
    probe = _Probe(store=store)

    @contextmanager
    def scope() -> Iterator[Session]:
        if failing:
            raise OperationalError("select 1", {}, Exception("cache is down"))
        store.opened += 1
        store.open_now = True
        try:
            yield _FakeRepository(store)  # type: ignore[misc]
        finally:
            store.open_now = False

    embedder = CachingEmbedder(
        inner=probe, config_version=CONFIG_VERSION, scope=scope
    )
    return embedder, probe, store


@pytest.fixture(autouse=True)
def _repository_is_the_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the scope yield a fake repository instead of a Session.

    The embedder constructs ``EmbeddingCacheRepository(session)``, so the fake is
    passed through as the session and the constructor is replaced by identity. That
    keeps the production call sequence under test — fetch, then embed, then touch
    and store — while the storage itself is a dict.
    """
    monkeypatch.setattr(
        "finsight.embedding.cache.EmbeddingCacheRepository", lambda session: session
    )


class TestIdenticalResults:
    def test_a_hit_returns_the_same_vector_the_model_returned(self) -> None:
        embedder, _probe, _store = _embedder()
        texts = ["Revenue from operations rose.", "Credit risk is monitored."]

        first = embedder.embed_documents(texts)
        second = embedder.embed_documents(texts)

        assert second == first

    def test_a_hit_is_bit_identical_and_not_merely_close(self) -> None:
        """``==`` on floats is the right assertion here, not ``approx``.

        A cached vector that is only approximately the fresh one could reorder two
        candidates whose scores differ in the last bits, which is a retrieval
        change the cache has no right to make.
        """
        embedder, _probe, _store = _embedder()
        fresh = FakeEmbedder().embed_documents(["Revenue grew."])[0]

        embedder.embed_documents(["Revenue grew."])
        hit = embedder.embed_documents(["Revenue grew."])[0]

        assert hit == fresh
        assert all(a == b for a, b in zip(hit, fresh, strict=True))

    def test_the_second_run_calls_the_model_for_nothing(self) -> None:
        embedder, probe, _store = _embedder()
        texts = ["one", "two", "three"]

        embedder.embed_documents(texts)
        embedder.embed_documents(texts)

        assert probe.texts_embedded == 3

    def test_only_changed_text_is_recomputed(self) -> None:
        """The measured case: most text survives a re-extraction unchanged."""
        embedder, probe, _store = _embedder()
        embedder.embed_documents(["kept", "kept too", "changed"])

        embedder.embed_documents(["kept", "kept too", "changed after editing"])

        assert probe.document_batches[1] == ("changed after editing",)


class TestOrder:
    """The port pairs vectors with chunks by position, so order is correctness."""

    def test_a_partially_cached_batch_keeps_its_order(self) -> None:
        embedder, _probe, _store = _embedder()
        fresh = FakeEmbedder()
        embedder.embed_documents(["b"])

        vectors = embedder.embed_documents(["a", "b", "c"])

        assert vectors == fresh.embed_documents(["a", "b", "c"])

    def test_a_repeated_text_is_embedded_once_and_returned_twice(self) -> None:
        embedder, probe, _store = _embedder()

        vectors = embedder.embed_documents(["same", "other", "same"])

        assert probe.document_batches == [("same", "other")]
        assert vectors[0] == vectors[2]
        assert vectors[1] != vectors[0]

    def test_a_fully_repeated_batch_returns_one_vector_per_position(self) -> None:
        embedder, _probe, _store = _embedder()

        assert len(embedder.embed_documents(["x", "x", "x", "x"])) == 4

    def test_an_empty_batch_asks_nothing_and_returns_nothing(self) -> None:
        embedder, probe, store = _embedder()

        assert embedder.embed_documents([]) == ()
        assert probe.texts_embedded == 0
        assert store.opened == 0


class TestTheKey:
    def test_a_document_and_a_query_do_not_share_an_entry(self) -> None:
        """The model applies different task prefixes, so one string has two vectors."""
        embedder, probe, _store = _embedder()

        embedder.embed_documents(["credit risk"])
        embedder.embed_query("credit risk")

        assert probe.document_batches == [("credit risk",)]
        assert probe.query_calls == ["credit risk"]

    def test_a_query_vector_is_the_query_vector(self) -> None:
        embedder, _probe, _store = _embedder()
        expected = FakeEmbedder().embed_query("credit risk")

        embedder.embed_documents(["credit risk"])

        assert embedder.embed_query("credit risk") == expected

    def test_a_repeated_query_is_served_from_the_cache(self) -> None:
        embedder, probe, _store = _embedder()

        first = embedder.embed_query("how did revenue change")
        second = embedder.embed_query("how did revenue change")

        assert first == second
        assert probe.query_calls == ["how did revenue change"]

    def test_a_different_configuration_version_shares_nothing(self) -> None:
        """Bumping the version must invalidate, or a re-index would reuse stale work."""
        embedder, probe, store = _embedder()
        embedder.embed_documents(["text"])

        other = CachingEmbedder(
            inner=probe,
            config_version="8",
            scope=_scope_over(store),
        )
        other.embed_documents(["text"])

        assert probe.document_batches == [("text",), ("text",)]

    def test_a_key_rejects_an_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="kind must be one of"):
            CacheKey(
                text="x", model="m", config_version="1", kind="somewhere-between"
            )


def _scope_over(store: _Store):  # type: ignore[no-untyped-def]
    """A second scope over the same store, for two-embedder tests."""

    @contextmanager
    def scope() -> Iterator[Session]:
        store.opened += 1
        store.open_now = True
        try:
            yield _FakeRepository(store)  # type: ignore[misc]
        finally:
            store.open_now = False

    return scope


class TestTransactionBoundary:
    """§29.7: no transaction may span a model call."""

    def test_no_session_is_open_while_the_model_is_called(self) -> None:
        embedder, probe, _store = _embedder()

        embedder.embed_documents(["a", "b"])

        assert probe.scope_open_during_call is False

    def test_a_miss_opens_two_scopes_not_one(self) -> None:
        """Read, embed, write — the embed is between two bounded transactions."""
        embedder, _probe, store = _embedder()

        embedder.embed_documents(["a"])

        assert store.opened == 2

    def test_a_full_hit_still_writes_once_to_record_the_use(self) -> None:
        embedder, _probe, store = _embedder()
        embedder.embed_documents(["a"])
        store.opened = 0

        embedder.embed_documents(["a"])

        assert store.opened == 2
        assert store.touched


class TestFaultTolerance:
    """A broken cache must cost time, never correctness."""

    def test_a_database_fault_still_returns_correct_vectors(self) -> None:
        embedder, _probe, _store = _embedder(failing=True)
        expected = FakeEmbedder().embed_documents(["a", "b"])

        assert embedder.embed_documents(["a", "b"]) == expected

    def test_a_database_fault_is_counted_rather_than_raised(self) -> None:
        embedder, _probe, _store = _embedder(failing=True)

        embedder.embed_documents(["a"])

        assert embedder.statistics.read_faults == 1
        assert embedder.statistics.write_faults == 1

    def test_a_model_failure_is_not_swallowed(self) -> None:
        """Only cache faults are tolerated. An unreachable model is still an error."""
        embedder, probe, _store = _embedder()

        def refuse(texts: Sequence[str]) -> tuple[Vector, ...]:
            raise RuntimeError("model is down")

        probe.embed_documents = refuse  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="model is down"):
            embedder.embed_documents(["a"])


class TestCounters:
    def test_hits_and_misses_are_counted_per_text(self) -> None:
        embedder, _probe, _store = _embedder()
        embedder.embed_documents(["a", "b"])

        embedder.embed_documents(["a", "b", "c"])

        assert embedder.statistics.hits == 2
        assert embedder.statistics.misses == 3

    def test_a_repeated_text_counts_once(self) -> None:
        """Deduplication happens before the lookup, so the rate is over distinct text."""
        embedder, _probe, _store = _embedder()

        embedder.embed_documents(["a", "a", "a"])

        assert embedder.statistics.misses == 1

    def test_the_hit_rate_of_nothing_is_zero_rather_than_an_error(self) -> None:
        assert CacheCounters().hit_rate == 0.0

    def test_the_hit_rate_is_over_lookups(self) -> None:
        assert CacheCounters(hits=3, misses=1).hit_rate == 0.75

    def test_the_report_names_hits_misses_and_rate(self) -> None:
        lines = list(describe(CacheCounters(hits=4097, misses=770, stored=770)))

        assert "4097 hit(s)" in lines[0]
        assert "770 miss(es)" in lines[0]
        assert "84.2%" in lines[0]

    def test_the_report_mentions_faults_only_when_there_were_some(self) -> None:
        clean = list(describe(CacheCounters(hits=1)))
        faulty = list(describe(CacheCounters(hits=1, read_faults=2)))

        assert not any("fault" in line for line in clean)
        assert any("fault" in line for line in faulty)


def _settings(*, cache: bool) -> Settings:
    return Settings(
        postgres_password=SecretStr("not-a-real-password-just-a-fixture"),
        embedding_cache_enabled=cache,
    )


class TestWiring:
    def test_the_cache_is_wrapped_around_the_configured_embedder(self) -> None:
        wrapped = build_cached_embedder(_settings(cache=True))

        assert isinstance(wrapped, CachingEmbedder)

    def test_switching_it_off_returns_the_adapter_itself(self) -> None:
        plain = build_cached_embedder(_settings(cache=False))

        assert not isinstance(plain, CachingEmbedder)

    def test_the_wrapper_reports_the_model_and_width_it_wraps(self) -> None:
        wrapped = cached(
            FakeEmbedder(dimensions=16),
            config_version=CONFIG_VERSION,
            scope=_scope_over(_Store()),
        )

        assert wrapped.model == FakeEmbedder().model
        assert wrapped.dimensions == 16
