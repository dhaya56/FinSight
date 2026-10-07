"""The answer path, orchestrated: every stage reached, and every failure degraded.

Retrieval, the repositories and the model are faked, so these assert *ordering and outcome*
rather than retrieval quality or grounding — those are covered where each stage lives. Four
properties carry the weight here:

* the chain runs in the right order and the model sees the evidence it will be checked against;
* a question with no evidence never reaches the model, because a five-second refusal we can
  derive for nothing is pure cost;
* each of the three generation failures abstains with its own flag and **keeps the evidence**,
  which is §26.10's deterministic fallback;
* the audit record is written on every path, including the refusals.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest

from finsight.generation.decision import (
    REASON_NO_EVIDENCE,
    AnswerDecision,
    Decision,
    SupportBand,
)
from finsight.generation.fake import MODEL, FakeGenerator
from finsight.generation.service import (
    DEGRADED_GENERATION_CONTRACT,
    DEGRADED_GENERATION_TRUNCATED,
    DEGRADED_GENERATION_UNAVAILABLE,
    AskService,
)
from finsight.generation.validation import (
    REASON_CITATION_NOT_IN_EVIDENCE,
    REASON_UNSUPPORTED_NUMERAL,
)
from finsight.persistence.repositories.chunks import Citation, PendingChunk
from finsight.retrieval.contracts import RetrievalFilters, Retriever
from finsight.retrieval.pipeline import Result, RetrievedChunk

ELEMENT = UUID("0199a1f0-0000-7000-8000-00000000aaaa")
SPAN = (
    "Revenue from operations for the year was 1,62,990 crore, an amount the company "
    "describes as reflecting broad-based demand."
)


def candidate(
    *,
    rank: int = 1,
    text: str = "Revenue from operations for the year was 1,62,990 crore.",
    element: UUID = ELEMENT,
    issuer: str = "Probe Limited",
    period: str = "FY2024-25",
    basis: str = "consolidated",
) -> RetrievedChunk:
    """One reranked candidate carrying the source region behind it."""
    return RetrievedChunk(
        chunk_id=uuid4(),
        rank=rank,
        text=text,
        heading_path=("Management discussion",),
        page_numbers=(rank + 10,),
        evidence_type="narrative",
        issuer_name=issuer,
        fiscal_period=period,
        reporting_basis=basis,
        fused_score=1.0 / rank,
        contributions={Retriever.BM25: rank},
        rerank_score=5.0 - rank,
        citations=(
            Citation(source_element_id=element, locator=f"p. {rank + 10}", position=0),
        ),
    )


class FakePipeline:
    """Returns prepared candidates and records what it was asked for."""

    def __init__(
        self,
        candidates: Sequence[RetrievedChunk] = (),
        *,
        degraded: tuple[str, ...] = (),
    ) -> None:
        self._candidates = tuple(candidates)
        self._degraded = degraded
        self.asked: list[tuple[str, int, RetrievalFilters | None]] = []

    def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 5,
    ) -> Result:
        self.asked.append((query, limit, filters))
        return Result(
            candidates=self._candidates,
            degraded=self._degraded,
            depth=25,
            reranked=True,
            lexical_retriever=Retriever.BM25,
        )


_ELEMENT_TEXT: dict[UUID, str] = {}
"""Source element text the fake repository serves, set per test.

