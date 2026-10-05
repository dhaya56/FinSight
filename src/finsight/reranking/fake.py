"""A deterministic reranker, for tests and for CI where the model cache does not exist.

Mirrors ``embedding/fake.py`` and carries the same warning: **it has no semantics.**
The score is a hash of the query and the passage, so the same pair always scores the
same and different pairs almost never collide — enough to exercise ordering, batching,
pairing and degradation without a model.

That it is *not* semantic is the point. A fake that looked plausible would let a test
assert that reranking improves results, and §23 reserves that claim for a measured
comparison against a golden set that does not exist.
"""

import hashlib
from collections.abc import Sequence
from typing import Final

from finsight.reranking.port import Passage, RerankUnavailableError, Scored

__all__ = ["FakeReranker"]

MODEL: Final = "deterministic-fake-reranker"


class FakeReranker:
    """Hash-derived scores in [0, 1)."""

    def __init__(self, *, fails: bool = False) -> None:
        self._fails = fails
        self.calls: list[tuple[str, int]] = []

    @property
    def model(self) -> str:
        return MODEL

    def rerank(self, query: str, passages: Sequence[Passage]) -> tuple[Scored, ...]:
        self.calls.append((query, len(passages)))
        if self._fails:
            raise RerankUnavailableError("fake reranker configured to fail")
        if not passages:
            return ()

        scored = sorted(
            ((passage, self._score(query, passage.text)) for passage in passages),
            key=lambda pair: (-pair[1], str(pair[0].chunk_id)),
        )
        return tuple(
            Scored(chunk_id=passage.chunk_id, score=score, rank=rank)
            for rank, (passage, score) in enumerate(scored, start=1)
        )

    def _score(self, query: str, text: str) -> float:
        digest = hashlib.sha256(f"{query}\x00{text}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / float(1 << 64)
