"""Dependency health model.

Each dependency is classified by what its loss costs the system
(PROJECT_BLUEPRINT.md §10.9, §31.1):

* **essential** — the system cannot serve correctly without it, so readiness fails.
* **degradable** — capability is reduced but sound answers remain possible.
* **optional** — absence changes nothing a caller can observe.

Readiness therefore reports whether every essential dependency is healthy, not
whether everything is perfect. Operating-mode reporting for degraded components
arrives with the observability phase.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

DependencyProbe = Callable[[], bool]
"""Returns True when the dependency answers. Probes must be bounded and must not raise."""


class DependencyClass(StrEnum):
    """What the loss of a dependency costs."""

    ESSENTIAL = "essential"
    DEGRADABLE = "degradable"
    OPTIONAL = "optional"


class HealthState(StrEnum):
    """Observed state of a single dependency."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """The observed state of one classified dependency."""

    name: str
    classification: DependencyClass
    state: HealthState

    @property
    def blocks_readiness(self) -> bool:
        """True when this dependency alone is enough to make the system not ready."""
        return (
            self.classification is DependencyClass.ESSENTIAL
            and self.state is HealthState.UNHEALTHY
        )


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Aggregate readiness across the checked dependencies."""

    ready: bool
    dependencies: tuple[DependencyStatus, ...]


def probe_dependency(
    name: str,
    classification: DependencyClass,
    probe: DependencyProbe,
) -> DependencyStatus:
    """Run one probe and record the resulting status."""
    state = HealthState.HEALTHY if probe() else HealthState.UNHEALTHY
    return DependencyStatus(name=name, classification=classification, state=state)


def evaluate_readiness(statuses: Iterable[DependencyStatus]) -> ReadinessReport:
    """Aggregate dependency statuses into a readiness decision.

    Ready when no essential dependency is unhealthy. Degradable and optional
    failures never block readiness; they reduce capability, which is reported
    separately once operating modes exist. With no dependencies to check the
    result is ready, because nothing essential is failing.
    """
    checked = tuple(statuses)
    ready = not any(status.blocks_readiness for status in checked)
    return ReadinessReport(ready=ready, dependencies=checked)