Module state because the repositories are patched in as classes, which cannot see a per-test
value any other way. Reset on both sides of every test so one case cannot leak into the next.
"""

_PARENTS: dict[UUID, PendingChunk] = {}
"""Parents the fake repository returns. Empty means no candidate expands, which is the
measured common case on the real corpus."""

_RECORDED: list[dict[str, object]] = []
"""Every call to the answer repository, so "was the refusal recorded" is checkable."""


class _Chunks:
    def __init__(self, _session: object) -> None:
        pass

    def parents_of(self, *, chunk_ids: Sequence[UUID]) -> dict[UUID, PendingChunk]:
        return {
            chunk_id: _PARENTS[chunk_id]
            for chunk_id in chunk_ids
            if chunk_id in _PARENTS
        }

    def citations_for(
        self, *, chunk_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[Citation, ...]]:
        return {}


class _Source:
    def __init__(self, _session: object) -> None:
        pass

    def text_for(self, *, element_ids: Sequence[UUID]) -> dict[UUID, str]:
        return {
            element_id: _ELEMENT_TEXT[element_id]
            for element_id in element_ids
            if element_id in _ELEMENT_TEXT
        }


class _Answers:
    def __init__(self, _session: object) -> None:
        pass

    def record(
        self,
        *,
        question: str,
        decision: AnswerDecision,
        model: str,
        evidence_passages: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        elapsed_ms: int = 0,
    ) -> UUID:
        _RECORDED.append(
            {
                "question": question,
                "decision": decision,
                "model": model,
                "evidence_passages": evidence_passages,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "elapsed_ms": elapsed_ms,
            }
        )
        return uuid4()


@pytest.fixture(autouse=True)
def _patched(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Patch the three repositories for this module, and undo it.

    ``monkeypatch`` rather than a started patcher: one left running outlives its test and
    reports itself as an unrelated failure elsewhere in the suite.
    """
    _ELEMENT_TEXT.clear()
    _PARENTS.clear()
    _RECORDED.clear()
    _ELEMENT_TEXT[ELEMENT] = SPAN
    monkeypatch.setattr("finsight.generation.service.ChunkRepository", _Chunks)
    monkeypatch.setattr("finsight.generation.service.SourceRepository", _Source)
    monkeypatch.setattr("finsight.generation.service.AnswerRepository", _Answers)
    yield
    _ELEMENT_TEXT.clear()
    _PARENTS.clear()
    _RECORDED.clear()


def build(
    *,
    candidates: Sequence[RetrievedChunk] | None = None,
    degraded: tuple[str, ...] = (),
    generator: FakeGenerator | None = None,
    budget_chars: int = 20_000,
) -> tuple[AskService, FakePipeline, FakeGenerator]:
    pipeline = FakePipeline(
        candidates if candidates is not None else [candidate()], degraded=degraded
    )
    model = generator or FakeGenerator()

    @contextmanager
    def scope() -> Iterator[None]:
        yield None

    service = AskService(
        pipeline=pipeline,  # type: ignore[arg-type]
        generator=model,
        evidence_budget_chars=budget_chars,
        expand_below_chars=170,
        session_scope_factory=scope,  # type: ignore[arg-type]
    )
    return service, pipeline, model


class TestTheChain:
    def test_a_supported_claim_is_answered_and_recorded(self) -> None:
        service, _, _ = build()

        answer = service.ask("what was revenue?")

        assert answer.decision.decision is Decision.ANSWERED
        assert answer.decision.support_band is SupportBand.STRONG
        assert len(answer.decision.released) == 1
        assert answer.answer_id is not None
        assert len(_RECORDED) == 1

    def test_the_released_claim_carries_the_resolved_span(self) -> None:
        """The reader's citation has to reach stored text, not a passage number."""
        service, _, _ = build()

        citations = service.ask("what was revenue?").decision.released[0].citations

        assert [citation.source_element_id for citation in citations] == [ELEMENT]
        assert citations[0].text == SPAN
        assert citations[0].locator == "p. 11"

    def test_the_model_is_shown_the_evidence_it_will_be_checked_against(self) -> None:
        service, _, model = build()

        service.ask("what was revenue?")

        prompt = model.calls[0].prompt
        assert "1,62,990 crore" in prompt
        assert '<passage id="1"' in prompt
        assert "<question>\nwhat was revenue?\n</question>" in prompt

    def test_the_filters_and_limit_reach_retrieval_unchanged(self) -> None:
        """§20.2's filters are hard, so the answer path must not widen them."""
        service, pipeline, _ = build()
        filters = RetrievalFilters(issuer_name="Probe Limited")

        service.ask("what was revenue?", filters=filters, limit=3)

        assert pipeline.asked == [("what was revenue?", 3, filters)]

    def test_the_schema_is_sent_with_every_request(self) -> None:
        """Constrained decoding is the contract; an unconstrained call is a different one."""
        service, _, model = build()

        service.ask("what was revenue?")

        assert "answerable" in str(model.calls[0].schema)

    def test_stage_timings_are_reported_separately(self) -> None:
        service, _, _ = build()

        timings = service.ask("what was revenue?").timings_ms

        assert set(timings) == {
            "retrieval_ms",
            "generation_ms",
            "verification_ms",
            "total_ms",
        }

    def test_record_can_be_declined_for_a_probe(self) -> None:
        service, _, _ = build()

        answer = service.ask("what was revenue?", record=False)

        assert answer.answer_id is None
        assert _RECORDED == []

    def test_a_blank_question_is_a_caller_fault(self) -> None:
        service, _, model = build()

        with pytest.raises(ValueError, match="must not be blank"):
            service.ask("   ")

        assert model.calls == []


