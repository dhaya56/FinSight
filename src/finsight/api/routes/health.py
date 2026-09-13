"""Liveness and readiness endpoints.

Liveness answers whether the process is running and deliberately touches no
dependency. Readiness answers whether every essential dependency is healthy, and
returns 503 when it is not, so an orchestrator or load balancer can act on the
status code alone.

The database probe is injected so that callers — including contract tests — can
substitute it. The application never calls it during start-up, which is what
allows the API to be constructed without any database configuration present.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from finsight.api.schemas.health import LivenessResponse, ReadinessResponse
from finsight.observability.dependency_health import (
    DependencyClass,
    DependencyProbe,
    evaluate_readiness,
    probe_dependency,
)
from finsight.persistence.database import is_database_reachable

POSTGRESQL_DEPENDENCY = "postgresql"

router = APIRouter(prefix="/health", tags=["health"])


def get_database_probe() -> DependencyProbe:
    """Provide the database reachability probe.

    Returns the callable without invoking it, so importing or constructing the
    application reads no settings and opens no connection.
    """
    return is_database_reachable


@router.get("/live", response_model=LivenessResponse, summary="Liveness")
def read_liveness() -> LivenessResponse:
    """Report that the process is running. Checks no dependency."""
    return LivenessResponse(status="alive")


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness")
def read_readiness(
    response: Response,
    database_probe: Annotated[DependencyProbe, Depends(get_database_probe)],
) -> ReadinessResponse:
    """Report readiness, returning 503 when an essential dependency is unhealthy."""
    report = evaluate_readiness(
        [
            probe_dependency(
                name=POSTGRESQL_DEPENDENCY,
                classification=DependencyClass.ESSENTIAL,
                probe=database_probe,
            )
        ]
    )
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(ready=report.ready)
