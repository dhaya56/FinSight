"""Integration tests for generations — what is queryable, and what is merely built.

These are integration tests rather than unit tests because every rule under test is
enforced by the database. §11.13 says a failed shadow run never replaces active
evidence; an application that merely *intends* that is one forgotten branch away
from publishing a half-indexed filing. So the tests assert the ``IntegrityError``,
not a Python guard.

The sharpest one is
:meth:`TestActivation.test_activating_a_second_generation_supersedes_the_first` —
two active generations for one document version would make §20.2's filter ambiguous
and a reader would receive evidence from both.

They require the stack from ``compose.yaml`` and fail rather than skip when it is
down, for the reason given in ``test_postgres.py``.
"""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finsight.persistence.database import dispose_engine, get_engine
from finsight.persistence.repositories.documents import DocumentRepository
from finsight.persistence.repositories.generations import GenerationRepository
from finsight.persistence.tables.documents import DocumentVersion
from finsight.persistence.tables.generations import (
    STATE_ACTIVE,
    STATE_FAILED,
    STATE_SHADOW,
    STATE_SUPERSEDED,
    Generation,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _release_pool() -> Iterator[None]:
    yield
    dispose_engine()


@pytest.fixture
def session() -> Iterator[Session]:
    """A session whose work is discarded, so tests leave no rows behind."""
    connection = get_engine().connect()
    transaction = connection.begin()
    opened = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield opened
    finally:
        opened.close()
        transaction.rollback()
        connection.close()


def _version(session: Session) -> UUID:
    content_hash = f"{uuid4().hex}{uuid4().hex}"
    recorded = DocumentRepository(session).record_version(
        hash_algorithm="sha256",
        content_hash=content_hash,
        byte_size=1024,
        detected_content_type="application/pdf",
        object_key=f"originals/sha256/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}",
        original_filename="probe.pdf",
    )
    return recorded.id


def _run(session: Session, version_id: UUID) -> UUID:
    """An extraction run, so a generation has a component to activate on."""
    return session.execute(
        text(
            "INSERT INTO extraction_runs"
            " (document_version_id, format, producer_policy, config_version,"
            "  state, element_count)"
            " VALUES (:v, 'pdf', 'pdf-native', '1', 'succeeded', 0) RETURNING id"
        ),
        {"v": version_id},
    ).scalar_one()


class TestOpening:
    def test_a_generation_is_born_shadow(self, session: Session) -> None:
        """§11.12: nothing is queryable until activation says so."""
        version_id = _version(session)

        generation_id = GenerationRepository(session).open(
            document_version_id=version_id
        )

        assert GenerationRepository(session).state_of(
            generation_id=generation_id
        ) == STATE_SHADOW

    def test_a_shadow_generation_is_not_active_for_its_version(
        self, session: Session
    ) -> None:
        version_id = _version(session)
        GenerationRepository(session).open(document_version_id=version_id)

        assert (
            GenerationRepository(session).active_for(document_version_id=version_id)
            is None
        )

    def test_several_shadow_generations_may_coexist(self, session: Session) -> None:
        """The partial unique index constrains active ones only.

        Reprocessing builds a new generation while the old one is still serving,
        so refusing a second shadow would make §11.12 reprocessing impossible.
        """
        version_id = _version(session)
        repository = GenerationRepository(session)

        repository.open(document_version_id=version_id)
        repository.open(document_version_id=version_id)
        session.flush()

        count = session.execute(
            select(Generation).where(Generation.document_version_id == version_id)
        ).all()
        assert len(count) == 2


class TestActivation:
    def test_activation_makes_a_generation_queryable(self, session: Session) -> None:
        version_id = _version(session)
        repository = GenerationRepository(session)
        generation_id = repository.open(
            document_version_id=version_id,
            extraction_run_id=_run(session, version_id),
        )

        repository.activate(generation_id=generation_id)

        assert repository.active_for(document_version_id=version_id) == generation_id

    def test_activation_records_its_time_from_the_database_clock(
        self, session: Session
    ) -> None:
        version_id = _version(session)
        repository = GenerationRepository(session)
        generation_id = repository.open(
            document_version_id=version_id,
            extraction_run_id=_run(session, version_id),
        )

        repository.activate(generation_id=generation_id)

        assert repository.activated_at(generation_id=generation_id) is not None

    def test_activation_moves_the_pointer_on_the_version(
        self, session: Session
    ) -> None:
        version_id = _version(session)
        repository = GenerationRepository(session)
        generation_id = repository.open(
            document_version_id=version_id,
            extraction_run_id=_run(session, version_id),
        )

        repository.activate(generation_id=generation_id)

        version = session.get(DocumentVersion, version_id)
        assert version is not None
        assert version.active_generation_id == generation_id

    def test_activating_a_second_generation_supersedes_the_first(
        self, session: Session
    ) -> None:
        """Two active generations would make §20.2's filter ambiguous.

        A reader would receive evidence from both, with no way to tell that the
        filing had been reprocessed between them.
        """
        version_id = _version(session)
        run_id = _run(session, version_id)
        repository = GenerationRepository(session)
        first = repository.open(
            document_version_id=version_id, extraction_run_id=run_id
        )
        repository.activate(generation_id=first)

        second = repository.open(
            document_version_id=version_id, extraction_run_id=run_id
        )
        repository.activate(generation_id=second)

        assert repository.state_of(generation_id=first) == STATE_SUPERSEDED
        assert repository.active_for(document_version_id=version_id) == second

    def test_a_superseded_generation_keeps_no_activation_time(
        self, session: Session
    ) -> None:
        """A leftover timestamp reads as evidence the generation is still live."""
        version_id = _version(session)
        run_id = _run(session, version_id)
        repository = GenerationRepository(session)
        first = repository.open(
            document_version_id=version_id, extraction_run_id=run_id
        )
        repository.activate(generation_id=first)
        second = repository.open(
            document_version_id=version_id, extraction_run_id=run_id
        )
        repository.activate(generation_id=second)

        assert repository.activated_at(generation_id=first) is None

    def test_a_superseded_generation_is_retained(self, session: Session) -> None:
        """§27.7: citations issued against it must keep resolving."""
        version_id = _version(session)
        run_id = _run(session, version_id)
        repository = GenerationRepository(session)
        first = repository.open(
            document_version_id=version_id, extraction_run_id=run_id
        )
        repository.activate(generation_id=first)
        second = repository.open(
            document_version_id=version_id, extraction_run_id=run_id
        )
        repository.activate(generation_id=second)

        assert session.get(Generation, first) is not None


class TestFailure:
    def test_failing_a_shadow_leaves_the_active_generation_untouched(
        self, session: Session
    ) -> None:
        """§11.13: a failed shadow run never replaces active evidence.

        This is the test that matters most. Reprocessing failing halfway must leave
        the filing exactly as queryable as it was before, not partially replaced.
        """
        version_id = _version(session)
        run_id = _run(session, version_id)
        repository = GenerationRepository(session)
        live = repository.open(document_version_id=version_id, extraction_run_id=run_id)
        repository.activate(generation_id=live)

        doomed = repository.open(document_version_id=version_id)
        repository.fail(generation_id=doomed)

        assert repository.state_of(generation_id=doomed) == STATE_FAILED
        assert repository.active_for(document_version_id=version_id) == live


class TestTheDatabaseRefusesUnsafeStates:
    """The constraints, asserted through the database rather than trusted."""

    def test_an_unknown_state_is_refused(self, session: Session) -> None:
        version_id = _version(session)
        session.add(
            Generation(document_version_id=version_id, state="probably_fine")
        )

        with pytest.raises(IntegrityError, match="state_known"):
            session.flush()

    def test_active_without_an_activation_time_is_refused(
        self, session: Session
    ) -> None:
        version_id = _version(session)
        session.add(
            Generation(
                document_version_id=version_id,
                state=STATE_ACTIVE,
                extraction_run_id=_run(session, version_id),
            )
        )

        with pytest.raises(IntegrityError, match="activated_at_matches_state"):
            session.flush()

    def test_active_with_no_component_run_is_refused(self, session: Session) -> None:
        """A generation with no extraction run has no evidence behind it."""
        version_id = _version(session)

        with pytest.raises(IntegrityError, match="active_requires_components"):
            session.execute(
                text(
                    "INSERT INTO generations"
                    " (document_version_id, state, activated_at)"
                    " VALUES (:v, 'active', clock_timestamp())"
                ),
                {"v": version_id},
            )

    def test_two_active_generations_for_one_version_are_refused(
        self, session: Session
    ) -> None:
        """The index, not the repository, is what makes this impossible.

        Two concurrent activations would both read "none active" and both proceed,
        so a prior read cannot enforce it.
        """
        version_id = _version(session)
        run_id = _run(session, version_id)
        repository = GenerationRepository(session)
        first = repository.open(
            document_version_id=version_id, extraction_run_id=run_id
        )
        repository.activate(generation_id=first)
        session.flush()

        second = repository.open(
            document_version_id=version_id, extraction_run_id=run_id
        )

        # Activating directly rather than through the repository, which supersedes
        # the incumbent first. The point is that the index refuses the state even
        # when the application does not.
        with pytest.raises(IntegrityError, match="uq_generations_document_version_id"):
            session.execute(
                text(
                    "UPDATE generations SET state = 'active',"
                    " activated_at = clock_timestamp() WHERE id = :g"
                ),
                {"g": second},
            )
