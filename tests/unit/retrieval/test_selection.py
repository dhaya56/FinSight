"""Evidence deduplication (§20.9): what collapses, what survives, what is untouched."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from finsight.retrieval.selection import dedupe


@dataclass(frozen=True, slots=True)
class Candidate:
    chunk_id: UUID
    rank: int = 0


def ranked(count: int) -> list[Candidate]:
    return [Candidate(chunk_id=uuid4(), rank=index) for index in range(1, count + 1)]


class TestCollapsing:
    def test_candidates_sharing_a_source_element_collapse(self) -> None:
        """A block split across an oversized boundary yields siblings citing it."""
        first, second = ranked(2)
        element = uuid4()

        kept, collapsed = dedupe(
            [first, second],
            elements={
                first.chunk_id: frozenset({element}),
                second.chunk_id: frozenset({element}),
            },
        )

        assert [c.chunk_id for c in kept] == [first.chunk_id]
        assert len(collapsed) == 1
        assert collapsed[0].removed == second.chunk_id
        assert collapsed[0].absorbed_by == first.chunk_id
        assert collapsed[0].shared_elements == 1

    def test_partial_overlap_is_enough(self) -> None:
        """Overlap, not identity: one shared block is the same evidence twice."""
        first, second = ranked(2)
        shared = uuid4()

        kept, collapsed = dedupe(
            [first, second],
            elements={
                first.chunk_id: frozenset({shared, uuid4()}),
                second.chunk_id: frozenset({shared, uuid4(), uuid4()}),
            },
        )

        assert len(kept) == 1
        assert len(collapsed) == 1

    def test_disjoint_candidates_all_survive(self) -> None:
        candidates = ranked(4)
        elements = {c.chunk_id: frozenset({uuid4()}) for c in candidates}

        kept, collapsed = dedupe(candidates, elements=elements)

        assert len(kept) == 4
        assert collapsed == ()

    def test_the_earlier_candidate_wins(self) -> None:
        """The sequence arrives ranked, so first is the strongest provenance."""
        first, second, third = ranked(3)
        element = uuid4()
        elements = {
            first.chunk_id: frozenset({element}),
            second.chunk_id: frozenset({element}),
            third.chunk_id: frozenset({element}),
        }

        kept, collapsed = dedupe([first, second, third], elements=elements)

        assert [c.chunk_id for c in kept] == [first.chunk_id]
        assert {c.removed for c in collapsed} == {second.chunk_id, third.chunk_id}
        assert all(c.absorbed_by == first.chunk_id for c in collapsed)


class TestInvariants:
    def test_order_is_never_changed(self) -> None:
        """Deduplication removes; it must not promote. Fusion owns the order."""
        candidates = ranked(5)
        elements = {c.chunk_id: frozenset({uuid4()}) for c in candidates}

        kept, _collapsed = dedupe(candidates, elements=elements)

        assert [c.chunk_id for c in kept] == [c.chunk_id for c in candidates]

    def test_ranks_are_renumbered_without_gaps(self) -> None:
        """A caller should never see 1, 2, 4 and have to wonder about 3."""
        first, second, third = ranked(3)
        element = uuid4()

        kept, _collapsed = dedupe(
            [first, second, third],
            elements={
                first.chunk_id: frozenset({uuid4()}),
                second.chunk_id: frozenset({element}),
                third.chunk_id: frozenset({element}),
            },
        )

        assert [c.rank for c in kept] == [1, 2]

    def test_a_candidate_with_no_recorded_elements_is_kept(self) -> None:
        """It cannot be shown to overlap anything, and §14.7 makes that worth seeing."""
        first, second = ranked(2)

        kept, collapsed = dedupe(
            [first, second],
            elements={first.chunk_id: frozenset({uuid4()})},
        )

        assert len(kept) == 2
        assert collapsed == ()

    def test_several_sourceless_candidates_do_not_collapse_each_other(self) -> None:
        """Two empty sets intersect to nothing, which is not evidence of sameness."""
        candidates = ranked(3)

        kept, collapsed = dedupe(candidates, elements={})

        assert len(kept) == 3
        assert collapsed == ()


class TestBoundaries:
    def test_no_candidates_yields_nothing(self) -> None:
        assert dedupe([], elements={}) == ((), ())

    def test_a_candidate_without_a_chunk_id_is_refused(self) -> None:
        """Rather than silently treating every such candidate as distinct."""

        @dataclass(frozen=True)
        class Nameless:
            score: float

        with pytest.raises(TypeError, match="no chunk_id"):
            dedupe([Nameless(score=1.0)], elements={})

    def test_a_candidate_without_a_rank_is_left_alone(self) -> None:
        """Renumbering is for things that carry a rank; others pass through."""

        @dataclass(frozen=True)
        class Bare:
            chunk_id: UUID

        candidate = Bare(chunk_id=uuid4())

        kept, _collapsed = dedupe([candidate], elements={})

        assert kept == (candidate,)
