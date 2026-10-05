"""Which failures degrade and which propagate.

The line matters: an unreachable index is survivable and §20.12 requires falling
back, while a collection whose shape disagrees with the configuration means the model
and the index have diverged, and falling back there hides a fault that corrupts every
later write.
"""

from uuid import uuid4

import pytest

from finsight.retrieval.contracts import (
    DEGRADED_LEXICAL_FALLBACK,
    Candidate,
    RetrievalFilters,
    Retriever,
)
from finsight.retrieval.service import LexicalRetrievalService
from finsight.vector_index.port import (
    VectorIndexShapeError,
    VectorIndexUnavailableError,
)


class FakeRetriever:
    def __init__(
        self,
        name: str,
        *,
        results: int = 2,
        raises: Exception | None = None,
    ) -> None:
        self._name = name
        self._results = results
        self._raises = raises
        self.calls: list[tuple[str, int]] = []

    @property
    def name(self) -> str:
        return self._name

    def search(
        self, query: str, *, filters: RetrievalFilters, limit: int
    ) -> tuple[Candidate, ...]:
        self.calls.append((query, limit))
        if self._raises is not None:
            raise self._raises
        return tuple(
            Candidate(
                chunk_id=uuid4(),
                score=1.0 / rank,
                rank=rank,
                retriever=self._name,
            )
            for rank in range(1, self._results + 1)
        )


def build(
    *, primary_raises: Exception | None = None, primary_results: int = 2
) -> tuple[LexicalRetrievalService, FakeRetriever, FakeRetriever]:
    primary = FakeRetriever(
        Retriever.BM25, results=primary_results, raises=primary_raises
    )
    fallback = FakeRetriever(Retriever.POSTGRES_FTS)
    return LexicalRetrievalService(primary=primary, fallback=fallback), primary, fallback


class TestHealthyPath:
    def test_bm25_answers_and_nothing_is_flagged(self) -> None:
        service, primary, fallback = build()

        result = service.search("credit risk")

        assert result.retriever == Retriever.BM25
        assert result.is_degraded is False
        assert len(result.candidates) == 2
        assert fallback.calls == []
        assert primary.calls == [("credit risk", 20)]

    def test_an_empty_result_is_not_a_degradation(self) -> None:
        """A query of stopwords matches nothing, and the system is fine.

        Flagging it would make "no answer exists" look like "the system is unwell".
        """
        service, _primary, fallback = build(primary_results=0)

        result = service.search("the and of")

        assert result.candidates == ()
        assert result.is_degraded is False
        assert fallback.calls == []

    def test_ranks_are_carried_rather_than_recomputed(self) -> None:
        """§20.6 fuses on rank; recomputing from scores would lose ties."""
        service, _primary, _fallback = build(primary_results=3)

        result = service.search("revenue")

        assert [c.rank for c in result.candidates] == [1, 2, 3]


class TestDegradation:
    def test_an_unreachable_index_falls_back_and_says_so(self) -> None:
        service, _primary, fallback = build(
            primary_raises=VectorIndexUnavailableError("qdrant is down")
        )

        result = service.search("credit risk")

        assert result.retriever == Retriever.POSTGRES_FTS
        assert result.degraded == (DEGRADED_LEXICAL_FALLBACK,)
        assert len(fallback.calls) == 1

    def test_the_fallback_receives_the_same_query_and_limit(self) -> None:
        """A narrower fallback would silently change what the caller asked for."""
        service, _primary, fallback = build(
            primary_raises=VectorIndexUnavailableError("down")
        )

        service.search("deferred tax", limit=7)

        assert fallback.calls == [("deferred tax", 7)]

    def test_a_shape_error_is_not_survivable(self) -> None:
        """The index and the model have diverged; falling back would hide it."""
        service, _primary, fallback = build(
            primary_raises=VectorIndexShapeError("768 against 1024")
        )

        with pytest.raises(VectorIndexShapeError):
            service.search("credit risk")

        assert fallback.calls == []

    def test_an_unexpected_error_is_not_survivable(self) -> None:
        service, _primary, fallback = build(primary_raises=RuntimeError("bug"))

        with pytest.raises(RuntimeError):
            service.search("credit risk")

        assert fallback.calls == []


class TestArguments:
    @pytest.mark.parametrize("limit", [0, -1])
    def test_a_non_positive_limit_is_refused(self, limit: int) -> None:
        """Qdrant would reject it less clearly, and SQL would return nothing."""
        service, primary, _fallback = build()

        with pytest.raises(ValueError, match="limit must be positive"):
            service.search("revenue", limit=limit)

        assert primary.calls == []

    def test_filters_default_to_unrestricted(self) -> None:
        """Optional by design: a cross-corpus question wants no issuer filter."""
        service, _primary, _fallback = build()

        assert service.search("revenue").is_degraded is False
