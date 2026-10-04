"""Counting tokens for the chunk budget.

**This is an approximation, and knowing whose is the point.** Chunks are embedded
by Nomic through Ollama, whose tokenizer is not available locally — Ollama exposes
embeddings, not vocabularies. So the budget is measured with a local WordPiece
tokenizer instead, the cross-encoder's, which is already cached for reranking and
costs no download.

The two disagree on *characters per token* — English financial prose measured at
roughly 4.4 here — but WordPiece and BPE vocabularies of similar size produce
similar token *counts*, which is what a budget needs. The margin is wide enough to
absorb the error: chunks are capped at 384 tokens against a model that truncates at
2,048, so a 5x counting error would still fit.

Recorded rather than hidden, because a future model with a very different
vocabulary — a character-level or multilingual one — would narrow that margin.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import Final

TokenCounter = Callable[[str], int]

TOKENIZER_MODEL: Final = "cross-encoder/ms-marco-MiniLM-L-6-v2"
"""The locally cached tokenizer the budget is measured with.

The reranker's, reused. A second tokenizer would be a second download and a second
vocabulary to keep in step for no additional accuracy.
"""

_FALLBACK_CHARS_PER_TOKEN: Final = 4.0
"""Used only when no tokenizer is available, which is a degraded mode.

Conservative on purpose: dividing by four over-counts dense numeric text, which
errs toward smaller chunks rather than toward the truncation wall.
"""


@lru_cache(maxsize=1)
def _tokenizer() -> object | None:
    """Load the tokenizer once, offline, or return None if it is not staged."""
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(TOKENIZER_MODEL, local_files_only=True)
    except Exception:
        return None


def build_token_counter() -> TokenCounter:
    """A deterministic token counter for the chunk budget.

    Falls back to a character estimate when the tokenizer is not staged, so
    chunking still runs on a machine without the model cache. The fallback is
    deliberately coarse and over-counting; it is not a silent substitution of
    equivalent behaviour.
    """
    tokenizer = _tokenizer()
    if tokenizer is None:
        return lambda text: int(len(text) / _FALLBACK_CHARS_PER_TOKEN) + 1

    def count(text: str) -> int:
        # ``verbose=False`` silences one warning and only one: "Token indices
        # sequence length is longer than the specified maximum sequence length for
        # this model (5396 > 512). Running this sequence through the model will
        # result in indexing errors." That warning is about running the *model*,
        # which this never does — it asks the tokenizer for a length and discards
        # the ids. Left on, it fires for every oversized block the chunker is about
        # to split, which is the path working correctly, and tells an operator that
        # chunking is producing indexing errors when it is not.
        #
        # What is *not* suppressed, because it is not requested: truncation. No
        # ``truncation`` argument is passed, so the tokenizer returns the full id
        # sequence and the count is exact. Passing ``truncation=True`` here would
        # cap every count at 512 and make a 5,396-token block look as though it fit
        # the 384-token budget — the chunker would then emit it whole and the
        # embedding model would silently drop seven eighths of it.
        ids = tokenizer.encode(  # type: ignore[attr-defined]
            text, add_special_tokens=False, verbose=False
        )
        return len(ids)

    return count
