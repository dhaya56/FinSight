"""RRF arithmetic, determinism, and the allocation floor.

Pure functions, so these are exact rather than approximate. The arithmetic is computed
independently in the test rather than compared against a recorded run, because a
recorded run would pin whatever the implementation does rather than what RRF is.
"""

import math
from uuid import UUID, uuid4

import pytest

from finsight.domain.representations.retrieval import EvidenceType
from finsight.retrieval.contracts import Candidate, Retriever
from finsight.retrieval.fusion import (
    FUSION_CONFIG_VERSION,
    Allocation,
    FusionConfig,
    allocate,
    reciprocal_rank_fusion,
)

NARRATIVE = EvidenceType.NARRATIVE.value
TABLE = EvidenceType.TABLE_DERIVED.value


def ranked(retriever: str, *chunk_ids: UUID, scores: tuple[float, ...] | None = None):
    return tuple(
        Candidate(
            chunk_id=chunk_id,
            score=scores[index] if scores else 1.0 / (index + 1),
            rank=index + 1,
            retriever=retriever,
        )
        for index, chunk_id in enumerate(chunk_ids)
    )


class TestRRFArithmetic:
    def test_the_score_is_the_sum_of_reciprocal_ranks(self) -> None:
        config = FusionConfig(k=60)
        shared, lexical_only, dense_only = uuid4(), uuid4(), uuid4()

        fused = reciprocal_rank_fusion(
            [
                ranked(Retriever.BM25, shared, lexical_only),
                ranked(Retriever.DENSE, dense_only, shared),
            ],
            config=config,
        )

        by_id = {candidate.chunk_id: candidate for candidate in fused}
        assert math.isclose(by_id[shared].score, 1 / 61 + 1 / 62)
        assert math.isclose(by_id[lexical_only].score, 1 / 62)
        assert math.isclose(by_id[dense_only].score, 1 / 61)

    def test_agreement_between_retrievers_outranks_a_single_first_place(self) -> None:
        """The property that makes fusion worth doing rather than concatenating."""
        agreed, lexical_first = uuid4(), uuid4()

        fused = reciprocal_rank_fusion(
            [
                ranked(Retriever.BM25, lexical_first, agreed),
                ranked(Retriever.DENSE, uuid4(), agreed),
            ]
        )

        assert fused[0].chunk_id == agreed

    def test_a_smaller_k_sharpens_the_top_of_each_list(self) -> None:
        first, second = uuid4(), uuid4()
        lists = [ranked(Retriever.BM25, first, second)]

        sharp = reciprocal_rank_fusion(lists, config=FusionConfig(k=1))
        flat = reciprocal_rank_fusion(lists, config=FusionConfig(k=1000))

        sharp_gap = sharp[0].score - sharp[1].score
        flat_gap = flat[0].score - flat[1].score
        assert sharp_gap > flat_gap

    def test_contributions_record_every_list_and_rank(self) -> None:
        """§20.13: a fused score alone cannot answer "why is this first"."""
        shared = uuid4()

        fused = reciprocal_rank_fusion(
            [
                ranked(Retriever.BM25, uuid4(), shared),
                ranked(Retriever.DENSE, shared),
            ]
        )

        contribution = next(c for c in fused if c.chunk_id == shared)
        assert contribution.contributions == {Retriever.BM25: 2, Retriever.DENSE: 1}
        assert contribution.retrievers == (Retriever.BM25, Retriever.DENSE)

    def test_ranks_are_dense_and_start_at_one(self) -> None:
        fused = reciprocal_rank_fusion(
            [ranked(Retriever.BM25, uuid4(), uuid4(), uuid4())]
        )

        assert [candidate.rank for candidate in fused] == [1, 2, 3]


