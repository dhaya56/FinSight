"""Assembling the evidence set: expansion, merging, budget, and edge ordering.

Pure functions over fakes, so these are exact. The properties worth most are the ones a
casual reading would assume rather than check: that merging two candidates into one parent
does not reorder the set, that identifiers stay dense after a passage is dropped, and that
the ordering puts rank 1 first and rank 2 *last* rather than second.
"""

from uuid import UUID, uuid4

import pytest

from finsight.generation.evidence import (
    EvidencePassage,
    assemble_evidence,
)
from finsight.persistence.repositories.chunks import Citation, PendingChunk
from finsight.retrieval.pipeline import RetrievedChunk


def child(
    text: str = "a child passage",
    *,
    rank: int = 1,
    chunk_id: UUID | None = None,
    citations: tuple[Citation, ...] = (),
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id or uuid4(),
        rank=rank,
        text=text,
        heading_path=("7. Risk factors",),
        page_numbers=(41,),
        evidence_type="narrative",
        issuer_name="Probe Limited",
        fiscal_period="FY2024-25",
        fused_score=0.03,
        contributions={"bm25": rank},
        rerank_score=-1.0 * rank,
        citations=citations,
    )


def parent(
    text: str = "a much longer parent passage", *, chunk_id: UUID | None = None
) -> PendingChunk:
    return PendingChunk(
        chunk_id=chunk_id or uuid4(),
        document_version_id=uuid4(),
        text=text,
        lexemes=None,
        heading_path=("7. Risk factors", "7.2 Credit risk"),
        page_numbers=(41, 42),
        evidence_type="narrative",
        issuer_name="Probe Limited",
        document_type="annual_report",
        fiscal_period="FY2024-25",
        reporting_basis="standalone",
    )


def ids(passages: tuple[EvidencePassage, ...]) -> list[int]:
    return [passage.id for passage in passages]


BUDGET = 10_000


class TestExpansion:
    def test_a_child_with_a_parent_is_replaced_by_the_parent(self) -> None:
        """The point of the whole module: a fragment becomes a readable passage."""
        one = child("fragment", rank=1)
        whole = parent("the full section, several sentences long")

        result = assemble_evidence(
            [one], parents={one.chunk_id: whole}, budget_chars=BUDGET
        )

        assert result.passages[0].text == "the full section, several sentences long"
        assert result.passages[0].expanded is True
        assert result.passages[0].chunk_id == whole.chunk_id
        assert result.expanded == 1

    def test_a_child_without_a_parent_keeps_its_own_text(self) -> None:
        """2,051 of the corpus's children are whole runs whose parent was dropped."""
        one = child("a complete run", rank=1)

        result = assemble_evidence([one], parents={}, budget_chars=BUDGET)

        assert result.passages[0].text == "a complete run"
        assert result.passages[0].expanded is False
        assert result.expanded == 0

    def test_expansion_can_be_switched_off(self) -> None:
        """So expanded and unexpanded can be compared on the same query."""
        one = child("fragment", rank=1)

        result = assemble_evidence(
            [one], parents={one.chunk_id: parent()}, budget_chars=BUDGET, expand_below_chars=0
        )

        assert result.passages[0].text == "fragment"
        assert result.expanded == 0


