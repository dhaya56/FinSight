"""Citation resolution: references become source text, and nothing is judged.

The distinctions that matter are between three failures a careless implementation would merge:
a reference naming no passage, a passage whose elements carry no text, and a claim with no
citations at all. The Gate acts differently on each, so resolution has to keep them apart.
"""

from uuid import UUID, uuid4

from finsight.generation.contract import parse_answer
from finsight.generation.evidence import assemble_evidence
from finsight.generation.resolution import (
    resolve_answer,
    source_element_ids,
)
from finsight.persistence.repositories.chunks import Citation
from finsight.retrieval.pipeline import RetrievedChunk

ELEMENT_A = UUID("aaaaaaaa-0000-4000-8000-000000000001")
ELEMENT_B = UUID("bbbbbbbb-0000-4000-8000-000000000002")

TEXT = {
    ELEMENT_A: "Revenue from operations was 48,206.00 crore.",
    ELEMENT_B: "The prior year figure was 43,891.00 crore.",
}


def child(
    *,
    rank: int = 1,
    citations: tuple[Citation, ...] = (),
    text: str = "a passage",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        rank=rank,
        text=text,
        heading_path=("3. Financial review",),
        page_numbers=(12,),
        evidence_type="narrative",
        issuer_name="Probe Limited",
        fiscal_period="FY2024-25",
        fused_score=0.03,
        contributions={"bm25": rank},
        rerank_score=-1.0,
        citations=citations,
    )


def evidence_of(*candidates: RetrievedChunk):
    return assemble_evidence(list(candidates), parents={}, budget_chars=50_000)


def answer_of(*claims: tuple[str, list[int]], answerable: bool = True):
    return parse_answer(
        {
            "answerable": answerable,
            "claims": [{"text": text, "citations": ids} for text, ids in claims],
        }
    )


class TestResolution:
    def test_a_reference_becomes_source_text(self) -> None:
        """The model wrote '1'; the text came from the store."""
        evidence = evidence_of(child(citations=(Citation(ELEMENT_A, "p. 12", 0),)))

        resolved = resolve_answer(
            answer_of(("Revenue was stated.", [1])), evidence, element_text=TEXT
        )

        citation = resolved.claims[0].citations[0]
        assert citation.text == TEXT[ELEMENT_A]
        assert citation.passage_id == 1
        assert citation.locator == "p. 12"
        assert citation.source_element_id == ELEMENT_A

    def test_several_elements_behind_one_passage_all_resolve(self) -> None:
        evidence = evidence_of(
            child(
                citations=(
                    Citation(ELEMENT_A, "p. 12", 0),
                    Citation(ELEMENT_B, "p. 13", 1),
                )
            )
        )

        resolved = resolve_answer(answer_of(("x.", [1])), evidence, element_text=TEXT)

        assert len(resolved.claims[0].citations) == 2

    def test_supported_text_joins_the_cited_spans(self) -> None:
        """What a numeral is checked against in the next commit."""
        evidence = evidence_of(
            child(
                citations=(
                    Citation(ELEMENT_A, "p. 12", 0),
                    Citation(ELEMENT_B, "p. 13", 1),
                )
            )
        )

        resolved = resolve_answer(answer_of(("x.", [1])), evidence, element_text=TEXT)

        assert "48,206.00" in resolved.claims[0].supported_text
        assert "43,891.00" in resolved.claims[0].supported_text

    def test_spans_are_joined_by_a_newline_not_a_space(self) -> None:
        """Otherwise two spans could form a number that appears in neither."""
        evidence = evidence_of(
            child(citations=(Citation(ELEMENT_A, "p. 1", 0), Citation(ELEMENT_B, "p. 2", 1)))
        )
        text = {ELEMENT_A: "1", ELEMENT_B: "2"}

        resolved = resolve_answer(answer_of(("x.", [1])), evidence, element_text=text)

        assert resolved.claims[0].supported_text == "1\n2"

    def test_the_answerable_flag_is_carried(self) -> None:
        resolved = resolve_answer(
            answer_of(answerable=False), evidence_of(child()), element_text=TEXT
        )

        assert resolved.answerable is False


