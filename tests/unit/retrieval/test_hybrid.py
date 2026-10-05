"""The hybrid stage: per-type retrieval, fusion, and §20.12's degradation flags.

The fakes record which filters they were asked for, because the property that matters
most here is invisible in the output: each retriever must be asked **once per evidence
type**. Filtering a single ranked list afterwards cannot honour §20.5's floor, and a
result set looks identical either way.
"""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from finsight.domain.representations.retrieval import EvidenceType
from finsight.embedding.port import EmbeddingUnavailableError
from finsight.retrieval.contracts import (
    DEGRADED_DENSE_UNAVAILABLE,
    DEGRADED_LEXICAL_FALLBACK,
    Candidate,
    LexicalResult,
    RetrievalFilters,
    Retriever,
)
from finsight.retrieval.fusion import Allocation, FusionConfig
from finsight.retrieval.hybrid import HybridRetrievalService
from finsight.vector_index.port import (
    VectorIndexShapeError,
    VectorIndexUnavailableError,
)

NARRATIVE = EvidenceType.NARRATIVE.value
TABLE = EvidenceType.TABLE_DERIVED.value


class FakeLexicalService:
    """Stands in for LexicalRetrievalService, which has its own fallback inside."""

    def __init__(
        self,
        *,
        per_type: int = 10,
        degraded: tuple[str, ...] = (),
        retriever: str = Retriever.BM25,
    ) -> None:
        self._per_type = per_type
        self._degraded = degraded
        self._retriever = retriever
        self.asked: list[str | None] = []

    def search(
        self, query: str, *, filters: RetrievalFilters, limit: int
    ) -> LexicalResult:
        self.asked.append(filters.evidence_type)
        return LexicalResult(
            candidates=tuple(
                Candidate(
                    chunk_id=uuid4(),
                    score=1.0 / rank,
                    rank=rank,
                    retriever=self._retriever,
                )
                for rank in range(1, self._per_type + 1)
            ),
            retriever=self._retriever,
            degraded=self._degraded,
        )


class FakeDense:
    name = Retriever.DENSE

    def __init__(
        self, *, per_type: int = 10, raises: Exception | None = None
    ) -> None:
        self._per_type = per_type
        self._raises = raises
        self.asked: list[str | None] = []

    def search(
        self, query: str, *, filters: RetrievalFilters, limit: int
    ) -> tuple[Candidate, ...]:
        self.asked.append(filters.evidence_type)
        if self._raises is not None:
            raise self._raises
        return tuple(
            Candidate(
                chunk_id=uuid4(),
                score=1.0 / rank,
                rank=rank,
                retriever=self.name,
            )
            for rank in range(1, self._per_type + 1)
        )


def build(
    *,
    lexical: FakeLexicalService | None = None,
    dense: FakeDense | None = None,
) -> tuple[HybridRetrievalService, FakeLexicalService, FakeDense]:
    resolved_lexical = lexical or FakeLexicalService()
    resolved_dense = dense or FakeDense()
    service = HybridRetrievalService(
        lexical=resolved_lexical,  # type: ignore[arg-type]
        dense=resolved_dense,  # type: ignore[arg-type]
    )
    return service, resolved_lexical, resolved_dense


class TestPerTypeRetrieval:
    def test_each_retriever_is_asked_once_per_evidence_type(self) -> None:
        """§20.5's floor cannot be honoured by filtering one ranked list."""
        service, lexical, dense = build()

        service.search("credit risk")

        assert lexical.asked == [NARRATIVE, TABLE]
        assert dense.asked == [NARRATIVE, TABLE]

    def test_the_caller_filters_are_preserved_alongside_the_type(self) -> None:
        """Scoping by type must not discard the issuer the caller asked for."""
        captured: list[RetrievalFilters] = []

        class Recorder(FakeLexicalService):
            def search(
                self, query: str, *, filters: RetrievalFilters, limit: int
            ) -> LexicalResult:
                captured.append(filters)
                return super().search(query, filters=filters, limit=limit)

        service, _lexical, _dense = build(lexical=Recorder())

        service.search("x", filters=RetrievalFilters(issuer_name="Infosys Limited"))

        assert all(f.issuer_name == "Infosys Limited" for f in captured)
        assert {f.evidence_type for f in captured} == {NARRATIVE, TABLE}

    def test_each_type_is_asked_for_the_full_limit(self) -> None:
        """Asking for only the floor would leave an unfilled allocation unfillable."""
        asked_limits: list[int] = []

        class Recorder(FakeLexicalService):
            def search(
                self, query: str, *, filters: RetrievalFilters, limit: int
            ) -> LexicalResult:
                asked_limits.append(limit)
                return super().search(query, filters=filters, limit=limit)

        service, _lexical, _dense = build(lexical=Recorder())

        service.search("x", limit=20)

        assert asked_limits == [20, 20]


