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
from finsight.vector_index.qdrant_index import is_vector_index_reachable

POSTGRESQL_DEPENDENCY = "postgresql"
SCHEMA_DEPENDENCY = "schema"
VECTOR_INDEX_DEPENDENCY = "qdrant"

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


def get_vector_index_probe() -> DependencyProbe:
    """Provide the vector-index probe, likewise uninvoked."""
    return is_vector_index_reachable


@router.get("/live", response_model=LivenessResponse, summary="Liveness")
def read_liveness() -> LivenessResponse:
    """Report that the process is running. Checks no dependency."""
    return LivenessResponse(status="alive")


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness")
def read_readiness(
    response: Response,
    database_probe: Annotated[DependencyProbe, Depends(get_database_probe)],
    schema_probe: Annotated[DependencyProbe, Depends(get_schema_probe)],
    vector_index_probe: Annotated[
        DependencyProbe, Depends(get_vector_index_probe)
    ],
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

    # Degradable, not essential (§10.9): losing the vector index costs dense
    # retrieval, which §20.12 degrades to the lexical path with a flag. Reporting
    # it as essential would take the whole service out of readiness over a
    # capability that has a defined fallback.
    vector_index = probe_dependency(
        name=VECTOR_INDEX_DEPENDENCY,
        classification=DependencyClass.DEGRADABLE,
        probe=vector_index_probe,
    )

    report = evaluate_readiness([database, schema, vector_index])
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(ready=report.ready)
