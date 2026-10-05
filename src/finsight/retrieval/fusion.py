"""Combining ranked lists (§20.6) and allocating across evidence types (§20.5).

**Ranks, never scores, and this is measured rather than assumed.** On one real query
BM25 returned a top score of **12.02** and PostgreSQL full-text search **0.23** for
the same corpus and the same question; a dense cosine lands in [-1, 1]. Adding or
weighting those directly lets whichever scale happens to be largest decide the
ranking. §20.6 exists for that reason — "RRF combines ranked lists without assuming
comparable raw scores" — and the measurement is in ADR-005.

**Allocation is a floor, not a quota.** §20.5 requires explicit allocation "so one
type cannot crowd out the other", which is a guarantee that each type *reaches* the
candidate set — not a cap that leaves slots empty when a type has nothing to offer.
So an unfilled allocation spills to the other type: a question whose answer is
entirely narrative still gets a full candidate set.

**No absolute score floor is applied before fusion, and that is measured rather than
overlooked.** Production practice recommends dropping dense results below a fixed
cosine — "e.g. < 0.65" — to stop a weak rank 1 being promoted. Measured on this model
and corpus, a floor at 0.65 would discard **77% of dense results**, including every
result for `PAT` and `RoNW`: dense cosine against a *short* query sits at 0.45-0.54
while a natural-language question sits at 0.70-0.77, so an absolute floor encodes how
verbose the question was rather than how good the match is. A floor relative to each
query's own top score would be the defensible shape, and it is a threshold §4 reserves
for the developer. ENV-010 carries the distribution.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from finsight.domain.representations.retrieval import EvidenceType
from finsight.retrieval.contracts import Candidate

__all__ = [
    "FUSION_CONFIG_VERSION",
    "Allocation",
    "FusedCandidate",
    "FusionConfig",
    "allocate",
    "reciprocal_rank_fusion",
]

FUSION_CONFIG_VERSION: Final = "1"
"""Bump when any value in :class:`FusionConfig` or :class:`Allocation` changes.

Recorded on a QueryTrace (§20.13) so a recorded result can be reproduced. Unlike the
chunking and embedding versions, changing this costs nothing stored — fusion happens
per query — so a bump is cheap and should be taken rather than avoided.
"""


@dataclass(frozen=True, slots=True)
class FusionConfig:
    """Reciprocal rank fusion's one parameter."""

    k: int = 60
    """The rank-smoothing constant. **Unmeasured.**

    60 is the value from the original RRF paper and the de-facto default; §20.6 does
    not specify one. It controls how sharply rank 1 outweighs rank 10: a smaller k
    makes the top of each list dominate, a larger k flattens the lists toward equal
    contribution. Nothing here has been measured on this corpus, because §22.6's
    metrics need a golden question set that does not exist.

    Adopted as the standard default rather than chosen, and carried in versioned
    config so a later comparison can move it and say so.

    **The one consequence worth knowing before changing it.** A chunk found by both
    retrievers at rank *r* each scores ``2/(k+r)``; a chunk found by one retriever at
    rank 1 scores ``1/(k+1)``. Consensus wins whenever ``r < k + 2``. With ``k=60``
    and a retrieval depth of 20 that holds for *every* rank, so **a chunk both
    retrievers agree on anywhere in the top 20 outranks a chunk either ranked first
    exclusively** — measured on the corpus as dual-retriever candidates taking ranks
    1-9 and the best exclusive one landing at rank 10.

    That is usually the behaviour wanted from fusion, and it is the wrong behaviour
    for an exact-identifier query where only the lexical side can be right. Making an
    exclusive rank 1 competitive needs ``k < depth - 2``. ENV-010 has the measurement
    and the arithmetic; §22.6's golden set is what would settle the value.
    """

    version: str = FUSION_CONFIG_VERSION

    def __post_init__(self) -> None:
        if self.k < 1:
            raise ValueError(f"k must be at least 1, got {self.k}")