class TestDeterminism:
    def test_tied_candidates_order_identically_across_runs(self) -> None:
        """A recorded QueryTrace must reproduce, so ties cannot order arbitrarily."""
        ids = [uuid4() for _ in range(6)]
        lists = [ranked(Retriever.BM25, *ids)]

        first = reciprocal_rank_fusion(lists)
        second = reciprocal_rank_fusion(lists)

        assert [c.chunk_id for c in first] == [c.chunk_id for c in second]

    def test_a_tie_is_broken_by_best_rank_then_identifier(self) -> None:
        low, high = sorted([uuid4(), uuid4()], key=str)
        # Both appear once at the same rank in different lists: equal scores.
        fused = reciprocal_rank_fusion(
            [ranked(Retriever.BM25, high), ranked(Retriever.DENSE, low)]
        )

        assert math.isclose(fused[0].score, fused[1].score)
        assert fused[0].chunk_id == low


class TestBoundaries:
    def test_no_lists_yields_nothing(self) -> None:
        assert reciprocal_rank_fusion([]) == ()

    def test_all_lists_empty_is_a_real_outcome(self) -> None:
        """A query whose terms appear nowhere matches nothing; not an error."""
        assert reciprocal_rank_fusion([(), ()]) == ()

    def test_one_empty_list_does_not_discard_the_other(self) -> None:
        """Dense being down must not take the lexical candidates with it."""
        fused = reciprocal_rank_fusion([ranked(Retriever.BM25, uuid4()), ()])

        assert len(fused) == 1

    def test_the_limit_truncates_after_fusion_not_before(self) -> None:
        ids = [uuid4() for _ in range(5)]

        fused = reciprocal_rank_fusion([ranked(Retriever.BM25, *ids)], limit=2)

        assert len(fused) == 2
        assert fused[0].rank == 1

    def test_a_repeated_chunk_from_one_retriever_keeps_its_best_rank(self) -> None:
        """Per-type allocation can run one retriever more than once per query."""
        chunk = uuid4()

        fused = reciprocal_rank_fusion(
            [
                ranked(Retriever.BM25, uuid4(), uuid4(), chunk),
                ranked(Retriever.BM25, chunk),
            ]
        )

        contribution = next(c for c in fused if c.chunk_id == chunk)
        assert contribution.contributions == {Retriever.BM25: 1}

    def test_consensus_outranks_exclusivity_at_the_default_k(self) -> None:
        """The property the default k implies, pinned so a change surfaces it.

        A chunk both retrievers put *last* in a depth-20 window outranks a chunk one
        retriever put first exclusively, because consensus wins whenever
        ``r < k + 2`` and k is 60. Usually what fusion is for; wrong for an
        exact-identifier query only the lexical side can answer. ENV-010 measures it
        on the corpus.
        """
        agreed, exclusive = uuid4(), uuid4()
        # ``agreed`` is last in *both* lists; ``exclusive`` is first in one and absent
        # from the other. Putting it in only one list would make it exclusive too,
        # which is the mistake this comment exists to stop being repeated.
        lexical = (exclusive, *[uuid4() for _ in range(18)], agreed)
        dense = (*[uuid4() for _ in range(19)], agreed)

        fused = reciprocal_rank_fusion(
            [
                ranked(Retriever.BM25, *lexical),
                ranked(Retriever.DENSE, *dense),
            ]
        )

        by_id = {candidate.chunk_id: candidate.rank for candidate in fused}
        assert by_id[agreed] < by_id[exclusive]

    def test_a_small_enough_k_lets_an_exclusive_first_place_win(self) -> None:
        """The crossover is real: ``k < depth - 2`` flips it.

        Pinned alongside the default so the trade-off is visible rather than needing
        to be re-derived. Not a recommendation — k stays unmeasured.
        """
        agreed, exclusive = uuid4(), uuid4()
        lexical = (exclusive, *[uuid4() for _ in range(18)], agreed)
        dense = (*[uuid4() for _ in range(19)], agreed)

        fused = reciprocal_rank_fusion(
            [
                ranked(Retriever.BM25, *lexical),
                ranked(Retriever.DENSE, *dense),
            ],
            config=FusionConfig(k=5),
        )

        by_id = {candidate.chunk_id: candidate.rank for candidate in fused}
        assert by_id[exclusive] < by_id[agreed]

    def test_k_must_be_at_least_one(self) -> None:
        """k=0 divides by the rank alone and k<0 can divide by zero."""
        with pytest.raises(ValueError, match="k must be at least 1"):
            FusionConfig(k=0)


