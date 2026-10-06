"""Validation findings: what must be removed, what must be disclosed, what must pass.

Three blocks, and the third matters as much as the first two. A Gate that rejects correct
answers fails differently from one that passes wrong ones, and the second failure is easier to
notice than the first — so the equivalent-spelling cases are tested as carefully as the
violations.
"""

from uuid import UUID, uuid4

from finsight.generation.contract import parse_answer
from finsight.generation.evidence import assemble_evidence
from finsight.generation.resolution import resolve_answer
from finsight.generation.validation import (
    REASON_CITATION_NOT_IN_EVIDENCE,
    REASON_CITED_SPAN_HAS_NO_TEXT,
    REASON_CURRENCY_NOT_IN_SPAN,
    REASON_MIXED_BASIS,
    REASON_MIXED_ISSUER,
    REASON_MIXED_PERIOD,
    REASON_NO_CITATION,
    REASON_SCALE_NOT_IN_SPAN,
    REASON_UNSUPPORTED_NUMERAL,
    Severity,
    validate_answer,
)
from finsight.persistence.repositories.chunks import Citation
from finsight.retrieval.pipeline import RetrievedChunk

ELEMENT_A = UUID("aaaaaaaa-0000-4000-8000-000000000001")
ELEMENT_B = UUID("bbbbbbbb-0000-4000-8000-000000000002")


def child(
    *,
    rank: int = 1,
    element: UUID = ELEMENT_A,
    issuer: str | None = "Probe Limited",
    period: str | None = "FY2024-25",
    basis: str | None = "standalone",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        rank=rank,
        text="passage text",
        heading_path=("3. Financial review",),
        page_numbers=(12,),
        evidence_type="narrative",
        issuer_name=issuer,
        fiscal_period=period,
        fused_score=0.03,
        contributions={"bm25": rank},
        rerank_score=-1.0,
        reporting_basis=basis,
        citations=(Citation(element, f"p. {12 + rank}", 0),),
    )


def check(
    claim_text: str,
    *,
    cites: list[int] | None = None,
    spans: dict[UUID, str] | None = None,
    passages: list[RetrievedChunk] | None = None,
):
    """Run the whole chain for one claim and return its findings."""
    candidates = passages or [child()]
    evidence = assemble_evidence(candidates, parents={}, budget_chars=50_000)
    answer = parse_answer(
        {
            "answerable": True,
            "claims": [{"text": claim_text, "citations": cites if cites is not None else [1]}],
        }
    )
    resolved = resolve_answer(
        answer, evidence, element_text=spans if spans is not None else {ELEMENT_A: "spantext"}
    )
    return validate_answer(resolved, evidence)


def codes(findings) -> list[str]:
    return [finding.code for finding in findings]


class TestMustBeRemoved:
    def test_a_numeral_in_no_cited_span(self) -> None:
        findings = check(
            "Revenue was 99,999.00 crore.",
            spans={ELEMENT_A: "Revenue was 48,206.00 crore."},
        )

        assert REASON_UNSUPPORTED_NUMERAL in codes(findings)
        assert findings[0].severity is Severity.REMOVE

    def test_an_invented_citation(self) -> None:
        findings = check("A claim.", cites=[47])

        assert REASON_CITATION_NOT_IN_EVIDENCE in codes(findings)
        assert all(finding.severity is Severity.REMOVE for finding in findings)

    def test_a_claim_citing_nothing(self) -> None:
        findings = check("A claim.", cites=[])

        assert codes(findings) == [REASON_NO_CITATION]

    def test_a_cited_span_with_no_text(self) -> None:
        findings = check("A claim.", spans={})

        assert REASON_CITED_SPAN_HAS_NO_TEXT in codes(findings)

    def test_a_scale_the_spans_do_not_use(self) -> None:
        """The gap the numeral check leaves: same number, wrong scale, wrong by 100x."""
        findings = check(
            "The figure was 412.00 lakh.",
            spans={ELEMENT_A: "The figure was 412.00 crore."},
        )

        assert REASON_SCALE_NOT_IN_SPAN in codes(findings)
        assert "different amount" in findings[0].detail

    def test_a_currency_the_spans_do_not_use(self) -> None:
        findings = check(
            "The figure was USD 412.00.",
            spans={ELEMENT_A: "The figure was INR 412.00."},
        )

        assert REASON_CURRENCY_NOT_IN_SPAN in codes(findings)

    def test_checks_stop_after_no_support(self) -> None:
        """Comparing a claim against spans there are none of produces noise, not findings."""
        findings = check("Revenue was 99,999.00 crore.", cites=[])

        assert codes(findings) == [REASON_NO_CITATION]


