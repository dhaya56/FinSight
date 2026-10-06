"""Findings become one decision (§27.11), and removals strip claims rather than failing answers.

The property this suite exists to protect is the partial answer: three good cited claims beside
one bad one must release three, not zero. Getting that wrong is invisible in a demo and ruinous
in use, because the common case is a model that is mostly right.
"""

from uuid import uuid4

from finsight.generation.decision import (
    REASON_MODEL_REPORTED_UNANSWERABLE,
    REASON_NOTHING_SURVIVED,
    Decision,
    SupportBand,
    decide,
)
from finsight.generation.resolution import ResolvedAnswer, ResolvedCitation, ResolvedClaim
from finsight.generation.validation import (
    REASON_MIXED_PERIOD,
    REASON_UNSUPPORTED_NUMERAL,
    Finding,
    Severity,
)


def citation(passage_id: int = 1) -> ResolvedCitation:
    return ResolvedCitation(
        passage_id=passage_id,
        source_element_id=uuid4(),
        locator=f"p. {passage_id}",
        text="span text",
    )


def claim(text: str, *, cited: int = 1) -> ResolvedClaim:
    return ResolvedClaim(text=text, citations=(citation(cited),))


def answer(*claims: ResolvedClaim, answerable: bool = True) -> ResolvedAnswer:
    return ResolvedAnswer(answerable=answerable, claims=claims)


def removal(index: int, code: str = REASON_UNSUPPORTED_NUMERAL) -> Finding:
    return Finding(
        claim_index=index, code=code, severity=Severity.REMOVE, detail="removed"
    )


def disclosure(index: int, code: str = REASON_MIXED_PERIOD) -> Finding:
    return Finding(
        claim_index=index, code=code, severity=Severity.DISCLOSE, detail="disclosed"
    )


class TestDecisions:
    def test_everything_clean_is_answered(self) -> None:
        result = decide(answer(claim("one."), claim("two.")), ())

        assert result.decision is Decision.ANSWERED
        assert len(result.released) == 2
        assert result.withheld == ()

    def test_some_removed_is_partial(self) -> None:
        """The case that matters: good claims survive a bad neighbour."""
        result = decide(
            answer(claim("good one."), claim("bad."), claim("good two.")),
            (removal(1),),
        )

        assert result.decision is Decision.PARTIAL
        assert [item.text for item in result.released] == ["good one.", "good two."]
        assert [item.text for item in result.withheld] == ["bad."]

    def test_everything_removed_is_abstained(self) -> None:
        result = decide(answer(claim("bad.")), (removal(0),))

        assert result.decision is Decision.ABSTAINED
        assert result.released == ()
        assert REASON_NOTHING_SURVIVED in result.reason_codes

    def test_no_claims_at_all_is_abstained(self) -> None:
        result = decide(answer(answerable=False), ())

        assert result.decision is Decision.ABSTAINED
        assert REASON_MODEL_REPORTED_UNANSWERABLE in result.reason_codes

    def test_the_model_saying_yes_then_producing_nothing_abstains(self) -> None:
        """What reaches the reader decides, not what the model claimed about itself."""
        result = decide(answer(answerable=True), ())

        assert result.decision is Decision.ABSTAINED

    def test_a_disclosure_alone_still_answers(self) -> None:
        """§27.8 qualifies a claim; it does not withhold it."""
        result = decide(answer(claim("one.")), (disclosure(0),))

        assert result.decision is Decision.ANSWERED
        assert result.released[0].disclosures[0].code == REASON_MIXED_PERIOD


