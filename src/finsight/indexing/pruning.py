"""Removing index points that belong to generations no reader can see.

A re-chunk leaves the previous generation's points in Qdrant. They are never served —
§20.2 binds every search to the active generations and
:meth:`RetrievalFilters.as_payload` writes that bound itself — so they are a storage
cost rather than a correctness risk. After one re-chunk of the development corpus they
were **half the collection**: 4,969 dead points against 4,867 live ones.

**This is the only destructive operation in the project, and it is deliberately confined
to the derived store.** §29.2 makes the vector index rebuildable from PostgreSQL, so the
cost of removing the wrong points is a re-index. The authoritative store keeps everything:
``repositories/documents.py`` still has no delete, §29.12 reserves removal for tombstoning,
and a superseded generation's rows are the audit trail it exists for.

The safety design is in three places and it is worth naming, because a prune that goes
wrong is silent — a search simply returns less:

* the generations to remove are **named explicitly** by the repository, by listing the
  states that may go rather than excluding the states that may not;
* the service **asserts the target set and the active set are disjoint** before deleting,
  so an invariant violation stops the operation instead of being carried into it;
* the active points are **counted before and after**, and a change raises. If the filter
  were ever wrong in a way the first two checks missed, this is what notices.

Nothing here runs by itself. Pruning is a developer action under §4, and
:meth:`PruningService.prune` reports without acting unless ``confirm`` is passed.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from finsight.domain.errors import DomainError
from finsight.persistence.database import session_scope
from finsight.persistence.repositories.generations import GenerationRepository
from finsight.vector_index.port import VectorIndex
from finsight.vector_index.qdrant_index import GENERATION_FIELD

__all__ = ["PruneReport", "PruningError", "PruningService", "build_pruning_service"]


class PruningError(DomainError):
    """A prune was refused, or did not do what it was asked to do."""


@dataclass(frozen=True, slots=True)
class PruneReport:
    """What a prune found, and what it did about it."""

    prunable: tuple[UUID, ...]
    active: tuple[UUID, ...]
    points_total_before: int
    points_prunable: int
    points_active_before: int

    removed: int = 0
    points_total_after: int = 0
    points_active_after: int = 0
    applied: bool = False
    """False when the service only reported. The default, and the safe one."""

    @property
    def would_remove(self) -> int:
        """Points the prune is able to remove, whether or not it did."""
        return self.points_prunable


@dataclass(frozen=True, slots=True)
class PruningService:
    """Removes the index points of generations no reader can see."""

    index: VectorIndex
    session_scope_factory: Callable[[], AbstractContextManager[Session]] = session_scope

    def prune(self, *, include_failed: bool = False, confirm: bool = False) -> PruneReport:
        """Report what can be removed, and remove it when ``confirm`` is passed.

        Reporting is the default because this is irreversible within the index. Rebuilding
        takes a re-index — 40 minutes for the development corpus — so a prune that was
        meant to be a dry run is an expensive mistake rather than a harmless one.

        Raises:
            PruningError: a generation is listed as both prunable and active, or the
                number of active points changed. Either means the filter is not doing
                what this code believes, and continuing would remove live evidence.
        """
        with self.session_scope_factory() as session:
            generations = GenerationRepository(session)
            prunable = tuple(generations.prunable_ids(include_failed=include_failed))
            active = tuple(generations.active_ids())

        overlap = set(prunable) & set(active)
        if overlap:
            raise PruningError(
                f"{len(overlap)} generation(s) are listed as both prunable and active: "
                f"{sorted(str(identifier) for identifier in overlap)}. Refusing to "
                "delete; the state machine or the query is wrong."
            )

        total_before = self.index.count()
        active_before = self._count_of(active)
        prunable_points = self._count_of(prunable)

        report = PruneReport(
            prunable=prunable,
            active=active,
            points_total_before=total_before,
            points_prunable=prunable_points,
            points_active_before=active_before,
        )
        if not confirm or not prunable:
            return report

        removed = self.index.delete_generations(prunable)
        active_after = self._count_of(active)
        if active_after != active_before:
            raise PruningError(
                f"active points changed from {active_before} to {active_after} during a "
                "prune. The index no longer matches what was served; rebuild it with "
                "'corpus index' before trusting a search."
            )

        return PruneReport(
            prunable=prunable,
            active=active,
            points_total_before=total_before,
            points_prunable=prunable_points,
            points_active_before=active_before,
            removed=removed,
            points_total_after=self.index.count(),
            points_active_after=active_after,
            applied=True,
        )

    def _count_of(self, generation_ids: tuple[UUID, ...]) -> int:
        """Points belonging to the named generations, or 0 when none are named.

        Short-circuited rather than passed through as an empty filter. An empty sequence
        counts nothing by design (``MatchAny`` over no values matches nothing), but the
        count and the delete read the same selector and the delete must never see a shape
        that could be mistaken for "unfiltered".
        """
        if not generation_ids:
            return 0
        targets = [str(generation) for generation in generation_ids]
        return self.index.count(filters={GENERATION_FIELD: targets})


REBUILD_HINT: Final = (
    "Points removed here are rebuildable: re-run 'corpus index' to restore them."
)


def build_pruning_service() -> PruningService:
    """Wire pruning to the configured index."""
    from finsight.config.settings import get_settings
    from finsight.vector_index.qdrant_index import build_vector_index

    return PruningService(index=build_vector_index(get_settings()))
