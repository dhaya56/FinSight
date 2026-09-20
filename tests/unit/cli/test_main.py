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
from finsight.object_store.port import ObjectStoreUnavailableError

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