class TestMustBeDisclosed:
    """Not false, but misleading read without qualification (§27.8)."""

    def test_two_periods_behind_one_claim(self) -> None:
        findings = check(
            "A claim.",
            cites=[1, 2],
            spans={ELEMENT_A: "text a", ELEMENT_B: "text b"},
            passages=[
                child(rank=1, element=ELEMENT_A, period="FY2024-25"),
                child(rank=2, element=ELEMENT_B, period="FY2023-24"),
            ],
        )

        assert REASON_MIXED_PERIOD in codes(findings)
        matching = next(f for f in findings if f.code == REASON_MIXED_PERIOD)
        assert matching.severity is Severity.DISCLOSE

    def test_two_issuers_behind_one_claim(self) -> None:
        findings = check(
            "A claim.",
            cites=[1, 2],
            spans={ELEMENT_A: "text a", ELEMENT_B: "text b"},
            passages=[
                child(rank=1, element=ELEMENT_A, issuer="Probe Limited"),
                child(rank=2, element=ELEMENT_B, issuer="Other Limited"),
            ],
        )

        assert REASON_MIXED_ISSUER in codes(findings)

    def test_two_reporting_bases_behind_one_claim(self) -> None:
        """§25.8: bases are compared only when a comparison was asked for."""
        findings = check(
            "A claim.",
            cites=[1, 2],
            spans={ELEMENT_A: "text a", ELEMENT_B: "text b"},
            passages=[
                child(rank=1, element=ELEMENT_A, basis="standalone"),
                child(rank=2, element=ELEMENT_B, basis="consolidated"),
            ],
        )

        assert REASON_MIXED_BASIS in codes(findings)

    def test_one_passage_cannot_conflict_with_itself(self) -> None:
        assert check("A claim.") == ()

    def test_agreeing_passages_produce_no_finding(self) -> None:
        findings = check(
            "A claim.",
            cites=[1, 2],
            spans={ELEMENT_A: "text a", ELEMENT_B: "text b"},
            passages=[
                child(rank=1, element=ELEMENT_A),
                child(rank=2, element=ELEMENT_B),
            ],
        )

        assert findings == ()

    def test_an_absent_value_is_not_a_conflict(self) -> None:
        """NULL metadata means not extracted, not a second value."""
        findings = check(
            "A claim.",
            cites=[1, 2],
            spans={ELEMENT_A: "text a", ELEMENT_B: "text b"},
            passages=[
                child(rank=1, element=ELEMENT_A, period="FY2024-25"),
                child(rank=2, element=ELEMENT_B, period=None),
            ],
        )

        assert REASON_MIXED_PERIOD not in codes(findings)


class TestMustPass:
    """Equivalent spellings. Flagging these makes the Gate reject correct answers."""

    def test_a_currency_symbol_against_its_code(self) -> None:
        findings = check(
            "The figure was ₹412.00 crore.",
            spans={ELEMENT_A: "The figure was INR 412.00 crore."},
        )

        assert findings == ()

    def test_rupees_written_as_rs(self) -> None:
        findings = check(
            "The figure was Rs. 412.00 crore.",
            spans={ELEMENT_A: "The figure was ₹412.00 crore."},
        )

        assert findings == ()

    def test_a_plural_scale_word(self) -> None:
        findings = check(
            "The figure was 412.00 crore.",
            spans={ELEMENT_A: "Rs. in Crores: 412.00"},
        )

        assert findings == ()

    def test_omitting_a_scale_the_span_carries(self) -> None:
        """Imprecise rather than wrong; removing it would punish a correct summary."""
        findings = check(
            "The figure was 412.00.",
            spans={ELEMENT_A: "The figure was 412.00 crore."},
        )

        assert findings == ()

    def test_a_claim_with_no_figures_at_all(self) -> None:
        findings = check(
            "The Company monitors credit risk through counterparty limits.",
            spans={ELEMENT_A: "Credit risk is monitored through counterparty limits."},
        )

        assert findings == ()

    def test_hundred_in_prose_is_not_a_scale_conflict(self) -> None:
        """Why the scale list is bounded: an open list eventually hits ordinary prose."""
        findings = check(
            "Several hundred employees were trained.",
            spans={ELEMENT_A: "Several hundred employees were trained."},
        )

        assert findings == ()


class TestAcrossClaims:
    def test_findings_name_the_claim_they_belong_to(self) -> None:
        evidence = assemble_evidence([child()], parents={}, budget_chars=50_000)
        answer = parse_answer(
            {
                "answerable": True,
                "claims": [
                    {"text": "Fine claim.", "citations": [1]},
                    {"text": "Bad claim with 99,999.00.", "citations": [1]},
                ],
            }
        )
        resolved = resolve_answer(answer, evidence, element_text={ELEMENT_A: "nothing"})

        findings = validate_answer(resolved, evidence)

        assert all(finding.claim_index == 1 for finding in findings)

    def test_an_answer_with_no_claims_has_no_findings(self) -> None:
        evidence = assemble_evidence([child()], parents={}, budget_chars=50_000)
        resolved = resolve_answer(
            parse_answer({"answerable": False, "claims": []}),
            evidence,
            element_text={},
        )

        assert validate_answer(resolved, evidence) == ()