class TestNoEvidence:
    def test_the_model_is_not_called(self) -> None:
        """Measured at five seconds for a refusal we can derive for nothing."""
        service, _, model = build(candidates=[])

        service.ask("what was revenue?")

        assert model.calls == []

    def test_it_abstains_naming_the_corpus_rather_than_the_model(self) -> None:
        service, _, _ = build(candidates=[])

        decision = service.ask("what was revenue?").decision

        assert decision.decision is Decision.ABSTAINED
        assert decision.reason_codes == (REASON_NO_EVIDENCE,)
        assert decision.support_band is SupportBand.NONE

    def test_it_is_still_recorded(self) -> None:
        """A refusal is the outcome most worth being able to look up later."""
        service, _, _ = build(candidates=[])

        service.ask("what was revenue?")

        assert len(_RECORDED) == 1
        assert _RECORDED[0]["evidence_passages"] == 0

    def test_the_configured_model_is_recorded_although_none_ran(self) -> None:
        service, _, _ = build(candidates=[])

        assert service.ask("what was revenue?").model == MODEL


class TestGenerationFailures:
    """§26.10: losing the model costs prose, not the answer."""

    @pytest.mark.parametrize(
        ("generator", "flag"),
        [
            (FakeGenerator(fails=True), DEGRADED_GENERATION_UNAVAILABLE),
            (FakeGenerator(truncated=True), DEGRADED_GENERATION_TRUNCATED),
            (FakeGenerator(malformed=True), DEGRADED_GENERATION_CONTRACT),
        ],
    )
    def test_each_failure_abstains_with_its_own_flag(
        self, generator: FakeGenerator, flag: str
    ) -> None:
        service, _, _ = build(generator=generator)

        decision = service.ask("what was revenue?").decision

        assert decision.decision is Decision.ABSTAINED
        assert decision.degraded == (flag,)

    def test_the_evidence_survives_the_failure(self) -> None:
        """A reader given five cited passages has more than one given an exception."""
        service, _, _ = build(
            candidates=[candidate(rank=1), candidate(rank=2)],
            generator=FakeGenerator(fails=True),
        )

        answer = service.ask("what was revenue?")

        assert len(answer.evidence.passages) == 2
        assert answer.evidence.by_id(1) is not None

    def test_the_failure_is_recorded(self) -> None:
        service, _, _ = build(generator=FakeGenerator(fails=True))

        service.ask("what was revenue?")

        recorded = _RECORDED[0]["decision"]
        assert isinstance(recorded, AnswerDecision)
        assert recorded.degraded == (DEGRADED_GENERATION_UNAVAILABLE,)

    def test_a_retrieval_degradation_is_kept_alongside(self) -> None:
        """Two different facts about one answer; §27.9 keeps both."""
        service, _, _ = build(
            degraded=("dense_unavailable",), generator=FakeGenerator(fails=True)
        )

        degraded = service.ask("what was revenue?").decision.degraded

        assert degraded == ("dense_unavailable", DEGRADED_GENERATION_UNAVAILABLE)


