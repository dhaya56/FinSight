"""Pruning: what it removes, and the three guards that stop it removing more.

This is the only destructive path in the project, and its failure mode is silent — a
search simply returns less. So most of these tests are about refusal rather than removal:
an empty target list, an overlap between prunable and active, and an active count that
moves during the operation.

The index is faked. What is under test is the service's safety logic, not Qdrant.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import ClassVar
from uuid import UUID, uuid4

import pytest

from finsight.indexing.pruning import PruneReport, PruningError, PruningService
from finsight.vector_index.qdrant_index import GENERATION_FIELD


class FakeIndex:
    """Holds points as a generation -> count mapping, and records what was deleted."""

    name = "fake"

    def __init__(self, points: dict[UUID, int] | None = None) -> None:
        self.points: dict[UUID, int] = dict(points or {})
        self.deleted: list[tuple[UUID, ...]] = []
        self.on_delete: object | None = None

    @property
    def collection(self) -> str:
        return self.name

    def count(self, *, filters: Mapping[str, object] | None = None) -> int:
        if not filters:
            return sum(self.points.values())
        wanted = filters[GENERATION_FIELD]
        assert isinstance(wanted, list)
        return sum(
            count
            for generation, count in self.points.items()
            if str(generation) in wanted
        )

    def delete_generations(self, generation_ids: Sequence[UUID]) -> int:
        self.deleted.append(tuple(generation_ids))
        if not generation_ids:
            return 0
        removed = 0
        for generation in generation_ids:
            removed += self.points.pop(generation, 0)
        # Lets a test simulate a filter that also removed something it should not.
        if callable(self.on_delete):
            self.on_delete(self)
        return removed


class FakeGenerations:
    """Stands in for GenerationRepository.

    Class attributes rather than constructor arguments: the service constructs the
    repository itself inside its session scope, so a test cannot reach the instance.
    They are reset on both sides of every test so one case cannot leak into the next.
    """

    prunable: ClassVar[list[UUID]] = []
    active: ClassVar[list[UUID]] = []
    include_failed_seen: ClassVar[list[bool]] = []

    def __init__(self, _session: object) -> None:
        pass

    def prunable_ids(self, *, include_failed: bool = False) -> list[UUID]:
        FakeGenerations.include_failed_seen.append(include_failed)
        return list(FakeGenerations.prunable)

    def active_ids(self) -> list[UUID]:
        return list(FakeGenerations.active)


@pytest.fixture(autouse=True)
def _patched_repository(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    FakeGenerations.prunable = []
    FakeGenerations.active = []
    FakeGenerations.include_failed_seen = []
    monkeypatch.setattr(
        "finsight.indexing.pruning.GenerationRepository", FakeGenerations
    )
    yield
    FakeGenerations.prunable = []
    FakeGenerations.active = []
    FakeGenerations.include_failed_seen = []


def build(index: FakeIndex) -> PruningService:
    @contextmanager
    def scope() -> Iterator[None]:
        yield None

    return PruningService(index=index, session_scope_factory=scope)  # type: ignore[arg-type]


class TestReporting:
    def test_reporting_is_the_default_and_removes_nothing(self) -> None:
        """A prune meant as a dry run that deleted would cost a 40-minute re-index."""
        superseded, active = uuid4(), uuid4()
        FakeGenerations.prunable = [superseded]
        FakeGenerations.active = [active]
        index = FakeIndex({superseded: 4969, active: 4867})

        report = build(index).prune()

        assert report.applied is False
        assert report.removed == 0
        assert index.deleted == []
        assert index.count() == 9836

    def test_the_report_separates_prunable_from_active(self) -> None:
        superseded, active = uuid4(), uuid4()
        FakeGenerations.prunable = [superseded]
        FakeGenerations.active = [active]

        report = build(FakeIndex({superseded: 4969, active: 4867})).prune()

        assert report.points_total_before == 9836
        assert report.points_prunable == 4969
        assert report.points_active_before == 4867
        assert report.would_remove == 4969

    def test_nothing_prunable_is_not_an_error(self) -> None:
        active = uuid4()
        FakeGenerations.active = [active]
        index = FakeIndex({active: 4867})

        report = build(index).prune(confirm=True)

        assert report.applied is False
        assert index.deleted == []


class TestRemoval:
    def test_confirm_removes_the_prunable_points(self) -> None:
        superseded, active = uuid4(), uuid4()
        FakeGenerations.prunable = [superseded]
        FakeGenerations.active = [active]
        index = FakeIndex({superseded: 4969, active: 4867})

        report = build(index).prune(confirm=True)

        assert report.applied is True
        assert report.removed == 4969
        assert report.points_total_after == 4867
        assert index.count() == 4867

    def test_active_points_are_untouched(self) -> None:
        """The number that must not change, asserted rather than assumed."""
        superseded, active = uuid4(), uuid4()
        FakeGenerations.prunable = [superseded]
        FakeGenerations.active = [active]
        index = FakeIndex({superseded: 100, active: 4867})

        report = build(index).prune(confirm=True)

        assert report.points_active_before == 4867
        assert report.points_active_after == 4867

    def test_several_generations_are_removed_in_one_call(self) -> None:
        first, second, active = uuid4(), uuid4(), uuid4()
        FakeGenerations.prunable = [first, second]
        FakeGenerations.active = [active]
        index = FakeIndex({first: 10, second: 20, active: 30})

        report = build(index).prune(confirm=True)

        assert report.removed == 30
        assert index.deleted == [(first, second)]

    def test_failed_generations_are_opt_in(self) -> None:
        build(FakeIndex()).prune(include_failed=True)

        assert FakeGenerations.include_failed_seen == [True]

    def test_failed_generations_are_excluded_by_default(self) -> None:
        build(FakeIndex()).prune()

        assert FakeGenerations.include_failed_seen == [False]


class TestRefusals:
    def test_an_overlap_between_prunable_and_active_refuses(self) -> None:
        """The state machine cannot produce this, so if it happens something is wrong.

        Refusing is the only safe response: the alternative deletes points that are
        being served, and the index is the only place that would notice.
        """
        shared = uuid4()
        FakeGenerations.prunable = [shared]
        FakeGenerations.active = [shared]
        index = FakeIndex({shared: 4867})

        with pytest.raises(PruningError, match="both prunable and active"):
            build(index).prune(confirm=True)

        assert index.deleted == []
        assert index.count() == 4867

    def test_an_overlap_refuses_even_when_only_reporting(self) -> None:
        """The invariant is broken whether or not this call intended to act."""
        shared = uuid4()
        FakeGenerations.prunable = [shared]
        FakeGenerations.active = [shared]

        with pytest.raises(PruningError):
            build(FakeIndex({shared: 1})).prune()

    def test_a_change_in_active_points_raises_after_the_fact(self) -> None:
        """The last line of defence, for a filter wrong in a way the others missed."""
        superseded, active = uuid4(), uuid4()
        FakeGenerations.prunable = [superseded]
        FakeGenerations.active = [active]
        index = FakeIndex({superseded: 10, active: 4867})

        def also_eat_active(fake: FakeIndex) -> None:
            fake.points[active] = 4000

        index.on_delete = also_eat_active

        with pytest.raises(PruningError, match="active points changed"):
            build(index).prune(confirm=True)

    def test_the_failure_message_says_how_to_recover(self) -> None:
        """A prune that went wrong is repairable, and the operator needs to know."""
        superseded, active = uuid4(), uuid4()
        FakeGenerations.prunable = [superseded]
        FakeGenerations.active = [active]
        index = FakeIndex({superseded: 10, active: 10})
        index.on_delete = lambda fake: fake.points.__setitem__(active, 0)

        with pytest.raises(PruningError, match="corpus index"):
            build(index).prune(confirm=True)


class TestFieldAgreement:
    def test_the_generation_payload_key_matches_retrieval(self) -> None:
        """Two literals in two layers. If they drift, every filter matches nothing.

        ``vector_index`` cannot import ``retrieval`` — that would invert the
        dependency — so the constant is declared twice and pinned here instead.
        """
        from finsight.retrieval.contracts import GENERATION_FIELD as RETRIEVAL_FIELD

        assert GENERATION_FIELD == RETRIEVAL_FIELD


class TestReportShape:
    def test_a_report_with_no_removal_still_describes_the_collection(self) -> None:
        report = PruneReport(
            prunable=(),
            active=(),
            points_total_before=0,
            points_prunable=0,
            points_active_before=0,
        )

        assert report.applied is False
        assert report.would_remove == 0
