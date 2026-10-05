"""The MiniLM cross-encoder adapter: the only module that runs a reranking model.

§23.2 names MiniLM the lightweight baseline. ADR-006 adopts it provisionally on that
basis, as ADR-002 did for PyMuPDF and ADR-004 for Nomic, and §23.9 still reserves the
selection.

**Loaded offline, and lazily.** ``local_files_only`` for the reason ENV-007 records:
the host's network requires its own trust anchors and a model fetch mid-query is not an
acceptable failure mode for the restricted paths this project runs. Lazy because the
weights are 22.7M parameters and a CLI invocation that never reranks should not pay for
them.

**The latency is the thing to know about this stage.** Measured on this host's CPU with
real chunk text at realistic length, batch 8:

| Candidates | Median |
|---|---|
| 10 | 607 ms |
| 25 | 1,700 ms |
| 50 | 4,005 ms |
| 100 | 9,135 ms |

Roughly 87 ms per passage, scaling linearly, against 337 ms for the whole hybrid
retrieval it follows. Depth is therefore the dominant cost of a query and §23.4's
trigger — "FlashRank is tested only if reranker latency prevents interactive use" — is
measurably live. ENV-010 carries the figures.

Batch 8 rather than 32, also measured: a larger batch pads every sequence to the
longest in it, and the wasted compute outweighs the fewer forward passes.
"""

from collections.abc import Sequence
from typing import Any, Final

from finsight.reranking.port import (
    Passage,
    RerankShapeError,
    RerankUnavailableError,
    Scored,
)

__all__ = ["CrossEncoderReranker", "build_reranker"]

MAX_TOKENS: Final = 512
"""The model's window. Not a configuration value — it is the architecture's."""


class CrossEncoderReranker:
    """Scores (query, passage) pairs with a local cross-encoder."""

    def __init__(
        self,
        *,
        model: str,
        batch_size: int = 8,
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        self._model_name = model
        self._batch_size = max(batch_size, 1)
        self._max_tokens = max_tokens
        self._tokenizer: Any | None = None
        self._network: Any | None = None
        self._torch: Any | None = None

    @property
    def model(self) -> str:
        return self._model_name

    def rerank(self, query: str, passages: Sequence[Passage]) -> tuple[Scored, ...]:
        if not passages:
            return ()

        tokenizer, network, torch = self._loaded()
        scores: list[float] = []
        try:
            for start in range(0, len(passages), self._batch_size):
                window = passages[start : start + self._batch_size]
                encoded = tokenizer(
                    [query] * len(window),
                    [passage.text for passage in window],
                    padding=True,
                    # Truncation is a safety net, not a routine path. Measured over
                    # the 200 largest chunks in the corpus, query + context + text
                    # peaks at 461 tokens against this window, so nothing is cut
                    # today. If a longer chunk ever arrives, losing its tail costs
                    # *ordering* for one candidate in one query — unlike the
                    # embedder, where truncation made text permanently unfindable
                    # and is therefore refused instead.
                    truncation=True,
                    max_length=self._max_tokens,
                    return_tensors="pt",
                )
                with torch.no_grad():
                    logits = network(**encoded).logits
                scores.extend(float(value) for value in logits.reshape(-1))
        except Exception as error:
            raise RerankUnavailableError(
                f"{self._model_name} failed while scoring "
                f"{len(passages)} passage(s): {type(error).__name__}: {error}"
            ) from error

        if len(scores) != len(passages):
            raise RerankShapeError(
                f"{self._model_name} returned {len(scores)} score(s) for "
                f"{len(passages)} passage(s); pairing them by position would "
                "attach scores to the wrong chunks"
            )

        ordered = sorted(
            zip(passages, scores, strict=True),
            # Descending score, then chunk id, so equal scores order the same way on
            # every run. A QueryTrace that did not reproduce would be worth little.
            key=lambda pair: (-pair[1], str(pair[0].chunk_id)),
        )
        return tuple(
            Scored(chunk_id=passage.chunk_id, score=score, rank=rank)
            for rank, (passage, score) in enumerate(ordered, start=1)
        )

    def _loaded(self) -> tuple[Any, Any, Any]:
        """Load the tokenizer, the weights and torch once, offline.

        A load failure is reported as unavailability rather than propagating an import
        error, because §23.1 makes this stage optional: a host without the model cache
        should lose reranking, not lose retrieval.
        """
        if self._tokenizer is not None and self._network is not None:
            return self._tokenizer, self._network, self._torch

        try:
            import torch
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

            tokenizer = AutoTokenizer.from_pretrained(
                self._model_name, local_files_only=True
            )
            network = AutoModelForSequenceClassification.from_pretrained(
                self._model_name, local_files_only=True
            )
            network.eval()
        except Exception as error:
            raise RerankUnavailableError(
                f"could not load {self._model_name} offline: "
                f"{type(error).__name__}: {error}"
            ) from error

        self._tokenizer, self._network, self._torch = tokenizer, network, torch
        return tokenizer, network, torch


def build_reranker() -> CrossEncoderReranker:
    """Wire the reranker from configuration."""
    from finsight.config.settings import get_settings

    settings = get_settings()
    return CrossEncoderReranker(
        model=settings.rerank_model,
        batch_size=settings.rerank_batch_size,
    )