@dataclass(frozen=True, slots=True)
class Allocation:
    """How a candidate budget is divided across evidence types (§20.5)."""

    narrative_share: float = 0.6
    """Share of the budget guaranteed to narrative candidates. **Unmeasured.**

    One number rather than two absolute counts, so the caller's ``limit`` stays the
    contract and the split is a ratio of it.

    Tilted toward narrative, and the reason is measured rather than aesthetic:
    ADR-003 admits no table detector, so a ``table_derived`` chunk is "overlapped a
    region some detector proposed" rather than "is a table". The limitation register
    measures 19% of detected tables split across several chunks and the table-derived
    children as markedly shorter than narrative ones. Until a detector is admitted,
    weighting toward the representation whose bounds are trustworthy is the
    defensible default — not a claim that narrative answers better.
    """

    version: str = FUSION_CONFIG_VERSION

    def __post_init__(self) -> None:
        if not 0.0 < self.narrative_share < 1.0:
            raise ValueError(
                f"narrative_share must lie strictly inside (0, 1), got "
                f"{self.narrative_share}"
            )

    def split(self, limit: int) -> Mapping[str, int]:
        """Per-type floors for a budget, each at least one.

        At least one, because a share that rounded to zero would silently remove a
        type from the candidate set entirely — the crowding-out §20.5 forbids,
        arrived at by arithmetic instead of by competition. A ``limit`` of 1 cannot
        honour both and gives the slot to narrative.
        """
        if limit < 1:
            raise ValueError(f"limit must be positive, got {limit}")
        if limit == 1:
            return {EvidenceType.NARRATIVE.value: 1, EvidenceType.TABLE_DERIVED.value: 0}
        narrative = min(max(round(limit * self.narrative_share), 1), limit - 1)
        return {
            EvidenceType.NARRATIVE.value: narrative,
            EvidenceType.TABLE_DERIVED.value: limit - narrative,
        }


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """One chunk after fusion, with every list it appeared in and at what rank.

    ``contributions`` is kept rather than collapsed into the score because §20.13
    requires "candidate identifiers, ranks, fusion, reranker scores" in the
    QueryTrace. A fused score alone cannot answer "why is this first", and that is
    the question a trace exists for.
    """

    chunk_id: UUID
    score: float
    rank: int
    contributions: Mapping[str, int] = field(default_factory=dict)

    @property
    def retrievers(self) -> tuple[str, ...]:
        return tuple(sorted(self.contributions))


def reciprocal_rank_fusion(
    lists: Sequence[Sequence[Candidate]],
    *,
    config: FusionConfig | None = None,
    limit: int | None = None,
) -> tuple[FusedCandidate, ...]:
    """Fuse ranked lists by ``sum(1 / (k + rank))`` over the lists each chunk is in.

    Ties are broken by the chunk's best rank across the lists, then by identifier, so
    the output is **deterministic**. Without that, two runs on identical input could
    order tied candidates differently and a recorded QueryTrace would not reproduce.

    A chunk appearing in several lists is scored once per list, which is what makes
    agreement between retrievers count for something — the property that distinguishes
    fusion from concatenation.

    ``lists`` may contain empty lists, and all of them being empty is a real outcome
    rather than an error: a query whose terms appear nowhere in the active generations
    matches nothing.
    """
    settings = config or FusionConfig()
    scores: dict[UUID, float] = {}
    contributions: dict[UUID, dict[str, int]] = {}
    best_rank: dict[UUID, int] = {}

    for candidates in lists:
        for candidate in candidates:
            scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (
                settings.k + candidate.rank
            )
            per_retriever = contributions.setdefault(candidate.chunk_id, {})
            # Keep the best rank when one retriever contributes a chunk twice, which
            # happens with per-type allocation: the same chunk cannot appear under two
            # evidence types, but a retriever may be run more than once per query.
            previous = per_retriever.get(candidate.retriever)
            if previous is None or candidate.rank < previous:
                per_retriever[candidate.retriever] = candidate.rank
            best = best_rank.get(candidate.chunk_id)
            if best is None or candidate.rank < best:
                best_rank[candidate.chunk_id] = candidate.rank

    ordered = sorted(
        scores,
        key=lambda chunk_id: (-scores[chunk_id], best_rank[chunk_id], str(chunk_id)),
    )
    if limit is not None:
        ordered = ordered[:limit]

    return tuple(
        FusedCandidate(
            chunk_id=chunk_id,
            score=scores[chunk_id],
            rank=rank,
            contributions=dict(sorted(contributions[chunk_id].items())),
        )
        for rank, chunk_id in enumerate(ordered, start=1)
    )


def allocate(
    by_type: Mapping[str, Sequence[Candidate]],
    *,
    limit: int,
    allocation: Allocation | None = None,
) -> tuple[Candidate, ...]:
    """Take each type's floor, then fill the remainder from whatever is left.

    Two passes, and the second is the point. The first guarantees §20.5's floor so
    neither type is crowded out; the second spills unused allocation, so a query whose
    answer is entirely narrative still returns a full budget instead of silently
    returning 60% of one.

    Within each pass candidates keep their retriever's rank order. Re-ranking happens
    at fusion and at the cross-encoder, not here — this stage decides *which*
    candidates compete, never which wins.
    """
    settings = allocation or Allocation()
    floors = settings.split(limit)

    taken: list[Candidate] = []
    seen: set[UUID] = set()
    leftovers: list[Candidate] = []

    for evidence_type, floor in floors.items():
        available = list(by_type.get(evidence_type, ()))
        for candidate in available[:floor]:
            if candidate.chunk_id not in seen:
                seen.add(candidate.chunk_id)
                taken.append(candidate)
        leftovers.extend(available[floor:])

    for candidate in leftovers:
        if len(taken) >= limit:
            break
        if candidate.chunk_id not in seen:
            seen.add(candidate.chunk_id)
            taken.append(candidate)

    return tuple(taken[:limit])
