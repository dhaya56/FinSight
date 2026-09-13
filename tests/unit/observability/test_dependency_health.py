"""Tests for the dependency health model."""

import pytest

from finsight.observability.dependency_health import (
    DependencyClass,
    DependencyStatus,
    HealthState,
    evaluate_readiness,
    probe_dependency,
)


def _status(classification: DependencyClass, state: HealthState) -> DependencyStatus:
    return DependencyStatus(name="probe", classification=classification, state=state)


class TestProbeDependency:
    def test_records_healthy_when_the_probe_answers(self) -> None:
        status = probe_dependency("postgresql", DependencyClass.ESSENTIAL, lambda: True)

        assert status.state is HealthState.HEALTHY
        assert status.name == "postgresql"
        assert status.classification is DependencyClass.ESSENTIAL

    def test_records_unhealthy_when_the_probe_does_not_answer(self) -> None:
        status = probe_dependency("postgresql", DependencyClass.ESSENTIAL, lambda: False)

        assert status.state is HealthState.UNHEALTHY


class TestBlocksReadiness:
    @pytest.mark.parametrize(
        ("classification", "state", "expected"),
        [
            (DependencyClass.ESSENTIAL, HealthState.UNHEALTHY, True),
            (DependencyClass.ESSENTIAL, HealthState.HEALTHY, False),
            (DependencyClass.DEGRADABLE, HealthState.UNHEALTHY, False),
            (DependencyClass.DEGRADABLE, HealthState.HEALTHY, False),
            (DependencyClass.OPTIONAL, HealthState.UNHEALTHY, False),
            (DependencyClass.OPTIONAL, HealthState.HEALTHY, False),
        ],
    )
    def test_only_a_failing_essential_dependency_blocks_readiness(
        self,
        classification: DependencyClass,
        state: HealthState,
        expected: bool,
    ) -> None:
        assert _status(classification, state).blocks_readiness is expected


class TestEvaluateReadiness:
    def test_ready_when_everything_is_healthy(self) -> None:
        report = evaluate_readiness(
            [
                _status(DependencyClass.ESSENTIAL, HealthState.HEALTHY),
                _status(DependencyClass.DEGRADABLE, HealthState.HEALTHY),
            ]
        )

        assert report.ready is True

    def test_not_ready_when_an_essential_dependency_is_unhealthy(self) -> None:
        report = evaluate_readiness(
            [
                _status(DependencyClass.ESSENTIAL, HealthState.UNHEALTHY),
                _status(DependencyClass.DEGRADABLE, HealthState.HEALTHY),
            ]
        )

        assert report.ready is False

    def test_degraded_capability_does_not_block_readiness(self) -> None:
        report = evaluate_readiness(
            [
                _status(DependencyClass.ESSENTIAL, HealthState.HEALTHY),
                _status(DependencyClass.DEGRADABLE, HealthState.UNHEALTHY),
                _status(DependencyClass.OPTIONAL, HealthState.UNHEALTHY),
            ]
        )

        assert report.ready is True

    def test_one_failing_essential_dependency_is_enough(self) -> None:
        report = evaluate_readiness(
            [
                _status(DependencyClass.ESSENTIAL, HealthState.HEALTHY),
                _status(DependencyClass.ESSENTIAL, HealthState.UNHEALTHY),
            ]
        )

        assert report.ready is False

    def test_no_dependencies_is_ready(self) -> None:
        assert evaluate_readiness([]).ready is True

    def test_checked_dependencies_are_preserved_in_order(self) -> None:
        first = DependencyStatus("first", DependencyClass.ESSENTIAL, HealthState.HEALTHY)
        second = DependencyStatus("second", DependencyClass.OPTIONAL, HealthState.UNHEALTHY)

        report = evaluate_readiness([first, second])

        assert report.dependencies == (first, second)
