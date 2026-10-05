"""The real cross-encoder, where it exists.

**Skipped when the model cache is absent**, which is CI. The deterministic fake covers
orchestration everywhere; this covers the three things only the real model can show:
that it loads offline, that it orders a relevant passage above an irrelevant one, and
that our chunks fit its window.

Marked ``integration`` because it loads 22.7M parameters and takes seconds, not because
it needs a service.
"""

from collections.abc import Iterator

import pytest

from finsight.reranking.cross_encoder import MAX_TOKENS, CrossEncoderReranker
from finsight.reranking.port import Passage, Reranker, RerankUnavailableError

MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

RELEVANT = (
    "The Group's exposure to credit risk is influenced mainly by the individual "
    "characteristic of each customer and the concentration of risk from the top few "
    "customers."
)
IRRELEVANT = (
    "Our cricket sponsorship delivered strong brand recall and social engagement "
    "across the season."
)


def _staged() -> bool:
    try:
        from transformers import AutoTokenizer

        AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    except Exception:
        return False
    return True


pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not _staged(), reason="cross-encoder not in the local model cache"
)]


@pytest.fixture(scope="module")
def reranker() -> Iterator[CrossEncoderReranker]:
    yield CrossEncoderReranker(model=MODEL, batch_size=8)


class TestRealModel:
    def test_it_satisfies_the_port(self, reranker: CrossEncoderReranker) -> None:
        assert isinstance(reranker, Reranker)
        assert reranker.model == MODEL

    def test_a_relevant_passage_outranks_an_irrelevant_one(
        self, reranker: CrossEncoderReranker
    ) -> None:
        """The one quality claim this makes, on an unambiguous pair.

        Not evidence that reranking improves retrieval on real queries — §23.9's
        selection needs a golden set. Evidence that the model is wired up correctly:
        a reversed sign or a mis-paired batch would fail here.
        """
        from uuid import uuid4

        relevant, irrelevant = uuid4(), uuid4()

        scored = reranker.rerank(
            "What is the company's exposure to credit risk?",
            [
                Passage(chunk_id=irrelevant, text=IRRELEVANT),
                Passage(chunk_id=relevant, text=RELEVANT),
            ],
        )

        assert scored[0].chunk_id == relevant
        assert scored[0].score > scored[1].score

    def test_scores_are_stable_across_calls(
        self, reranker: CrossEncoderReranker
    ) -> None:
        """A QueryTrace that did not reproduce would be worth little."""
        from uuid import uuid4

        passages = [
            Passage(chunk_id=uuid4(), text=RELEVANT),
            Passage(chunk_id=uuid4(), text=IRRELEVANT),
        ]

        first = reranker.rerank("credit risk", passages)
        second = reranker.rerank("credit risk", passages)

        assert [item.chunk_id for item in first] == [item.chunk_id for item in second]
        assert [item.score for item in first] == [item.score for item in second]

    def test_one_score_per_passage_across_batches(
        self, reranker: CrossEncoderReranker
    ) -> None:
        """Batch size 8 with 20 passages exercises the batching loop."""
        from uuid import uuid4

        passages = [
            Passage(chunk_id=uuid4(), text=f"{RELEVANT} variant {index}")
            for index in range(20)
        ]

        scored = reranker.rerank("credit risk", passages)

        assert len(scored) == 20
        assert {item.chunk_id for item in scored} == {p.chunk_id for p in passages}
        assert [item.rank for item in scored] == list(range(1, 21))

    def test_no_passages_yields_nothing_without_loading(self) -> None:
        """An empty candidate set must not pay for 22.7M parameters."""
        assert CrossEncoderReranker(model="does-not-exist").rerank("x", []) == ()

    def test_a_passage_at_the_window_limit_is_scored_rather_than_refused(
        self, reranker: CrossEncoderReranker
    ) -> None:
        """Truncation is a safety net here, unlike the embedder which refuses.

        Losing a passage's tail costs ordering for one candidate in one query; losing
        an embedding's tail made text permanently unfindable.
        """
        from uuid import uuid4

        scored = reranker.rerank(
            "credit risk",
            [Passage(chunk_id=uuid4(), text="credit risk " * (MAX_TOKENS * 2))],
        )

        assert len(scored) == 1


class TestFailure:
    def test_a_missing_model_degrades_rather_than_crashing(self) -> None:
        """§23.1 makes this stage optional, so a host without the cache loses
        reranking rather than losing retrieval."""
        from uuid import uuid4

        broken = CrossEncoderReranker(model="finsight/no-such-reranker")

        with pytest.raises(RerankUnavailableError, match="could not load"):
            broken.rerank("x", [Passage(chunk_id=uuid4(), text="text")])
