"""Integration tests for PostgreSQL connectivity and transaction behavior.

These require the container from ``compose.yaml`` to be running and resolve the
real configuration from ``.env``. They are excluded from the default test run and
requested explicitly with ``python -m pytest -m integration``. When the database
is unreachable they fail rather than skip: a silent skip would let a broken
connection look like success.
"""

from collections.abc import Iterator
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finsight.api.app import create_app
from finsight.domain.representations.source import (
    BlockLocation,
    ElementType,
    ExtractedElement,
    ExtractionState,
    PageLocation,
    location_from_mapping,
)
from finsight.persistence.database import (
    dispose_engine,
    get_engine,
    is_database_reachable,
    is_schema_current,
    session_scope,
)
from finsight.persistence.repositories.documents import DocumentRepository
from finsight.persistence.repositories.source import SourceRepository, count_elements
from finsight.persistence.tables.documents import STATE_RECEIVED, DocumentVersion
from finsight.persistence.tables.source import FORMAT_PDF, ExtractionRun, SourceElement

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True, scope="module")
def _release_pool() -> Iterator[None]:
    """Return pooled connections once this module is done with them."""
    yield
    dispose_engine()


@pytest.fixture
def scratch_table() -> Iterator[str]:
    """Create a uniquely named table for transaction tests and drop it afterwards.

    A real table rather than a temporary one: a temporary table lives only as long
    as its connection, and pooled connections are reused, which would make the
    commit and rollback assertions depend on pool behavior instead of transaction
    behavior. The name is generated here and never derived from input.
    """
    name = f"finsight_txn_probe_{uuid4().hex}"
    with get_engine().begin() as connection:
        connection.execute(text(f'CREATE TABLE "{name}" (value integer NOT NULL)'))
    try:
        yield name
    finally:
        with get_engine().begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{name}"'))


def _row_count(table: str) -> int:
    with get_engine().connect() as connection:
        result = connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
    return int(result)


def test_database_is_reachable() -> None:
    assert is_database_reachable() is True


def test_server_reports_expected_major_version() -> None:
    with get_engine().connect() as connection:
        version = connection.execute(text("SHOW server_version")).scalar_one()

    assert str(version).startswith("18.")


def test_session_scope_commits_on_clean_exit(scratch_table: str) -> None:
    with session_scope() as session:
        session.execute(text(f'INSERT INTO "{scratch_table}" (value) VALUES (1)'))

    assert _row_count(scratch_table) == 1


def test_session_scope_rolls_back_on_exception(scratch_table: str) -> None:
    class ProbeError(RuntimeError):
        """Raised only by this test."""

    with pytest.raises(ProbeError), session_scope() as session:
        session.execute(text(f'INSERT INTO "{scratch_table}" (value) VALUES (2)'))
        raise ProbeError("rollback probe")

    assert _row_count(scratch_table) == 0


def test_session_scope_reraises_the_original_exception(scratch_table: str) -> None:
    class ProbeError(RuntimeError):
        """Raised only by this test."""

    with pytest.raises(ProbeError, match="original message"), session_scope() as session:
        session.execute(text(f'INSERT INTO "{scratch_table}" (value) VALUES (3)'))
        raise ProbeError("original message")


def test_dispose_engine_is_idempotent() -> None:
    assert is_database_reachable() is True

    dispose_engine()
    dispose_engine()

    assert is_database_reachable() is True


def test_readiness_reports_ready_against_the_live_database() -> None:
    """Readiness with the real probes, not overrides, against the running container."""
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_schema_is_migrated_to_head() -> None:
    """The applied revision matches the head of the migrations directory."""
    assert is_schema_current() is True


@pytest.fixture
def rolled_back_session() -> Iterator[Session]:
    """A session whose work is discarded, so tests leave no rows behind.

    The repository opens savepoints, so the session joins the outer transaction
    with ``create_savepoint`` rather than starting its own.
    """
    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _record(session: Session, content_hash: str) -> DocumentVersion:
    return DocumentRepository(session).record_version(
        hash_algorithm="sha256",
        content_hash=content_hash,
        byte_size=1024,
        detected_content_type="application/pdf",
        object_key=f"originals/sha256/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}",
        original_filename="probe.pdf",
    )