class TestAllocationSplit:
    def test_the_default_split_favours_narrative(self) -> None:
        """ADR-003 admits no table detector, so table bounds are untrustworthy."""
        split = Allocation().split(20)

        assert split[NARRATIVE] == 12
        assert split[TABLE] == 8

    def test_both_types_always_receive_at_least_one(self) -> None:
        """A share rounding to zero would crowd out a type by arithmetic (§20.5)."""
        split = Allocation(narrative_share=0.95).split(4)

        assert split[NARRATIVE] >= 1
        assert split[TABLE] >= 1

    def test_the_floors_sum_to_the_budget(self) -> None:
        for limit in range(2, 40):
            split = Allocation().split(limit)
            assert split[NARRATIVE] + split[TABLE] == limit

    def test_a_budget_of_one_goes_to_narrative(self) -> None:
        split = Allocation().split(1)

        assert split[NARRATIVE] == 1
        assert split[TABLE] == 0

    def test_a_non_positive_budget_is_refused(self) -> None:
        with pytest.raises(ValueError, match="limit must be positive"):
            Allocation().split(0)

    @pytest.mark.parametrize("share", [0.0, 1.0, -0.1, 1.5])
    def test_a_share_outside_the_open_unit_interval_is_refused(
        self, share: float
    ) -> None:
        """0 or 1 would remove a type entirely, which is what §20.5 forbids."""
        with pytest.raises(ValueError, match="narrative_share"):
            Allocation(narrative_share=share)

    def test_the_version_is_pinned(self) -> None:
        assert Allocation().version == FUSION_CONFIG_VERSION
        assert FusionConfig().version == FUSION_CONFIG_VERSION


class TestAllocate:
    def test_each_type_reaches_its_floor(self) -> None:
        narrative = ranked(Retriever.BM25, *[uuid4() for _ in range(20)])
        table = ranked(Retriever.BM25, *[uuid4() for _ in range(20)])

        taken = allocate({NARRATIVE: narrative, TABLE: table}, limit=10)

        assert len(taken) == 10
        chosen = {candidate.chunk_id for candidate in taken}
        assert len(chosen & {c.chunk_id for c in narrative}) == 6
        assert len(chosen & {c.chunk_id for c in table}) == 4

    def test_an_unfilled_allocation_spills_to_the_other_type(self) -> None:
        """§20.5 is a floor, not a quota. A narrative-only answer still fills up."""
        narrative = ranked(Retriever.BM25, *[uuid4() for _ in range(20)])

        taken = allocate({NARRATIVE: narrative, TABLE: ()}, limit=10)

        assert len(taken) == 10

    def test_a_type_with_nothing_does_not_shrink_the_result(self) -> None:
        table = ranked(Retriever.BM25, *[uuid4() for _ in range(20)])

        taken = allocate({NARRATIVE: (), TABLE: table}, limit=10)

        assert len(taken) == 10

    def test_rank_order_within_a_type_is_preserved(self) -> None:
        """Allocation decides who competes, never who wins."""
        ids = [uuid4() for _ in range(6)]
        narrative = ranked(Retriever.BM25, *ids)

        taken = allocate({NARRATIVE: narrative, TABLE: ()}, limit=6)

        assert [candidate.chunk_id for candidate in taken] == ids

    def test_a_chunk_is_never_taken_twice(self) -> None:
        shared = uuid4()
        narrative = ranked(Retriever.BM25, shared, uuid4())
        table = ranked(Retriever.BM25, shared, uuid4())

        taken = allocate({NARRATIVE: narrative, TABLE: table}, limit=10)

        assert len({candidate.chunk_id for candidate in taken}) == len(taken)

    def test_fewer_candidates_than_the_budget_returns_what_exists(self) -> None:
        taken = allocate({NARRATIVE: ranked(Retriever.BM25, uuid4())}, limit=10)

        assert len(taken) == 1

    def test_nothing_available_yields_nothing(self) -> None:
        assert allocate({}, limit=10) == ()
