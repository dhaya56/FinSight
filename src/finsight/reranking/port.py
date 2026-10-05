"""The reranking boundary: a protocol, its errors, and nothing else.

§23 leaves the reranker **unselected**: MiniLM is "the lightweight baseline" (§23.2),
BGE is "tested against the same candidate lists" (§23.3), and FlashRank is tested
"only if reranker latency prevents interactive use" (§23.4). §23.9 reserves the
choice. So the port exists for the same reason the embedding port does — the adapter
is expected to be replaced, and no module outside it imports a model runtime.

**§23.1 makes the reranker optional, and that is a requirement rather than a
convenience.** It "must improve ordering under the local latency and memory budget
and remain optional under degradation", and §23.8 says failure "returns fused order
plus a degradation flag". A caller must therefore be able to lose this stage and still
answer, which is why :class:`RerankUnavailableError` is separate from a programming
fault.

**Scores are not comparable to anything else and are not confidence.** A cross-encoder
emits a logit — measured on this model at -1.14 for a relevant passage and -11.45 for
an irrelevant one, both negative, neither bounded. They order candidates and nothing
more: §27 forbids presenting an evidence-support band as a correctness probability, and
a raw logit is further from one still.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "Passage",
    "RerankError",
    "RerankShapeError",
    "RerankUnavailableError",
    "Reranker",
    "Scored",
]


class RerankError(RuntimeError):
    """Base class for failures at the reranking boundary."""


class RerankUnavailableError(RerankError):
    """The reranker could not score, and the caller should proceed without it.

    Covers a model that will not load, a runtime that fails mid-batch, and a host
    that has run out of memory. All three are survivable in the one way §23.8
    specifies: return the fused order and flag it. None of them is a reason to fail a
    query, because fusion has already produced a usable ordering.
    """


class RerankShapeError(RerankError):
    """The reranker returned a different number of scores than passages given.

    Not survivable by degrading, because the pairing is positional: a caller that
    zipped mismatched lists would attach scores to the wrong chunks and reorder
    confidently in the wrong order. Louder than a silent misalignment.
    """


@dataclass(frozen=True, slots=True)
class Passage:
    """One candidate as the reranker sees it.

    ``text`` is the **deterministic** context plus the chunk body that §23.6 permits —
    "heading, period, basis, and table context without receiving untrusted generated
    summaries as fact". It is built by the same projection the embedding uses, so the
    two stages cannot disagree about what context a chunk has, and neither can invent
    any.
    """

    chunk_id: UUID
    text: str


@dataclass(frozen=True, slots=True)
class Scored:
    """One reranked candidate. ``rank`` is 1-based and dense."""

    chunk_id: UUID
    score: float
    rank: int


@runtime_checkable
class Reranker(Protocol):
    """Scores query and candidate together. Structural, so adapters do not subclass."""

    @property
    def model(self) -> str:
        """The model identifier, recorded on a QueryTrace (§20.13).

        Two orderings produced by different rerankers are not comparable, and a trace
        that did not say which produced it could not be reproduced.
        """
        ...

    def rerank(self, query: str, passages: Sequence[Passage]) -> tuple[Scored, ...]:
        """Score every passage against the query, best first.

        Returns one :class:`Scored` per passage, ranked. An empty input returns an
        empty result rather than raising — a query that retrieved nothing is a real
        outcome, and the reranker is not the stage that should object to it.

        Raises:
            RerankUnavailableError: scoring could not be performed. The caller keeps
                the fused order and flags the degradation (§23.8).
            RerankShapeError: the model returned the wrong number of scores.
        """
        ...