class TestDocumentRepository:
    def test_records_a_document_and_its_first_version(
        self,
        rolled_back_session: Session,
    ) -> None:
        version = _record(rolled_back_session, f"{uuid4().hex}{uuid4().hex}")

        assert version.id is not None
        assert version.document_id is not None
        assert version.state == STATE_RECEIVED
        assert version.created_at is not None

    def test_identical_bytes_do_not_create_a_second_version(
        self,
        rolled_back_session: Session,
    ) -> None:
        """Re-upload is idempotent by construction (§11.5)."""
        content_hash = f"{uuid4().hex}{uuid4().hex}"

        first = _record(rolled_back_session, content_hash)
        second = _record(rolled_back_session, content_hash)

        assert first.id == second.id
        assert first.document_id == second.document_id

    def test_lookup_by_content_hash(self, rolled_back_session: Session) -> None:
        content_hash = f"{uuid4().hex}{uuid4().hex}"
        recorded = _record(rolled_back_session, content_hash)

        found = DocumentRepository(rolled_back_session).version_by_content_hash(
            hash_algorithm="sha256", content_hash=content_hash
        )

        assert found is not None
        assert found.id == recorded.id

    def test_lookup_of_unknown_bytes_returns_none(
        self,
        rolled_back_session: Session,
    ) -> None:
        found = DocumentRepository(rolled_back_session).version_by_content_hash(
            hash_algorithm="sha256", content_hash=f"{uuid4().hex}{uuid4().hex}"
        )

        assert found is None

    def test_the_database_rejects_a_duplicate_content_address(
        self,
        rolled_back_session: Session,
    ) -> None:
        """Idempotency rests on the constraint, not on a prior read."""
        content_hash = f"{uuid4().hex}{uuid4().hex}"
        recorded = _record(rolled_back_session, content_hash)

        duplicate = DocumentVersion(
            document_id=recorded.document_id,
            hash_algorithm="sha256",
            content_hash=content_hash,
            byte_size=1024,
            detected_content_type="application/pdf",
            object_key="originals/sha256/aa/bb/duplicate",
            state=STATE_RECEIVED,
        )
        rolled_back_session.add(duplicate)

        with pytest.raises(IntegrityError):
            rolled_back_session.flush()


METHOD = "pymupdf"
METHOD_VERSION = "0.0.0-test"
POLICY = "pdf-native"
CONFIG = "v1"

RECURSIVE_DOCUMENT_ORDER = """
    WITH RECURSIVE tree AS (
        SELECT id, element_type, text, ARRAY[ordinal] AS path
        FROM source_elements
        WHERE extraction_run_id = :run_id AND parent_id IS NULL
      UNION ALL
        SELECT child.id,
               child.element_type,
               child.text,
               tree.path || child.ordinal
        FROM source_elements AS child
        JOIN tree ON child.parent_id = tree.id
        WHERE child.extraction_run_id = :run_id
    )
    SELECT text FROM tree WHERE element_type = 'block' ORDER BY path
"""
"""Walks the element hierarchy depth-first, ordering siblings by ordinal.

The ordinal path is what reconstructs reading order across pages, and it works
for any depth, so a format with sections inside pages needs no new query.
"""


def _version(session: Session) -> DocumentVersion:
    """A fresh document version for an extraction run to attach to."""
    return _record(session, f"{uuid4().hex}{uuid4().hex}")


def _block(ordinal: int, text_value: str, *, top: float) -> ExtractedElement:
    return ExtractedElement(
        element_type=ElementType.BLOCK,
        ordinal=ordinal,
        locator="p. 1",
        location=BlockLocation(bbox=(72.0, top, 523.0, top + 12.0)),
        extraction_method=METHOD,
        extraction_method_version=METHOD_VERSION,
        text=text_value,
    )


def _page(ordinal: int, *blocks: ExtractedElement) -> ExtractedElement:
    return ExtractedElement(
        element_type=ElementType.PAGE,
        ordinal=ordinal,
        locator=f"p. {ordinal + 1}",
        location=PageLocation(
            page_number=ordinal + 1, width=595.0, height=842.0, rotation=0
        ),
        extraction_method=METHOD,
        extraction_method_version=METHOD_VERSION,
        children=blocks,
    )