class TestExpansionPolicy:
    """Only fragments expand, which is what the measurement changed.

    Retrieved children averaged 1,574 characters over six real queries and 1 of 48 fell
    below the floor, so expanding everything discarded half the reranked set to the budget.
    These pin the policy that replaced it.
    """

    def test_a_substantial_child_is_not_expanded(self) -> None:
        """The measured common case: reranking already returned a readable passage."""
        substantial = child("x" * 1500, rank=1)
        whole = parent("y" * 6000)

        result = assemble_evidence(
            [substantial], parents={substantial.chunk_id: whole}, budget_chars=BUDGET
        )

        assert result.passages[0].expanded is False
        assert result.passages[0].text == "x" * 1500
        assert result.expanded == 0

    def test_a_fragment_is_expanded(self) -> None:
        """The rare case the policy exists for."""
        fragment = child("share", rank=1)
        whole = parent("the full paragraph the fragment came from")

        result = assemble_evidence(
            [fragment], parents={fragment.chunk_id: whole}, budget_chars=BUDGET
        )

        assert result.passages[0].expanded is True
        assert result.expanded == 1

    def test_a_mixed_set_expands_only_the_fragments(self) -> None:
        fragment, substantial = child("share", rank=1), child("x" * 1500, rank=2)
        one, two = parent("expanded one"), parent("expanded two")

        result = assemble_evidence(
            [fragment, substantial],
            parents={fragment.chunk_id: one, substantial.chunk_id: two},
            budget_chars=BUDGET,
        )

        assert result.expanded == 1
        assert {passage.expanded for passage in result.passages} == {True, False}

    def test_the_threshold_is_exactly_exclusive(self) -> None:
        """At the threshold is not below it, so the boundary cannot drift silently."""
        exact = child("x" * 100, rank=1)

        result = assemble_evidence(
            [exact],
            parents={exact.chunk_id: parent("expanded")},
            budget_chars=BUDGET,
            expand_below_chars=100,
        )

        assert result.passages[0].expanded is False

    def test_a_large_threshold_restores_expanding_everything(self) -> None:
        """Kept so the two policies can be compared rather than assumed."""
        substantial = child("x" * 1500, rank=1)

        result = assemble_evidence(
            [substantial],
            parents={substantial.chunk_id: parent("expanded")},
            budget_chars=BUDGET,
            expand_below_chars=100_000,
        )

        assert result.passages[0].expanded is True

    def test_a_negative_threshold_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            assemble_evidence(
                [child()], parents={}, budget_chars=BUDGET, expand_below_chars=-1
            )

    def test_the_passage_carries_the_parents_metadata(self) -> None:
        """A reader following the citation must see the span the text came from."""
        one = child(rank=1)
        whole = parent()

        result = assemble_evidence(
            [one], parents={one.chunk_id: whole}, budget_chars=BUDGET
        )

        passage = result.passages[0]
        assert passage.heading_path == ("7. Risk factors", "7.2 Credit risk")
        assert passage.page_numbers == (41, 42)
        assert passage.reporting_basis == "standalone"

    def test_the_parents_citations_are_used_when_supplied(self) -> None:
        """The shown text is the parent's, so its citations are the honest ones."""
        one = child(rank=1, citations=(Citation(uuid4(), "p. 41", 0),))
        whole = parent()
        parent_citation = Citation(uuid4(), "pp. 41-42", 0)

        result = assemble_evidence(
            [one],
            parents={one.chunk_id: whole},
            citations={whole.chunk_id: (parent_citation,)},
            budget_chars=BUDGET,
        )

        assert result.passages[0].citations == (parent_citation,)

    def test_a_child_citation_is_the_fallback(self) -> None:
        """Rather than returning a passage with no way to cite it at all."""
        own = Citation(uuid4(), "p. 41", 0)
        one = child(rank=1, citations=(own,))

        result = assemble_evidence(
            [one], parents={one.chunk_id: parent()}, budget_chars=BUDGET
        )

        assert result.passages[0].citations == (own,)


class TestMerging:
    def test_candidates_sharing_a_parent_become_one_passage(self) -> None:
        """Sending the same text twice spends budget to say nothing."""
        shared = parent("one section containing both hits")
        first, second = child("hit one", rank=1), child("hit two", rank=2)

        result = assemble_evidence(
            [first, second],
            parents={first.chunk_id: shared, second.chunk_id: shared},
            budget_chars=BUDGET,
        )

        assert len(result.passages) == 1
        assert result.merged == 1

    def test_a_merged_passage_records_every_candidate_it_covers(self) -> None:
        """A citation has to resolve back to what retrieval actually found."""
        shared = parent()
        first, second = child(rank=1), child(rank=2)

        result = assemble_evidence(
            [first, second],
            parents={first.chunk_id: shared, second.chunk_id: shared},
            budget_chars=BUDGET,
        )

        assert set(result.passages[0].stands_for) == {first.chunk_id, second.chunk_id}

    def test_a_merged_passage_keeps_the_best_rank(self) -> None:
        shared = parent()
        first, second = child(rank=2), child(rank=5)

        result = assemble_evidence(
            [first, second],
            parents={first.chunk_id: shared, second.chunk_id: shared},
            budget_chars=BUDGET,
        )

        assert result.passages[0].best_rank == 2

    def test_merging_does_not_reorder_the_set(self) -> None:
        """The property a careless merge breaks: order must follow the best rank."""
        shared = parent("shared")
        strong, weak, also_strong = (
            child("rank one", rank=1),
            child("rank two", rank=2),
            child("rank three", rank=3),
        )

        result = assemble_evidence(
            [strong, weak, also_strong],
            parents={strong.chunk_id: shared, also_strong.chunk_id: shared},
            budget_chars=BUDGET,
        )

        # Two passages: the shared parent (best rank 1) then the lone rank-2 child.
        assert len(result.passages) == 2
        assert result.ranked[0].best_rank == 1
        assert result.ranked[1].best_rank == 2

    def test_the_same_candidate_twice_is_absorbed_once(self) -> None:
        one = child(rank=1)

        result = assemble_evidence([one, one], parents={}, budget_chars=BUDGET)

        assert len(result.passages) == 1
        assert result.passages[0].stands_for == (one.chunk_id,)


