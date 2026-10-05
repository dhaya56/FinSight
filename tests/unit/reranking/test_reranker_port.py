"""The fake reranker's contract, which the real adapter must also satisfy.

The fake exists so CI can exercise ordering, batching and degradation without a model
cache. These tests pin the properties the pipeline relies on, so a real adapter that
broke one of them fails here rather than in a trace nobody reads.
"""

from uuid import uuid4

import pytest

from finsight.reranking.fake import MODEL, FakeReranker
from finsight.reranking.port import Passage, Reranker, RerankUnavailableError


def passages(count: int) -> list[Passage]:
    return [
        Passage(chunk_id=uuid4(), text=f"passage number {index} about credit risk")
        for index in range(count)
    ]


class TestContract:
    def test_the_fake_satisfies_the_port(self) -> None:
        assert isinstance(FakeReranker(), Reranker)

    def test_one_score_per_passage(self) -> None:
        """A missing score would silently drop a candidate from the result."""
        given = passages(7)

        scored = FakeReranker().rerank("credit risk", given)

        assert len(scored) == len(given)
        assert {item.chunk_id for item in scored} == {p.chunk_id for p in given}

    def test_ranks_are_dense_and_start_at_one(self) -> None:
        scored = FakeReranker().rerank("credit risk", passages(5))

        assert [item.rank for item in scored] == [1, 2, 3, 4, 5]

    def test_results_are_ordered_best_first(self) -> None:
        scored = FakeReranker().rerank("credit risk", passages(10))

        assert list(scored) == sorted(scored, key=lambda item: -item.score)

    def test_no_passages_yields_no_scores(self) -> None:
        """A query that retrieved nothing is a real outcome, not an error."""
        assert FakeReranker().rerank("credit risk", []) == ()

    def test_the_model_is_named(self) -> None:
        """§20.13: a trace that cannot say which reranker produced it is unusable."""
        assert FakeReranker().model == MODEL


class TestDeterminism:
    def test_the_same_pair_scores_identically(self) -> None:
        given = passages(5)
        reranker = FakeReranker()

        first = reranker.rerank("credit risk", given)
        second = reranker.rerank("credit risk", given)

        assert [item.chunk_id for item in first] == [
            item.chunk_id for item in second
        ]

    def test_a_different_query_can_reorder(self) -> None:
        """Otherwise the fake would be a pass-through and prove nothing."""
        given = passages(12)
        reranker = FakeReranker()

        one = [item.chunk_id for item in reranker.rerank("credit risk", given)]
        two = [item.chunk_id for item in reranker.rerank("revenue growth", given)]

        assert one != two

    def test_input_order_does_not_change_the_result(self) -> None:
        """Scoring must depend on the pair, never on position in the batch."""
        given = passages(8)
        reranker = FakeReranker()

        forward = reranker.rerank("credit risk", given)
        backward = reranker.rerank("credit risk", list(reversed(given)))

        assert [item.chunk_id for item in forward] == [
            item.chunk_id for item in backward
        ]


class TestFailure:
    def test_a_configured_failure_raises_unavailable(self) -> None:
        """Which is what the pipeline degrades on (§23.8)."""
        with pytest.raises(RerankUnavailableError):
            FakeReranker(fails=True).rerank("credit risk", passages(3))

    def test_calls_are_recorded_for_assertions(self) -> None:
        reranker = FakeReranker()

        reranker.rerank("credit risk", passages(4))

        assert reranker.calls == [("credit risk", 4)]
