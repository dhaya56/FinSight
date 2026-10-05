"""The token counter must not truncate, and must not silently become a stub.

Both failures are quiet. A truncating counter caps every long block at the model's
512-token window, so a 5,000-token block reports as fitting a 384-token budget, is
emitted whole, and is then cut down by the embedding model with no record. A counter
that has silently fallen back to the character estimate produces chunks of a
different size from every other machine's, which looks like a data difference rather
than a configuration one.
"""

import pytest

from finsight.chunking import tokens
from finsight.chunking.tokens import build_token_counter

LONG = "Revenue from operations increased during the year under review. " * 200
"""Roughly 2,600 words — far past the 512-token window of the MiniLM tokenizer."""


def _staged() -> bool:
    return tokens._tokenizer() is not None


class TestTokenCounter:
    @pytest.mark.skipif(not _staged(), reason="tokenizer not in the local cache")
    def test_a_long_text_is_not_truncated_to_the_model_window(self) -> None:
        """The guard against passing ``truncation=True`` for a quieter warning.

        512 is the tokenizer's model_max_length. A count at or just under it for a
        text this long means truncation is on and every budget decision above it is
        meaningless.
        """
        count = build_token_counter()(LONG)

        assert count > 512
        assert count > 1000

    @pytest.mark.skipif(not _staged(), reason="tokenizer not in the local cache")
    def test_counting_is_deterministic(self) -> None:
        """Chunk boundaries are reproducible only if the count is."""
        counter = build_token_counter()

        assert counter(LONG) == counter(LONG)

    @pytest.mark.skipif(not _staged(), reason="tokenizer not in the local cache")
    def test_the_count_grows_with_the_text(self) -> None:
        counter = build_token_counter()

        assert counter("Revenue") < counter("Revenue from operations")

    def test_the_fallback_over_counts_rather_than_under_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A machine without the cache still chunks, and errs toward smaller chunks.

        Under-counting would push chunks past the budget and into the truncation
        this module exists to avoid, so the degraded mode is deliberately pessimistic
        rather than merely approximate.
        """
        monkeypatch.setattr(tokens, "_tokenizer", lambda: None)
        body = "Revenue from operations"

        count = build_token_counter()(body)

        assert count == len(body) // 4 + 1
        assert count > len(body.split())

    def test_the_fallback_never_reports_zero_for_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A zero would let an unbounded number of blocks into one chunk."""
        monkeypatch.setattr(tokens, "_tokenizer", lambda: None)

        assert build_token_counter()("a") > 0