def _two_page_document() -> tuple[ExtractedElement, ...]:
    return (
        _page(0, _block(0, "first", top=100.0), _block(1, "second", top=140.0)),
        _page(1, _block(0, "third", top=100.0), _block(1, "fourth", top=140.0)),
    )


def _start(
    session: Session,
    version: DocumentVersion,
    **overrides: str,
) -> ExtractionRun:
    settings = {"producer_policy": POLICY, "config_version": CONFIG}
    settings.update(overrides)
    return SourceRepository(session).start_run(
        document_version_id=version.id,
        format=FORMAT_PDF,
        **settings,
    )


class TestSourceRepository:
    def test_records_a_run_and_its_element_tree(
        self,
        rolled_back_session: Session,
    ) -> None:
        repository = SourceRepository(rolled_back_session)
        run = _start(rolled_back_session, _version(rolled_back_session))
        elements = _two_page_document()

        written = repository.record_elements(run_id=run.id, elements=elements)

        assert written == count_elements(elements) == 6

    def test_children_are_linked_to_the_parent_that_produced_them(
        self,
        rolled_back_session: Session,
    ) -> None:
        """The returned-id ordering is what makes this correct, not luck."""
        repository = SourceRepository(rolled_back_session)
        run = _start(rolled_back_session, _version(rolled_back_session))
        repository.record_elements(run_id=run.id, elements=_two_page_document())

        stored = repository.elements_for_run(run_id=run.id)
        pages = {e.id: e for e in stored if e.element_type == ElementType.PAGE.value}
        blocks = [e for e in stored if e.element_type == ElementType.BLOCK.value]

        assert len(pages) == 2
        assert all(stored_block.parent_id in pages for stored_block in blocks)
        page_two = sorted(
            b.text for b in blocks if pages[b.parent_id].locator == "p. 2"
        )
        assert page_two == ["fourth", "third"]

    def test_a_recursive_query_reconstructs_document_order(
        self,
        rolled_back_session: Session,
    ) -> None:
        """Reading order survives as data: pages by ordinal, blocks within them."""
        repository = SourceRepository(rolled_back_session)
        run = _start(rolled_back_session, _version(rolled_back_session))
        repository.record_elements(run_id=run.id, elements=_two_page_document())

        rows = rolled_back_session.execute(
            text(RECURSIVE_DOCUMENT_ORDER),
            {"run_id": run.id},
        ).scalars()

        assert list(rows) == ["first", "second", "third", "fourth"]

    @pytest.mark.parametrize(
        "original",
        [
            "₹1,23,456.78",
            "1\u00a0234,56",
            "ﬁnancial",
            "लाभ",
            "  spaced  ",
            "a\r\nb",
            "soft\u00adhyphen",
        ],
    )
    def test_text_survives_the_round_trip_unchanged(
        self,
        rolled_back_session: Session,
        original: str,
    ) -> None:
        """PostgreSQL must not be where a citation offset silently shifts."""
        repository = SourceRepository(rolled_back_session)
        run = _start(rolled_back_session, _version(rolled_back_session))
        repository.record_elements(
            run_id=run.id, elements=(_page(0, _block(0, original, top=100.0)),)
        )

        stored = [
            e
            for e in repository.elements_for_run(run_id=run.id)
            if e.element_type == ElementType.BLOCK.value
        ]

        assert stored[0].text == original
        assert stored[0].char_count == len(original)

    def test_locations_round_trip_through_jsonb(
        self,
        rolled_back_session: Session,
    ) -> None:
        repository = SourceRepository(rolled_back_session)
        run = _start(rolled_back_session, _version(rolled_back_session))
        repository.record_elements(
            run_id=run.id, elements=(_page(0, _block(0, "text", top=100.0)),)
        )

        rebuilt = [
            location_from_mapping(ElementType(e.element_type), e.location)
            for e in repository.elements_for_run(run_id=run.id)
        ]

        assert rebuilt[0] == PageLocation(
            page_number=1, width=595.0, height=842.0, rotation=0
        )
        assert rebuilt[1] == BlockLocation(bbox=(72.0, 100.0, 523.0, 112.0))

    def test_a_coverage_gap_is_recorded_as_a_row(
        self,
        rolled_back_session: Session,
    ) -> None:
        """A page that could not be read is a row, not a missing row (11.11)."""
        repository = SourceRepository(rolled_back_session)
        run = _start(rolled_back_session, _version(rolled_back_session))
        unreadable = ExtractedElement(
            element_type=ElementType.PAGE,
            ordinal=0,
            locator="p. 1",
            location=PageLocation(
                page_number=1, width=595.0, height=842.0, rotation=0
            ),
            extraction_method=METHOD,
            extraction_method_version=METHOD_VERSION,
            failure_reason="unreadable_page",
        )

        repository.record_elements(run_id=run.id, elements=(unreadable,))
        stored = repository.elements_for_run(run_id=run.id)

        assert stored[0].failure_reason == "unreadable_page"
        assert stored[0].text is None
        assert stored[0].char_count is None


