"""The pipeline: depth separate from the final count, and §23.8's degradation.

The hybrid stage and the repository are faked, so these assert orchestration rather
than retrieval. Two properties carry the weight: that the reranker is given the *wide*
candidate set rather than the final one, and that losing it returns the fused order
with a flag instead of failing the query.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest

from finsight.persistence.repositories.chunks import Citation, PendingChunk
from finsight.reranking.fake import FakeReranker
from finsight.retrieval.contracts import (
    DEGRADED_DENSE_UNAVAILABLE,
    RetrievalFilters,
    Retriever,
)
from finsight.retrieval.fusion import FusedCandidate
from finsight.retrieval.hybrid import HybridResult
from finsight.retrieval.pipeline import (
    DEGRADED_RERANKER_UNAVAILABLE,
    RetrievalPipeline,
)


def chunk_of(chunk_id: UUID, index: int) -> PendingChunk:
    return PendingChunk(
        chunk_id=chunk_id,
        document_version_id=uuid4(),
        text=f"passage {index} discussing credit risk and exposure",
        lexemes="'credit':1 'risk':2",
        heading_path=("7. Risk factors",),
        page_numbers=(index + 1,),
        evidence_type="narrative",
        issuer_name="Probe Limited",
        document_type="annual_report",
        fiscal_period="FY2024-25",
        reporting_basis="both",
        currency="INR",
    )


class FakeHybrid:
    def __init__(self, *, available: int = 25, degraded: tuple[str, ...] = ()) -> None:
        self.ids = [uuid4() for _ in range(available)]
        self._degraded = degraded
        self.asked: list[int] = []

    def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 20,
    ) -> HybridResult:
        self.asked.append(limit)
        chosen = self.ids[:limit]
        return HybridResult(
            candidates=tuple(
                FusedCandidate(
                    chunk_id=chunk_id,
                    score=1.0 / (rank + 1),
                    rank=rank + 1,
                    contributions={Retriever.BM25: rank + 1},
                )
                for rank, chunk_id in enumerate(chosen)
            ),
            degraded=self._degraded,
            lexical_retriever=Retriever.BM25,
            fusion_version="1",
        )


_MISSING: set[UUID] = set()
"""Chunk ids the fake repository should fail to resolve, set per test.

Module state rather than a constructor argument because the repository is patched in
by an autouse fixture, which cannot see a per-test value any other way. Cleared on
both sides of every test so one case cannot leak into the next.
"""


_SHARED_ELEMENTS: dict[UUID, frozenset[UUID]] = {}
"""Source regions per chunk, for the deduplication stage. Empty means "distinct".

Empty by default so most tests see no collapsing, which keeps them about orchestration.
The deduplication rules themselves are covered in ``test_selection.py``.
"""


class _Repository:
    """Resolves every requested id to a chunk, except those in ``_MISSING``."""

    def __init__(self, _session: object) -> None:
        pass

    def load_context(self, *, chunk_ids: Sequence[UUID]) -> list[PendingChunk]:
        return [
            chunk_of(chunk_id, index)
            for index, chunk_id in enumerate(chunk_ids)
            if chunk_id not in _MISSING
        ]

    def citations_for(
        self, *, chunk_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[Citation, ...]]:
        return {
            chunk_id: (
                Citation(
                    source_element_id=uuid4(),
                    locator=f"p. {index + 1}",
                    position=0,
                ),
            )
            for index, chunk_id in enumerate(chunk_ids)
        }

    def source_elements_for(
        self, *, chunk_ids: Sequence[UUID]
    ) -> dict[UUID, frozenset[UUID]]:
        return {
            chunk_id: _SHARED_ELEMENTS[chunk_id]
            for chunk_id in chunk_ids
            if chunk_id in _SHARED_ELEMENTS
        }


@pytest.fixture(autouse=True)
def _patched_repository(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Patch the repository for the module, and undo it.

    ``monkeypatch`` rather than ``patch(...).start()``: an unstopped patcher survives
    the test that created it and reaches other modules, which is a defect that reports
    itself as an unrelated failure somewhere else in the suite.
    """
    _MISSING.clear()
    _SHARED_ELEMENTS.clear()
    monkeypatch.setattr("finsight.retrieval.pipeline.ChunkRepository", _Repository)
    yield
    _MISSING.clear()
    _SHARED_ELEMENTS.clear()


def build(
    *,
    hybrid: FakeHybrid | None = None,
    reranker: FakeReranker | None = None,
    depth: int = 25,
    missing: Sequence[UUID] = (),
) -> tuple[RetrievalPipeline, FakeHybrid]:
    resolved = hybrid or FakeHybrid()
    _MISSING.update(missing)

    @contextmanager
    def scope() -> Iterator[None]:
        yield None

    pipeline = RetrievalPipeline(
        hybrid=resolved,  # type: ignore[arg-type]
        reranker=reranker,
        depth=depth,
        session_scope_factory=scope,  # type: ignore[arg-type]
    )
    return pipeline, resolved