class TestTheGateIsWired:
    """Not re-testing the Gate — checking the orchestration actually calls it."""

    def test_an_invented_numeral_is_withheld(self) -> None:
        service, _, _ = build(generator=FakeGenerator(invent_numeral="99,999"))

        decision = service.ask("what was revenue?").decision

        assert decision.decision is Decision.ABSTAINED
        assert decision.reason_codes[0] == "no_claim_survived_validation"
        assert REASON_UNSUPPORTED_NUMERAL in decision.reason_codes
        assert len(decision.withheld) == 1

    def test_a_citation_outside_the_evidence_set_is_withheld(self) -> None:
        service, _, _ = build(generator=FakeGenerator(cite_outside=99))

        decision = service.ask("what was revenue?").decision

        assert REASON_CITATION_NOT_IN_EVIDENCE in decision.reason_codes
        assert not decision.released

    def test_a_partial_answer_releases_what_survived(self) -> None:
        """A neighbour's invented figure must not discard a properly cited claim."""
        service, _, _ = build(
            generator=FakeGenerator(
                claims=(
                    "The company describes broad-based demand.",
                    "Revenue was 99,999 crore.",
                )
            )
        )

        decision = service.ask("what was revenue?").decision

        assert decision.decision is Decision.PARTIAL
        assert len(decision.released) == 1
        assert len(decision.withheld) == 1
        assert decision.support_band is SupportBand.WEAK

    def test_the_model_reporting_no_answer_is_its_own_reason(self) -> None:
        service, _, _ = build(generator=FakeGenerator(answerable=False, claims=()))

        decision = service.ask("what was revenue?").decision

        assert decision.decision is Decision.ABSTAINED
        assert decision.reason_codes == ("model_reported_unanswerable",)

    def test_two_periods_are_disclosed_rather_than_removed(self) -> None:
        other = UUID("0199a1f0-0000-7000-8000-00000000bbbb")
        _ELEMENT_TEXT[other] = "The prior year figure was 1,46,767 crore."
        service, _, _ = build(
            candidates=[
                candidate(rank=1),
                candidate(rank=2, element=other, period="FY2023-24"),
            ],
            generator=FakeGenerator(citations=(1, 2)),
        )

        decision = service.ask("how did revenue move?").decision

        assert decision.decision is Decision.ANSWERED
        assert decision.support_band is SupportBand.MODERATE
        assert [finding.code for finding in decision.released[0].disclosures] == [
            "mixed_period"
        ]


class TestEvidenceAssembly:
    def test_a_fragment_is_expanded_to_its_parent(self) -> None:
        """§20.8's mechanism, reached through the service rather than directly."""
        short = candidate(text="Revenue was 1,62,990 crore.")
        _PARENTS[short.chunk_id] = PendingChunk(
            chunk_id=uuid4(),
            document_version_id=uuid4(),
            text=SPAN,
            lexemes="",
            heading_path=("Management discussion",),
            page_numbers=(11,),
            evidence_type="narrative",
            issuer_name="Probe Limited",
            document_type="annual_report",
            fiscal_period="FY2024-25",
            reporting_basis="consolidated",
            currency="INR",
        )
        service, _, _ = build(candidates=[short])

        answer = service.ask("what was revenue?")

        assert answer.evidence.expanded == 1
        assert answer.evidence.by_id(1) is not None
        assert answer.evidence.by_id(1).text == SPAN  # type: ignore[union-attr]

    def test_the_budget_bounds_what_the_model_sees(self) -> None:
        service, _, _ = build(
            candidates=[candidate(rank=rank) for rank in range(1, 6)],
            budget_chars=60,
        )

        answer = service.ask("what was revenue?")

        assert len(answer.evidence.passages) == 1
        assert answer.evidence.dropped_for_budget == 4
        assert answer.evidence.considered == 5


class TestDisclosure:
    def test_no_timing_or_count_carries_document_text(self) -> None:
        """The footer an operator may pipe into a log holds no passage."""
        service, _, _ = build()

        answer = service.ask("what was revenue?")

        assert "crore" not in str(answer.timings_ms)
        assert answer.prompt_tokens > 0