class TestReasonCodes:
    def test_removal_codes_are_reported(self) -> None:
        result = decide(answer(claim("bad.")), (removal(0),))

        assert REASON_UNSUPPORTED_NUMERAL in result.reason_codes

    def test_disclosure_codes_are_reported(self) -> None:
        result = decide(answer(claim("one.")), (disclosure(0),))

        assert REASON_MIXED_PERIOD in result.reason_codes

    def test_codes_are_not_repeated(self) -> None:
        result = decide(
            answer(claim("a."), claim("b.")), (removal(0), removal(1))
        )

        assert result.reason_codes.count(REASON_UNSUPPORTED_NUMERAL) == 1

    def test_structural_reasons_come_first(self) -> None:
        """A reader should see why there is no answer before what was wrong with the parts."""
        result = decide(answer(claim("bad.")), (removal(0),))

        assert result.reason_codes[0] == REASON_NOTHING_SURVIVED

    def test_an_unanswerable_flag_is_not_reported_when_content_survived(self) -> None:
        """The flag describes the model's own view; released content contradicts it."""
        result = decide(answer(claim("one."), answerable=False), ())

        assert REASON_MODEL_REPORTED_UNANSWERABLE not in result.reason_codes

    def test_a_clean_answer_has_no_reason_codes(self) -> None:
        assert decide(answer(claim("one.")), ()).reason_codes == ()


class TestSupportBand:
    """Rule-based and checkable. Never a probability (§27.10, §27.13)."""

    def test_clean_and_undegraded_is_strong(self) -> None:
        assert decide(answer(claim("one.")), ()).support_band is SupportBand.STRONG

    def test_a_disclosure_makes_it_moderate(self) -> None:
        result = decide(answer(claim("one.")), (disclosure(0),))

        assert result.support_band is SupportBand.MODERATE

    def test_degradation_alone_makes_it_moderate(self) -> None:
        result = decide(answer(claim("one.")), (), degraded=("dense_unavailable",))

        assert result.support_band is SupportBand.MODERATE

    def test_a_disclosure_and_degradation_together_make_it_weak(self) -> None:
        result = decide(
            answer(claim("one.")), (disclosure(0),), degraded=("dense_unavailable",)
        )

        assert result.support_band is SupportBand.WEAK

    def test_any_removal_makes_it_weak(self) -> None:
        """Heaviest signal: the model asserted what its own evidence did not support."""
        result = decide(answer(claim("good."), claim("bad.")), (removal(1),))

        assert result.support_band is SupportBand.WEAK

    def test_nothing_released_is_none(self) -> None:
        assert decide(answer(answerable=False), ()).support_band is SupportBand.NONE


class TestDegradationIsSeparate:
    """§27.9: "the index was down" and "the passages do not support this" are different."""

    def test_degradation_is_carried_apart_from_reason_codes(self) -> None:
        result = decide(answer(claim("one.")), (), degraded=("dense_unavailable",))

        assert result.degraded == ("dense_unavailable",)
        assert "dense_unavailable" not in result.reason_codes

    def test_degradation_does_not_change_the_decision(self) -> None:
        result = decide(answer(claim("one.")), (), degraded=("reranker_unavailable",))

        assert result.decision is Decision.ANSWERED


class TestWithheldIsVisible:
    def test_a_withheld_claim_keeps_its_text_and_reasons(self) -> None:
        """An invisible removal reads as a model that never spoke, which is flattering and wrong."""
        result = decide(answer(claim("bad with 99,999.")), (removal(0),))

        assert result.withheld[0].text == "bad with 99,999."
        assert result.withheld[0].findings[0].code == REASON_UNSUPPORTED_NUMERAL

    def test_a_claim_with_both_severities_is_withheld_with_both_recorded(self) -> None:
        """The removal decides; the disclosure is still worth showing in the explanation."""
        result = decide(answer(claim("bad.")), (removal(0), disclosure(0)))

        assert len(result.withheld) == 1
        assert len(result.withheld[0].findings) == 2

    def test_released_and_withheld_together_account_for_every_claim(self) -> None:
        result = decide(
            answer(claim("a."), claim("b."), claim("c.")), (removal(1),)
        )

        assert len(result.released) + len(result.withheld) == 3