class TestDepth:
    def test_the_window_is_the_depth_not_the_limit(self) -> None:
        """A true answer fusion dropped to rank 20 must still reach the reranker."""
        pipeline, hybrid = build(depth=25)

        pipeline.search("credit risk", limit=5)

        assert hybrid.asked == [25]

    def test_the_reranker_sees_the_whole_window(self) -> None:
        reranker = FakeReranker()
        pipeline, _hybrid = build(depth=25, reranker=reranker)

        pipeline.search("credit risk", limit=5)

        assert reranker.calls == [("credit risk", 25)]

    def test_only_the_limit_is_returned(self) -> None:
        pipeline, _hybrid = build(depth=25, reranker=FakeReranker())

        result = pipeline.search("credit risk", limit=5)

        assert len(result.candidates) == 5
        assert [c.rank for c in result.candidates] == [1, 2, 3, 4, 5]

    def test_a_limit_wider_than_the_depth_widens_the_window(self) -> None:
        """Rather than silently returning fewer results than asked for."""
        pipeline, hybrid = build(depth=10)

        pipeline.search("credit risk", limit=30)

        assert hybrid.asked == [30]

    def test_the_depth_used_is_reported(self) -> None:
        pipeline, _hybrid = build(depth=25)

        assert pipeline.search("x", limit=5).depth == 25


class TestReranking:
    def test_reranking_changes_the_order(self) -> None:
        """Otherwise the stage is a no-op and nothing would notice."""
        hybrid = FakeHybrid(available=25)
        without, _ = build(hybrid=hybrid)
        fused_order = [c.chunk_id for c in without.search("credit risk", limit=10).candidates]

        hybrid_again = FakeHybrid(available=25)
        hybrid_again.ids = hybrid.ids
        with_rerank, _ = build(hybrid=hybrid_again, reranker=FakeReranker())
        reranked_order = [
            c.chunk_id for c in with_rerank.search("credit risk", limit=10).candidates
        ]

        assert fused_order != reranked_order

    def test_the_rerank_score_is_recorded_per_candidate(self) -> None:
        pipeline, _hybrid = build(reranker=FakeReranker())

        result = pipeline.search("credit risk", limit=5)

        assert all(c.rerank_score is not None for c in result.candidates)
        assert result.reranked is True
        assert result.reranker_model == FakeReranker().model

    def test_the_fused_score_and_contributions_survive_reranking(self) -> None:
        """§20.13 wants both: why it survived fusion and why it ended up here."""
        pipeline, _hybrid = build(reranker=FakeReranker())

        result = pipeline.search("credit risk", limit=5)

        assert all(c.fused_score > 0 for c in result.candidates)
        assert all(c.contributions for c in result.candidates)

    def test_without_a_reranker_the_fused_order_is_kept(self) -> None:
        pipeline, hybrid = build(reranker=None)

        result = pipeline.search("credit risk", limit=5)

        assert [c.chunk_id for c in result.candidates] == hybrid.ids[:5]
        assert result.reranked is False
        assert result.reranker_model is None
        assert result.is_degraded is False


class TestDegradation:
    def test_a_reranker_failure_returns_the_fused_order_and_flags_it(self) -> None:
        """§23.8, exactly."""
        pipeline, hybrid = build(reranker=FakeReranker(fails=True))

        result = pipeline.search("credit risk", limit=5)

        assert result.degraded == (DEGRADED_RERANKER_UNAVAILABLE,)
        assert result.reranked is False
        assert [c.chunk_id for c in result.candidates] == hybrid.ids[:5]
        assert all(c.rerank_score is None for c in result.candidates)

    def test_retrieval_flags_are_carried_through(self) -> None:
        """A reader told the index was unreachable needs that, not only the order."""
        pipeline, _hybrid = build(
            hybrid=FakeHybrid(degraded=(DEGRADED_DENSE_UNAVAILABLE,)),
            reranker=FakeReranker(),
        )

        result = pipeline.search("credit risk", limit=5)

        assert DEGRADED_DENSE_UNAVAILABLE in result.degraded

    def test_both_degradations_appear_together(self) -> None:
        pipeline, _hybrid = build(
            hybrid=FakeHybrid(degraded=(DEGRADED_DENSE_UNAVAILABLE,)),
            reranker=FakeReranker(fails=True),
        )

        result = pipeline.search("credit risk", limit=5)

        assert set(result.degraded) == {
            DEGRADED_DENSE_UNAVAILABLE,
            DEGRADED_RERANKER_UNAVAILABLE,
        }


class TestBoundaries:
    def test_nothing_retrieved_returns_nothing_and_no_reranker_call(self) -> None:
        reranker = FakeReranker()
        pipeline, _hybrid = build(
            hybrid=FakeHybrid(available=0), reranker=reranker
        )

        result = pipeline.search("photosynthesis", limit=5)

        assert result.candidates == ()
        assert reranker.calls == []
        assert result.is_degraded is False

    def test_a_chunk_that_no_longer_resolves_is_dropped(self) -> None:
        """§10.7: PostgreSQL is authoritative, so a chunk it lost is not evidence."""
        hybrid = FakeHybrid(available=25)
        pipeline, _hybrid = build(hybrid=hybrid, missing=hybrid.ids[:3])

        result = pipeline.search("credit risk", limit=25)

        assert len(result.candidates) == 22
        assert not ({c.chunk_id for c in result.candidates} & set(hybrid.ids[:3]))

    @pytest.mark.parametrize("limit", [0, -2])
    def test_a_non_positive_limit_is_refused(self, limit: int) -> None:
        pipeline, hybrid = build()

        with pytest.raises(ValueError, match="limit must be positive"):
            pipeline.search("x", limit=limit)

        assert hybrid.asked == []

    def test_the_fusion_version_is_carried(self) -> None:
        pipeline, _hybrid = build()

        assert pipeline.search("x", limit=5).fusion_version == "1"