class TestExtractionRunIdempotency:
    def test_a_second_run_for_the_same_configuration_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        """Idempotency rests on the partial unique index, not on a prior read."""
        repository = SourceRepository(rolled_back_session)
        version = _version(rolled_back_session)
        first = _start(rolled_back_session, version)
        repository.complete_run(
            run=first, state=ExtractionState.SUCCEEDED, element_count=0
        )

        second = _start(rolled_back_session, version)

        with pytest.raises(IntegrityError):
            repository.complete_run(
                run=second, state=ExtractionState.SUCCEEDED, element_count=0
            )

    def test_a_failed_run_does_not_block_a_retry(
        self,
        rolled_back_session: Session,
    ) -> None:
        """Failed rows stay out of the index so the diagnostic row can be kept."""
        repository = SourceRepository(rolled_back_session)
        version = _version(rolled_back_session)
        _start(rolled_back_session, version)

        retry = _start(rolled_back_session, version)
        repository.complete_run(
            run=retry, state=ExtractionState.SUCCEEDED, element_count=0
        )

        assert retry.state == ExtractionState.SUCCEEDED.value

    def test_a_new_configuration_version_is_a_new_run(
        self,
        rolled_back_session: Session,
    ) -> None:
        repository = SourceRepository(rolled_back_session)
        version = _version(rolled_back_session)
        first = _start(rolled_back_session, version)
        repository.complete_run(
            run=first, state=ExtractionState.SUCCEEDED, element_count=0
        )

        second = _start(rolled_back_session, version, config_version="v2")
        repository.complete_run(
            run=second, state=ExtractionState.PARTIAL, element_count=0
        )

        assert first.id != second.id

    def test_lookup_finds_the_run_for_a_configuration(
        self,
        rolled_back_session: Session,
    ) -> None:
        repository = SourceRepository(rolled_back_session)
        version = _version(rolled_back_session)
        run = _start(rolled_back_session, version)
        repository.complete_run(
            run=run, state=ExtractionState.SUCCEEDED, element_count=0
        )

        found = repository.run_for_configuration(
            document_version_id=version.id,
            producer_policy=POLICY,
            config_version=CONFIG,
        )

        assert found is not None
        assert found.id == run.id

    def test_lookup_ignores_a_failed_run(
        self,
        rolled_back_session: Session,
    ) -> None:
        repository = SourceRepository(rolled_back_session)
        version = _version(rolled_back_session)
        _start(rolled_back_session, version)

        found = repository.run_for_configuration(
            document_version_id=version.id,
            producer_policy=POLICY,
            config_version=CONFIG,
        )

        assert found is None


