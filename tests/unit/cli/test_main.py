"""Tests for the operator command line.

The disclosure test is the one with teeth. Everything else here is argument
parsing and exit codes; that one checks the CLI cannot print a filing into a
shared terminal or a log (CLAUDE.md §10).
"""

from uuid import UUID, uuid4

import pytest

from finsight.cli import main as cli
from finsight.domain.errors import DomainError
from finsight.domain.representations.source import ExtractionState, RecordedExtraction
from finsight.extraction.contracts import DocumentUnreadableError
from finsight.generation.decision import (
    AnswerDecision,
    Decision,
    ReleasedClaim,
    SupportBand,
    WithheldClaim,
)
from finsight.generation.evidence import EvidencePassage, EvidenceSet
from finsight.generation.resolution import ResolvedCitation
from finsight.generation.service import AskedAnswer
from finsight.generation.validation import Finding, Severity
from finsight.object_store.port import ObjectStoreUnavailableError
from finsight.retrieval.contracts import RetrievalFilters

VERSION_ID = UUID("0199a1f0-0000-7000-8000-000000000000")


class StubService:
    """Returns a prepared result, or raises a prepared error."""

    def __init__(
        self,
        result: RecordedExtraction | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.called_with: UUID | None = None

    def extract(self, document_version_id: UUID) -> RecordedExtraction:
        self.called_with = document_version_id
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def recorded(
    state: ExtractionState = ExtractionState.SUCCEEDED,
    *,
    already_existed: bool = False,
    element_count: int = 42,
) -> RecordedExtraction:
    return RecordedExtraction(
        run_id=UUID("0199a1f0-1111-7000-8000-000000000000"),
        document_version_id=VERSION_ID,
        state=state,
        element_count=element_count,
        already_existed=already_existed,
    )


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Swap the wired service for a stub, so no infrastructure is touched."""

    def _install(service: StubService) -> StubService:
        monkeypatch.setattr(cli, "build_extraction_service", lambda: service)
        return service

    return _install


def passage(identifier: int = 1) -> EvidencePassage:
    return EvidencePassage(
        id=identifier,
        chunk_id=uuid4(),
        stands_for=(uuid4(),),
        text="a passage",
        char_count=9,
        expanded=False,
        best_rank=identifier,
        heading_path=("Management discussion",),
        page_numbers=(12,),
        issuer_name="Probe Limited",
        fiscal_period="FY2024-25",
        reporting_basis="consolidated",
    )


def evidence_of(*passages: EvidencePassage) -> EvidenceSet:
    return EvidenceSet(
        passages=passages,
        budget_chars=20_000,
        used_chars=sum(item.char_count for item in passages),
        considered=len(passages),
        dropped_for_budget=0,
        expanded=0,
        merged=0,
        _by_id={item.id: item for item in passages},
    )


def asked(
    *,
    withheld: bool = False,
    span: str = "Revenue was 1,62,990 crore.",
    spans: int = 1,
) -> AskedAnswer:
    """A released answer, optionally with one claim the Gate removed.

    ``spans`` is how many source elements the cited passage resolved to — more than a handful
    is the normal case for a chunk covering a table.
    """
    cited = tuple(
        ResolvedCitation(
            passage_id=1,
            source_element_id=uuid4(),
            locator="p. 12",
            text=f"span {index + 1}: {span}" if spans > 1 else span,
        )
        for index in range(spans)
    )
    removed = (
        WithheldClaim(
            text="Revenue was 99,999 crore.",
            findings=(
                Finding(
                    claim_index=1,
                    code="unsupported_numeral",
                    severity=Severity.REMOVE,
                    detail="states 99,999, which appears in none of the passages it cites",
                ),
            ),
        ),
    )
    return AskedAnswer(
        question="what was revenue?",
        decision=AnswerDecision(
            decision=Decision.PARTIAL if withheld else Decision.ANSWERED,
            reason_codes=("unsupported_numeral",) if withheld else (),
            support_band=SupportBand.WEAK if withheld else SupportBand.STRONG,
            released=(
                ReleasedClaim(text="A supported claim.", citations=cited),
            ),
            withheld=removed if withheld else (),
        ),
        evidence=evidence_of(passage()),
        model="llama3.1:8b",
        answer_id=uuid4(),
        timings_ms={"retrieval_ms": 900, "generation_ms": 16_000, "total_ms": 17_000},
        prompt_tokens=1_840,
        completion_tokens=52,
    )


def abstained(*, degraded: tuple[str, ...] = ()) -> AskedAnswer:
    return AskedAnswer(
        question="what is the chief executive's pay?",
        decision=AnswerDecision(
            decision=Decision.ABSTAINED,
            reason_codes=("model_reported_unanswerable",),
            support_band=SupportBand.NONE,
            released=(),
            withheld=(),
            degraded=degraded,
        ),
        evidence=evidence_of(passage()),
        model="llama3.1:8b",
        answer_id=uuid4(),
        timings_ms={"total_ms": 5_100},
    )


class StubAskService:
    """Returns a prepared answer and records what it was asked."""

    def __init__(self, answer: AskedAnswer) -> None:
        self._answer = answer
        self.question: str | None = None
        self.filters: RetrievalFilters | None = None
        self.limit: int | None = None

    def ask(
        self,
        question: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 8,
    ) -> AskedAnswer:
        self.question = question
        self.filters = filters
        self.limit = limit
        return self._answer


@pytest.fixture
def ask(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Swap the wired answer path for a stub: no model, no index, no database."""

    def _install(answer: AskedAnswer) -> StubAskService:
        service = StubAskService(answer)
        monkeypatch.setattr(cli, "build_ask_service", lambda: service)
        return service

    return _install


class TestExtractCommand:
    def test_reports_what_was_recorded(self, install, capsys) -> None:  # type: ignore[no-untyped-def]
        install(StubService(recorded()))

        exit_code = cli.main(["extract", str(VERSION_ID)])
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "extraction recorded" in out
        assert "succeeded" in out
        assert "42" in out

    def test_passes_the_requested_version_through(self, install) -> None:  # type: ignore[no-untyped-def]
        service = install(StubService(recorded()))

        cli.main(["extract", str(VERSION_ID)])

        assert service.called_with == VERSION_ID

    def test_a_repeat_run_is_reported_as_a_no_op(self, install, capsys) -> None:  # type: ignore[no-untyped-def]
        """Idempotence is the expected mode, so it must not look like a failure."""
        install(StubService(recorded(already_existed=True)))

        exit_code = cli.main(["extract", str(VERSION_ID)])

        assert exit_code == 0
        assert "already recorded" in capsys.readouterr().out

    def test_a_partial_run_says_so(self, install, capsys) -> None:  # type: ignore[no-untyped-def]
        """An operator must not read 'partial' as 'done'."""
        install(StubService(recorded(ExtractionState.PARTIAL)))

        cli.main(["extract", str(VERSION_ID)])

        assert "not extracted" in capsys.readouterr().out


class TestFailures:
    @pytest.mark.parametrize(
        "error",
        [
            DocumentUnreadableError("the document is password protected"),
            ObjectStoreUnavailableError("the backend could not be reached"),
        ],
    )
    def test_a_known_failure_becomes_an_exit_code(
        self,
        install,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
        error: Exception,
    ) -> None:
        install(StubService(error=error))

        exit_code = cli.main(["extract", str(VERSION_ID)])

        assert exit_code == 1
        assert "error:" in capsys.readouterr().err

    def test_an_unexpected_error_keeps_its_traceback(self, install) -> None:  # type: ignore[no-untyped-def]
        """A swallowed stack trace is how a real defect looks like a bad document."""
        install(StubService(error=ZeroDivisionError("a genuine defect")))

        with pytest.raises(ZeroDivisionError):
            cli.main(["extract", str(VERSION_ID)])

    def test_domain_errors_are_caught_as_a_family(self, install) -> None:  # type: ignore[no-untyped-def]
        install(StubService(error=DomainError("some domain rule")))

        assert cli.main(["extract", str(VERSION_ID)]) == 1


class TestArgumentParsing:
    def test_a_malformed_identifier_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["extract", "not-a-uuid"])

    def test_a_missing_subcommand_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            cli.main([])

    def test_an_unknown_subcommand_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["reindex", str(uuid4())])