class TestFusedOutput:
    def test_the_result_is_bounded_by_the_limit(self) -> None:
        service, _lexical, _dense = build()

        result = service.search("credit risk", limit=5)

        assert len(result.candidates) == 5
        assert [c.rank for c in result.candidates] == [1, 2, 3, 4, 5]

    def test_both_retrievers_contribute(self) -> None:
        service, _lexical, _dense = build()

        result = service.search("credit risk", limit=20)

        contributors = {
            retriever
            for candidate in result.candidates
            for retriever in candidate.retrievers
        }
        assert contributors == {Retriever.BM25, Retriever.DENSE}

    def test_per_retriever_counts_are_recorded(self) -> None:
        """"Fusion returned nothing" and "one side was down" look identical without."""
        service, _lexical, _dense = build()

        result = service.search("credit risk", limit=10)

        assert result.per_retriever[Retriever.BM25] == 10
        assert result.per_retriever["dense"] == 10

    def test_the_fusion_version_is_reported(self) -> None:
        """§20.13: a recorded trace has to say which configuration produced it."""
        service, _lexical, _dense = build()

        assert service.search("x").fusion_version == FusionConfig().version

    def test_an_empty_corpus_yields_nothing_and_no_flag(self) -> None:
        service, _lexical, _dense = build(
            lexical=FakeLexicalService(per_type=0), dense=FakeDense(per_type=0)
        )

        result = service.search("photosynthesis")

        assert result.candidates == ()
        assert result.is_degraded is False


class TestDegradation:
    def test_an_unreachable_index_drops_dense_and_flags_it(self) -> None:
        service, _lexical, _dense = build(
            dense=FakeDense(raises=VectorIndexUnavailableError("qdrant down"))
        )

        result = service.search("credit risk", limit=10)

        assert result.degraded == (DEGRADED_DENSE_UNAVAILABLE,)
        assert result.dense_used is False
        assert len(result.candidates) == 10

    def test_dense_is_not_retried_for_the_second_evidence_type(self) -> None:
        """It will not be up a millisecond later, and one flag describes the query."""
        service, _lexical, dense = build(
            dense=FakeDense(raises=VectorIndexUnavailableError("down"))
        )

        result = service.search("credit risk")

        assert dense.asked == [NARRATIVE]
        assert result.degraded.count(DEGRADED_DENSE_UNAVAILABLE) == 1

    def test_an_unreachable_embedding_model_degrades_the_same_way(self) -> None:
        """Dense retrieval needs both services; losing either costs only dense."""
        service, _lexical, _dense = build(
            dense=FakeDense(
                raises=VectorIndexUnavailableError(
                    str(EmbeddingUnavailableError("ollama down"))
                )
            )
        )

        result = service.search("credit risk")

        assert result.degraded == (DEGRADED_DENSE_UNAVAILABLE,)

    def test_losing_qdrant_flags_both_halves(self) -> None:
        """The realistic outage: BM25 lives in Qdrant too, so both degrade."""
        service, _lexical, _dense = build(
            lexical=FakeLexicalService(
                degraded=(DEGRADED_LEXICAL_FALLBACK,),
                retriever=Retriever.POSTGRES_FTS,
            ),
            dense=FakeDense(raises=VectorIndexUnavailableError("qdrant down")),
        )

        result = service.search("credit risk", limit=10)

        assert set(result.degraded) == {
            DEGRADED_LEXICAL_FALLBACK,
            DEGRADED_DENSE_UNAVAILABLE,
        }
        assert result.lexical_retriever == Retriever.POSTGRES_FTS
        assert len(result.candidates) == 10

    def test_a_lexical_flag_is_not_duplicated_across_evidence_types(self) -> None:
        service, _lexical, _dense = build(
            lexical=FakeLexicalService(degraded=(DEGRADED_LEXICAL_FALLBACK,))
        )

        result = service.search("credit risk")

        assert result.degraded.count(DEGRADED_LEXICAL_FALLBACK) == 1

    def test_a_shape_error_is_not_survivable(self) -> None:
        service, _lexical, _dense = build(
            dense=FakeDense(raises=VectorIndexShapeError("768 against 1024"))
        )

        with pytest.raises(VectorIndexShapeError):
            service.search("credit risk")


class TestArguments:
    @pytest.mark.parametrize("limit", [0, -3])
    def test_a_non_positive_limit_is_refused(self, limit: int) -> None:
        service, lexical, _dense = build()

        with pytest.raises(ValueError, match="limit must be positive"):
            service.search("x", limit=limit)

        assert lexical.asked == []

    def test_the_allocation_is_configurable(self) -> None:
        service, _lexical, _dense = build()
        tilted = replace(service, allocation=Allocation(narrative_share=0.9))

        assert tilted.allocation.narrative_share == 0.9

    def test_filters_are_optional(self) -> None:
        service, _lexical, _dense = build()

        assert service.search("x").is_degraded is False


def ids_of(candidates: tuple) -> set[UUID]:
    return {candidate.chunk_id for candidate in candidates}