class TestCurrentExtractionRun:
    def test_a_new_version_points_at_no_run(
        self,
        rolled_back_session: Session,
    ) -> None:
        version = _version(rolled_back_session)

        assert version.current_extraction_run_id is None

    def test_setting_the_current_run_points_the_version_at_it(
        self,
        rolled_back_session: Session,
    ) -> None:
        repository = SourceRepository(rolled_back_session)
        version = _version(rolled_back_session)
        run = _start(rolled_back_session, version)
        repository.complete_run(
            run=run, state=ExtractionState.SUCCEEDED, element_count=0
        )

        repository.set_current_run(run=run)

        assert version.current_extraction_run_id == run.id
        current = repository.current_run(document_version_id=version.id)
        assert current is not None
        assert current.id == run.id

    def test_a_failed_run_cannot_become_the_current_run(
        self,
        rolled_back_session: Session,
    ) -> None:
        """Otherwise an empty element set would look like an empty document."""
        repository = SourceRepository(rolled_back_session)
        run = _start(rolled_back_session, _version(rolled_back_session))

        with pytest.raises(ValueError, match="failed extraction run"):
            repository.set_current_run(run=run)

    def test_a_partial_run_may_become_the_current_run(
        self,
        rolled_back_session: Session,
    ) -> None:
        """A run with recorded coverage gaps is still usable output (11.11)."""
        repository = SourceRepository(rolled_back_session)
        version = _version(rolled_back_session)
        run = _start(rolled_back_session, version)
        repository.complete_run(
            run=run, state=ExtractionState.PARTIAL, element_count=1
        )

        repository.set_current_run(run=run)

        assert version.current_extraction_run_id == run.id


class TestSourceElementConstraints:
    """The database repeats the domain's rules, because it is authoritative."""

    def _add(self, session: Session, **overrides: object) -> None:
        run = _start(session, _version(session))
        fields: dict[str, object] = {
            "extraction_run_id": run.id,
            "ordinal": 0,
            "element_type": ElementType.BLOCK.value,
            "extraction_method": METHOD,
            "extraction_method_version": METHOD_VERSION,
            "locator": "p. 1",
            "location": {"bbox": [0.0, 0.0, 10.0, 10.0]},
        }
        fields.update(overrides)
        session.add(SourceElement(**fields))
        session.flush()

    def test_a_char_count_that_disagrees_with_its_text_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        """A lying count would corrupt every offset-based citation built on it."""
        with pytest.raises(IntegrityError, match="char_count_matches_text"):
            self._add(rolled_back_session, text="four", char_count=99)

    def test_text_without_a_char_count_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        """A NULL count must fail the constraint, not slip past it as unknown."""
        with pytest.raises(IntegrityError, match="char_count_matches_text"):
            self._add(rolled_back_session, text="four", char_count=None)

    def test_a_char_count_without_text_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        """The mirror of the case above, and the same NULL trap."""
        with pytest.raises(IntegrityError, match="char_count_matches_text"):
            self._add(rolled_back_session, text=None, char_count=4)

    def test_text_together_with_a_failure_reason_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        with pytest.raises(IntegrityError, match="text_or_failure_reason"):
            self._add(
                rolled_back_session,
                text="four",
                char_count=4,
                failure_reason="unreadable",
            )

    def test_an_unknown_element_type_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        with pytest.raises(IntegrityError, match="element_type_known"):
            self._add(rolled_back_session, element_type="paragraph")

    def test_a_negative_ordinal_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        with pytest.raises(IntegrityError, match="ordinal_non_negative"):
            self._add(rolled_back_session, ordinal=-1)

    def test_a_location_that_is_not_an_object_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        with pytest.raises(IntegrityError, match="location_is_object"):
            self._add(rolled_back_session, location=[0.0, 0.0, 10.0, 10.0])


class TestExtractionRunConstraints:
    def test_an_unknown_state_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        run = _start(rolled_back_session, _version(rolled_back_session))
        run.state = "running"

        with pytest.raises(IntegrityError, match="state_known"):
            rolled_back_session.flush()

    def test_an_unknown_format_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        version = _version(rolled_back_session)
        rolled_back_session.add(
            ExtractionRun(
                document_version_id=version.id,
                format="docx",
                producer_policy=POLICY,
                config_version=CONFIG,
                state=ExtractionState.FAILED.value,
                element_count=0,
            )
        )

        with pytest.raises(IntegrityError, match="format_known"):
            rolled_back_session.flush()

    def test_completing_before_starting_is_refused(
        self,
        rolled_back_session: Session,
    ) -> None:
        run = _start(rolled_back_session, _version(rolled_back_session))
        run.completed_at = run.started_at - timedelta(seconds=1)

        with pytest.raises(IntegrityError, match="completed_after_started"):
            rolled_back_session.flush()