class TestThreeDistinctFailures:
    """A careless implementation merges these. The Gate treats each differently."""

    def test_a_reference_naming_no_passage_is_unresolved(self) -> None:
        """§27.6: the model invented a citation."""
        evidence = evidence_of(child(citations=(Citation(ELEMENT_A, "p. 12", 0),)))

        resolved = resolve_answer(answer_of(("x.", [47])), evidence, element_text=TEXT)

        assert resolved.claims[0].unresolved_ids == (47,)
        assert resolved.claims[0].citations == ()
        assert resolved.unresolved_ids == (47,)

    def test_a_passage_whose_elements_carry_no_text_is_textless(self) -> None:
        """Reachable without anything being wrong: a page element has no text of its own."""
        evidence = evidence_of(child(citations=(Citation(ELEMENT_A, "p. 12", 0),)))

        resolved = resolve_answer(answer_of(("x.", [1])), evidence, element_text={})

        assert resolved.claims[0].textless_ids == (1,)
        assert resolved.claims[0].unresolved_ids == ()
        assert resolved.claims[0].has_support is False

    def test_a_claim_citing_nothing_is_unsupported(self) -> None:
        evidence = evidence_of(child(citations=(Citation(ELEMENT_A, "p. 12", 0),)))

        resolved = resolve_answer(answer_of(("x.", [])), evidence, element_text=TEXT)

        assert resolved.claims[0].has_support is False
        assert resolved.claims[0].unresolved_ids == ()
        assert resolved.claims[0].textless_ids == ()

    def test_unsupported_claims_are_collected(self) -> None:
        evidence = evidence_of(child(citations=(Citation(ELEMENT_A, "p. 12", 0),)))

        resolved = resolve_answer(
            answer_of(("good.", [1]), ("bad.", [99])), evidence, element_text=TEXT
        )

        assert len(resolved.unsupported_claims) == 1
        assert resolved.unsupported_claims[0].text == "bad."

    def test_a_mixed_claim_keeps_what_resolved(self) -> None:
        """One invented reference must not discard a valid one beside it."""
        evidence = evidence_of(child(citations=(Citation(ELEMENT_A, "p. 12", 0),)))

        resolved = resolve_answer(answer_of(("x.", [1, 47])), evidence, element_text=TEXT)

        assert len(resolved.claims[0].citations) == 1
        assert resolved.claims[0].unresolved_ids == (47,)
        assert resolved.claims[0].has_support is True


class TestDuplicates:
    def test_a_repeated_reference_resolves_once(self) -> None:
        """Resolving twice would make a numeral look better supported than it is."""
        evidence = evidence_of(child(citations=(Citation(ELEMENT_A, "p. 12", 0),)))

        resolved = resolve_answer(answer_of(("x.", [1, 1, 1])), evidence, element_text=TEXT)

        assert len(resolved.claims[0].citations) == 1

    def test_unresolved_ids_are_reported_once_across_the_answer(self) -> None:
        evidence = evidence_of(child(citations=(Citation(ELEMENT_A, "p. 12", 0),)))

        resolved = resolve_answer(
            answer_of(("a.", [47]), ("b.", [47])), evidence, element_text=TEXT
        )

        assert resolved.unresolved_ids == (47,)


class TestBatchedRead:
    def test_every_reachable_element_is_collected(self) -> None:
        """One query, not one per citation, on a path already dominated by model latency."""
        first = child(rank=1, citations=(Citation(ELEMENT_A, "p. 12", 0),))
        second = child(rank=2, citations=(Citation(ELEMENT_B, "p. 13", 0),))
        evidence = evidence_of(first, second)

        wanted = source_element_ids(answer_of(("x.", [1, 2])), evidence)

        assert set(wanted) == {ELEMENT_A, ELEMENT_B}

    def test_an_invented_reference_contributes_nothing_to_the_read(self) -> None:
        evidence = evidence_of(child(citations=(Citation(ELEMENT_A, "p. 12", 0),)))

        assert source_element_ids(answer_of(("x.", [47])), evidence) == ()

    def test_elements_are_collected_without_duplicates(self) -> None:
        shared = Citation(ELEMENT_A, "p. 12", 0)
        evidence = evidence_of(
            child(rank=1, citations=(shared,)), child(rank=2, citations=(shared,))
        )

        assert source_element_ids(answer_of(("x.", [1, 2])), evidence) == (ELEMENT_A,)

    def test_no_claims_needs_no_read(self) -> None:
        assert source_element_ids(answer_of(answerable=False), evidence_of(child())) == ()
