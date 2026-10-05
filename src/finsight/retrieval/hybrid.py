"""The hybrid stage: allocate by evidence type, retrieve both ways, fuse (§20.4-20.6).

**Why each retriever runs once per evidence type.** §20.5's floor cannot be honoured
by filtering a single ranked list afterwards: if the top twenty are all
table-derived, narrative gets nothing, and discarding some of them to make room would
mean choosing which to drop with no basis for the choice. Oversampling one list and
allocating from it weakens the guarantee to best-effort, which is not what "cannot
crowd out" means. Asking each type separately is the only way a floor is a floor.

**The cost of that, measured rather than assumed.** A warm hybrid search over the
development corpus takes **337 ms** end to end; a cold one takes **6.2 s**, almost
entirely Ollama loading the model on first use. Within the warm figure, the query is
encoded **once per evidence type** — the same text embedded twice, at a measured
33 ms per call, so roughly 10% of the total is redundant.

That redundancy is left in place deliberately, not overlooked. Removing it means the
retriever protocol taking a *set* of filters rather than one, so an encoding can be
reused across them; that is the right shape and it is a refactor, and the standing
instruction on this project is to reach a working baseline before optimising. Recorded
here with the figure so the next person sees a decision rather than an accident.

**What degrades and what does not.** §20.12 requires explicit fallbacks and flags for
dense, lexical and reranking failures. Dense failure — Qdrant unreachable, or Ollama
unreachable — costs dense retrieval and sets a flag. Lexical failure falls through to
PostgreSQL full-text search and sets a different flag. Losing Qdrant sets *both*,
because BM25 lives there too; that combination is the realistic outage and is tested.

Losing PostgreSQL is not survivable and is not flagged: it holds the chunks, the
lexemes and the generation states, so there is nothing left to retrieve from or to
resolve a result against. §10.9 classifies it as essential for that reason.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from finsight.domain.representations.retrieval import EvidenceType
from finsight.retrieval.contracts import (
    DEGRADED_DENSE_UNAVAILABLE,
    Candidate,
    RetrievalFilters,
)
from finsight.retrieval.fusion import (
    Allocation,
    FusedCandidate,
    FusionConfig,
    allocate,
    reciprocal_rank_fusion,
)
from finsight.retrieval.service import LexicalRetrievalService, LexicalRetriever
from finsight.vector_index.port import VectorIndexUnavailableError

__all__ = ["HybridResult", "HybridRetrievalService"]


@dataclass(frozen=True, slots=True)
class HybridResult:
    """Fused candidates, and everything a QueryTrace needs about how (§20.13)."""

    candidates: tuple[FusedCandidate, ...]
    degraded: tuple[str, ...] = ()
    lexical_retriever: str = ""
    dense_used: bool = True
    fusion_version: str = ""
    per_retriever: dict[str, int] = field(default_factory=dict)
    """How many candidates each retriever contributed before fusion.

    Recorded because "the fusion returned nothing" and "one retriever returned
    nothing and the other was down" are different diagnoses with the same output.
    """

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded)


@dataclass(frozen=True, slots=True)
class HybridRetrievalService:
    """Retrieves lexically and densely under one filter set, then fuses."""

    lexical: LexicalRetrievalService
    dense: LexicalRetriever
    allocation: Allocation = field(default_factory=Allocation)
    fusion: FusionConfig = field(default_factory=FusionConfig)

    def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 20,
    ) -> HybridResult:
        """Fused candidates in rank order.

        ``limit`` bounds both the per-type retrieval and the fused output. Each type
        is asked for the full ``limit`` rather than only its floor, so the spill-over
        in :func:`allocate` has something to spill — asking for the floor would make
        an unfilled allocation unfillable.

        Raises:
            ValueError: ``limit`` is not positive.
            VectorIndexShapeError: the collection disagrees with the configuration.
                Not survivable on either path.
        """
        if limit < 1:
            raise ValueError(f"limit must be positive, got {limit}")
        resolved = filters or RetrievalFilters()

        lexical_by_type: dict[str, Sequence[Candidate]] = {}
        dense_by_type: dict[str, Sequence[Candidate]] = {}
        degraded: list[str] = []
        lexical_name = ""
        dense_used = True

        for evidence_type in (EvidenceType.NARRATIVE, EvidenceType.TABLE_DERIVED):
            scoped = replace(resolved, evidence_type=evidence_type.value)

            lexical = self.lexical.search(query, filters=scoped, limit=limit)
            lexical_by_type[evidence_type.value] = lexical.candidates
            lexical_name = lexical.retriever
            for flag in lexical.degraded:
                if flag not in degraded:
                    degraded.append(flag)

            if dense_used:
                try:
                    dense_by_type[evidence_type.value] = self.dense.search(
                        query, filters=scoped, limit=limit
                    )
                except VectorIndexUnavailableError:
                    # One flag for the whole query, not one per evidence type: the
                    # service is down, and it will not be up for the second type.
                    dense_used = False
                    dense_by_type = {}
                    if DEGRADED_DENSE_UNAVAILABLE not in degraded:
                        degraded.append(DEGRADED_DENSE_UNAVAILABLE)

        lexical_allocated = allocate(
            lexical_by_type, limit=limit, allocation=self.allocation
        )
        dense_allocated = (
            allocate(dense_by_type, limit=limit, allocation=self.allocation)
            if dense_used
            else ()
        )

        fused = reciprocal_rank_fusion(
            [lexical_allocated, dense_allocated],
            config=self.fusion,
            limit=limit,
        )
        return HybridResult(
            candidates=fused,
            degraded=tuple(degraded),
            lexical_retriever=lexical_name,
            dense_used=dense_used,
            fusion_version=self.fusion.version,
            per_retriever={
                lexical_name: len(lexical_allocated),
                **({"dense": len(dense_allocated)} if dense_used else {}),
            },
        )


def build_hybrid_retrieval_service() -> HybridRetrievalService:
    """Wire the hybrid stage from configuration."""
    from finsight.config.settings import get_settings
    from finsight.embedding.ollama_embedder import build_embedder
    from finsight.retrieval.dense import DenseRetriever
    from finsight.retrieval.lexical import build_retrievers
    from finsight.vector_index.qdrant_index import build_vector_index

    settings = get_settings()
    index = build_vector_index(settings)
    primary, fallback = build_retrievers(index=index)
    return HybridRetrievalService(
        lexical=LexicalRetrievalService(primary=primary, fallback=fallback),
        dense=DenseRetriever(embedder=build_embedder(settings), index=index),
    )
