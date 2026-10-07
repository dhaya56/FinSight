"""The operations route: dependencies, index consistency, and answer activity.

**Authenticated, unlike `/health/ready`** (§28.10). Readiness answers "can this serve" to
anyone, including an orchestrator holding no token; *which* component is down, how large the
index is and what the system has been answering are facts about the deployment.

**Defined with ``def``, not ``async def``**, because the probes and repository calls are
synchronous; see :mod:`finsight.api.routes.search` for the full reasoning.

**A probe that raises is a probe that failed.** Each is bounded and already documented as
non-raising, but this route treats an exception as unhealthy rather than letting one
unreachable dependency take down the page that exists to report on it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from finsight.api.auth import require_token
from finsight.api.dependencies import SessionScope, get_session_scope
from finsight.api.schemas.system import (
    AnswerActivityModel,
    DependencyModel,
    IndexStateModel,
    SystemResponse,
)
from finsight.observability.dependency_health import DependencyClass
from finsight.persistence.database import is_database_reachable, is_schema_current
from finsight.persistence.repositories.operations import OperationsRepository
from finsight.vector_index.qdrant_index import is_vector_index_reachable

router = APIRouter(
    prefix="/v1",
    tags=["operations"],
    dependencies=[Depends(require_token)],
)

_PROBES = (
    ("PostgreSQL", DependencyClass.ESSENTIAL, is_database_reachable),
    ("Schema", DependencyClass.ESSENTIAL, is_schema_current),
    ("Qdrant", DependencyClass.DEGRADABLE, is_vector_index_reachable),
)


def _points() -> int | None:
    """How many points the vector collection holds, or ``None`` when it is unreachable.

    ``None`` is deliberately distinct from zero: an empty index and an index that could
    not be asked are different operational states, and conflating them would report a
    healthy-but-empty deployment as a broken one.
    """
    from finsight.vector_index.qdrant_index import build_vector_index

    try:
        return build_vector_index().count()
    except Exception:
        return None


@router.get(
    "/system",
    response_model=SystemResponse,
    summary="Dependency health, index consistency and answer activity",
    responses={
        401: {"description": "Missing or invalid bearer token."},
        503: {"description": "Authentication is not configured."},
    },
)
def system(
    scope: Annotated[SessionScope, Depends(get_session_scope)],
) -> SystemResponse:
    """Everything the operations surface needs, in one call."""
    dependencies = tuple(
        DependencyModel(
            name=name, classification=classification.value, healthy=_safely(probe)
        )
        for name, classification, probe in _PROBES
    )

    with scope() as session:
        repository = OperationsRepository(session)
        index = repository.index_state()
        activity = repository.answer_activity()

    points = _points()
    return SystemResponse(
        dependencies=dependencies,
        index=IndexStateModel(
            indexed_chunks=index.indexed_chunks,
            context_chunks=index.context_chunks,
            index_points=points,
            index_consistent=None if points is None else points == index.indexed_chunks,
            pending_events=index.pending_events,
            failed_events=index.failed_events,
            completed_events=index.completed_events,
        ),
        answers=AnswerActivityModel(
            total=activity.total,
            by_decision=activity.by_decision,
            by_support_band=activity.by_support_band,
            by_reason=activity.by_reason,
            median_elapsed_ms=activity.median_elapsed_ms,
            slowest_elapsed_ms=activity.slowest_elapsed_ms,
        ),
    )


def _safely(probe: object) -> bool:
    """Run a probe, treating a raise as a failure rather than a crash."""
    try:
        return bool(probe())  # type: ignore[operator]
    except Exception:
        return False