class TestBudget:
    def test_passages_are_dropped_whole_when_the_budget_is_reached(self) -> None:
        """Truncating one would cut a sentence the model then cites (Â§14.9)."""
        candidates = [child("x" * 400, rank=rank) for rank in range(1, 6)]

        result = assemble_evidence(candidates, parents={}, budget_chars=1000)

        assert len(result.passages) == 2
        assert result.dropped_for_budget == 3
        assert result.used_chars == 800
        assert all(len(passage.text) == 400 for passage in result.passages)

    def test_the_first_passage_is_kept_even_if_it_exceeds_the_budget(self) -> None:
        """An empty evidence set cannot answer anything; one over-long passage can."""
        candidates = [child("x" * 5000, rank=1)]

        result = assemble_evidence(candidates, parents={}, budget_chars=100)

        assert len(result.passages) == 1
        assert result.used_chars == 5000

    def test_identifiers_stay_dense_after_a_drop(self) -> None:
        """A gap in the numbering would make the model cite an id that resolves to nothing."""
        candidates = [
            child("x" * 100, rank=1),
            child("y" * 5000, rank=2),
            child("z" * 100, rank=3),
        ]

        result = assemble_evidence(candidates, parents={}, budget_chars=300)

        assert sorted(ids(result.passages)) == [1, 2]
        assert result.by_id(1) is not None
        assert result.by_id(2) is not None
        assert result.by_id(3) is None

    def test_a_non_positive_budget_is_refused(self) -> None:
        with pytest.raises(ValueError, match="budget_chars must be positive"):
            assemble_evidence([child()], parents={}, budget_chars=0)

    def test_no_candidates_yields_an_empty_set(self) -> None:
        result = assemble_evidence([], parents={}, budget_chars=BUDGET)

        assert result.is_empty
        assert result.passages == ()
        assert result.used_chars == 0


class TestEdgeOrdering:
    def test_the_strongest_is_first_and_the_second_is_last(self) -> None:
        """Against lost-in-the-middle. Not second â€” last."""
        candidates = [child(f"rank {rank}", rank=rank) for rank in range(1, 6)]

        result = assemble_evidence(candidates, parents={}, budget_chars=BUDGET)

        assert ids(result.passages) == [1, 3, 5, 4, 2]

    def test_two_passages_put_one_first_and_two_last(self) -> None:
        candidates = [child(rank=1), child(rank=2)]

        result = assemble_evidence(candidates, parents={}, budget_chars=BUDGET)

        assert ids(result.passages) == [1, 2]

    def test_a_single_passage_is_unaffected(self) -> None:
        result = assemble_evidence([child(rank=1)], parents={}, budget_chars=BUDGET)

        assert ids(result.passages) == [1]

    def test_ranked_returns_reading_order_regardless(self) -> None:
        """A reader sees a numbered source list; only the model sees the edge ordering."""
        candidates = [child(f"rank {rank}", rank=rank) for rank in range(1, 6)]

        result = assemble_evidence(candidates, parents={}, budget_chars=BUDGET)

        assert ids(result.ranked) == [1, 2, 3, 4, 5]

    def test_every_passage_survives_the_reordering(self) -> None:
        """An ordering that lost a passage would be an invisible evidence loss."""
        candidates = [child(f"rank {rank}", rank=rank) for rank in range(1, 9)]

        result = assemble_evidence(candidates, parents={}, budget_chars=BUDGET)

        assert len(result.passages) == 8
        assert sorted(ids(result.passages)) == list(range(1, 9))


class TestAccounting:
    def test_the_set_reports_what_shaping_it_cost(self) -> None:
        shared = parent("shared parent")
        first, second, third = child(rank=1), child(rank=2), child("x" * 9000, rank=3)

        result = assemble_evidence(
            [first, second, third],
            parents={first.chunk_id: shared, second.chunk_id: shared},
            budget_chars=500,
        )

        assert result.considered == 3
        assert result.merged == 1
        assert result.expanded == 1
        assert result.dropped_for_budget == 1
        assert result.budget_chars == 500