class TestAskCommand:
    """What the operator sees: the decision, what was withheld, and the sources.

    The service is stubbed, so these assert *presentation*. Two of them matter beyond
    formatting: that an abstention exits 0 — declining to answer is a correct outcome, not a
    command failure — and that what the Evidence Gate removed is printed, because an answer
    that silently omits it reads as complete when it is not.
    """

    def test_a_released_answer_prints_claims_with_inline_citations(
        self,
        ask,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        ask(asked())

        exit_code = cli.main(["ask", "what was revenue?"])
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "decision: answered" in out
        assert "support: strong" in out
        assert "A supported claim. [1]" in out
        assert "p. 12" in out

    def test_an_abstention_exits_zero_and_says_why(
        self,
        ask,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        """Declining to answer is §26.1's correct outcome, not a failure."""
        ask(abstained())

        exit_code = cli.main(["ask", "what is the chief executive's pay?"])
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "decision: abstained" in out
        assert "no claim was released" in out
        assert "reasons:  model_reported_unanswerable" in out

    def test_withheld_claims_are_printed_with_their_reasons(
        self,
        ask,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        ask(asked(withheld=True))

        cli.main(["ask", "what was revenue?"])
        out = capsys.readouterr().out

        assert "withheld 1 of 2 claim(s)" in out
        assert "unsupported_numeral" in out

    def test_degradation_is_announced_before_anything_else(
        self,
        ask,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        """A reader who stops at the first line must already know it is degraded."""
        ask(abstained(degraded=("generation_unavailable",)))

        cli.main(["ask", "what was revenue?"])
        lines = capsys.readouterr().out.splitlines()

        assert lines[0] == "DEGRADED: generation_unavailable"

    def test_the_filters_reach_the_service(self, ask) -> None:  # type: ignore[no-untyped-def]
        service = ask(asked())

        cli.main(
            ["ask", "what was revenue?", "--issuer", "Probe Limited", "--since", "2024"]
        )

        assert service.filters is not None
        assert service.filters.issuer_name == "Probe Limited"
        assert service.limit == 8

    def test_year_and_since_together_are_rejected(
        self,
        ask,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        ask(asked())

        exit_code = cli.main(["ask", "q", "--year", "2025", "--since", "2024"])

        assert exit_code == 1
        assert "--year or --since" in capsys.readouterr().err

    def test_a_cited_span_is_truncated_without_full(
        self,
        ask,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        """So a careless redirect spills a line rather than a filing."""
        ask(asked(span="x" * 400))

        cli.main(["ask", "what was revenue?"])
        out = capsys.readouterr().out

        assert "..." in out
        assert "x" * 400 not in out

    def test_the_span_list_is_bounded(
        self,
        ask,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        """A chunk over a table resolves to one element per row.

        Measured on the real corpus: one cited passage produced fourteen spans, of which
        twelve read like "2025 2024". Listing them all buries the two that carried the claim.
        """
        ask(asked(spans=7))

        cli.main(["ask", "what was revenue?"])
        out = capsys.readouterr().out

        assert "... and 3 further span(s) in this passage" in out
        assert "span 4" in out
        assert "span 5" not in out

    def test_full_prints_the_whole_span(
        self,
        ask,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        ask(asked(span="x" * 400))

        cli.main(["ask", "what was revenue?", "--full"])

        assert "x" * 400 in capsys.readouterr().out


class TestDisclosure:
    def test_output_carries_no_document_content_or_location(
        self,
        install,  # type: ignore[no-untyped-def]
        capsys,  # type: ignore[no-untyped-def]
    ) -> None:
        """Identifiers, a state and a count. Nothing that describes the filing."""
        install(StubService(recorded()))

        cli.main(["extract", str(VERSION_ID)])
        captured = capsys.readouterr()

        for forbidden in ("originals/sha256", ".pdf", "postgresql://", "Revenue"):
            assert forbidden not in captured.out
            assert forbidden not in captured.err
