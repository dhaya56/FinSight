"""Liveness and readiness endpoints.

Liveness answers whether the process is running and deliberately touches no
dependency. Readiness answers whether every essential dependency is healthy, and
returns 503 when one is not, so an orchestrator or load balancer can act on the
status code alone.

Both probes are injected so that callers — including contract tests — can
substitute them. The application never calls them during start-up, which is what
allows the API to be constructed without any database configuration present.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from finsight.api.schemas.health import LivenessResponse, ReadinessResponse
from finsight.observability.dependency_health import (
    DependencyClass,
    DependencyProbe,
    DependencyStatus,
    HealthState,
    evaluate_readiness,
    probe_dependency,
)
from finsight.persistence.database import is_database_reachable, is_schema_current

POSTGRESQL_DEPENDENCY = "postgresql"
SCHEMA_DEPENDENCY = "schema"

router = APIRouter(prefix="/health", tags=["health"])


def get_database_probe() -> DependencyProbe:
    """Provide the database reachability probe.

    Returns the callable without invoking it, so importing or constructing the
    application reads no settings and opens no connection.
    """
    return is_database_reachable


def get_schema_probe() -> DependencyProbe:
    """Provide the schema-currency probe, likewise uninvoked."""
    return is_schema_current


@router.get("/live", response_model=LivenessResponse, summary="Liveness")
def read_liveness() -> LivenessResponse:
    """Report that the process is running. Checks no dependency."""
    return LivenessResponse(status="alive")


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness")
def read_readiness(
    response: Response,
    database_probe: Annotated[DependencyProbe, Depends(get_database_probe)],
    schema_probe: Annotated[DependencyProbe, Depends(get_schema_probe)],
) -> ReadinessResponse:
    """Report readiness, returning 503 when an essential dependency is unhealthy."""
    database = probe_dependency(
        name=POSTGRESQL_DEPENDENCY,
        classification=DependencyClass.ESSENTIAL,
        probe=database_probe,
    )

    # The schema probe needs the same connection the reachability probe just
    # failed to obtain. Running it anyway would pay the connect timeout twice for
    # one answer that is already decided, so an unreachable database reports its
    # schema as unhealthy without a second attempt.
    if database.state is HealthState.HEALTHY:
        schema = probe_dependency(
            name=SCHEMA_DEPENDENCY,
            classification=DependencyClass.ESSENTIAL,
            probe=schema_probe,
        )
    else:
        schema = DependencyStatus(
            name=SCHEMA_DEPENDENCY,
            classification=DependencyClass.ESSENTIAL,
            state=HealthState.UNHEALTHY,
        )

    report = evaluate_readiness([database, schema])
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(ready=report.ready)
