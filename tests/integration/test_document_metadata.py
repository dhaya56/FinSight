"""Integration tests for the §20.2 filter values.

A retrieval that cannot constrain by issuer and period will happily answer a
question about one company with another company's filing. These values are what
make that impossible, so the tests are about two things: that the values survive a
round trip, and that the database refuses the ones that would be meaningless.

:meth:`TestVocabularies.test_the_check_matches_the_domain_enum` is the one worth
keeping. The manifest validator and the database CHECK are generated from the same
enum, and if they ever stop agreeing, corpus ingestion fails on a document the
manifest called valid.
"""

import datetime
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finsight.domain.documents import DocumentType, ReportingBasis
from finsight.persistence.database import dispose_engine, get_engine
from finsight.persistence.repositories.document_metadata import (
    DocumentMetadataRepository,
)
from finsight.persistence.repositories.documents import DocumentRepository
from finsight.persistence.tables.document_metadata import SOURCE_CORPUS_MANIFEST

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _release_pool() -> Iterator[None]:
    yield
    dispose_engine()


@pytest.fixture
def session() -> Iterator[Session]:
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
    return DocumentRepository(session).record_version(
        hash_algorithm="sha256",
        content_hash=content_hash,
        byte_size=1024,
        detected_content_type="application/pdf",
        object_key=f"originals/sha256/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}",
        original_filename="probe.pdf",
    ).id


def _record(session: Session, version_id: UUID, **overrides: object) -> None:
    values: dict[str, object] = {
        "document_version_id": version_id,
        "issuer_name": "Infosys Limited",
        "issuer_identifier": "L85110KA1981PLC013115",
        "document_type": DocumentType.ANNUAL_REPORT.value,
        "jurisdiction": "IN",
        "fiscal_period": "FY2024-25",
        "period_end": datetime.date(2025, 3, 31),
        "reporting_basis": ReportingBasis.BOTH.value,
        "currency": "INR",
        "units_as_presented": "INR crore",
        "source": SOURCE_CORPUS_MANIFEST,
    }
    values.update(overrides)
    DocumentMetadataRepository(session).record(**values)  # type: ignore[arg-type]


class TestRoundTrip:
    def test_the_filter_values_survive(self, session: Session) -> None:
        version_id = _version(session)
        _record(session, version_id)

        found = DocumentMetadataRepository(session).for_version(
            document_version_id=version_id
        )

        assert found is not None
        assert found.issuer_name == "Infosys Limited"
        assert found.fiscal_period == "FY2024-25"
        assert found.reporting_basis == ReportingBasis.BOTH.value

    def test_an_unstated_period_end_is_null_not_invented(
        self, session: Session
    ) -> None:
        """A nine-month stub period may have no clean end date to assert."""
        version_id = _version(session)
        _record(session, version_id, period_end=None)

        found = DocumentMetadataRepository(session).for_version(
            document_version_id=version_id
        )

        assert found is not None
        assert found.period_end is None

    def test_a_version_with_no_metadata_returns_none(
        self, session: Session
    ) -> None:
        """None means never established — not that the document has no issuer.

        The state of every document uploaded outside the corpus, since nothing
        extracts issuer or period from a filing yet.
        """
        version_id = _version(session)

        assert (
            DocumentMetadataRepository(session).for_version(
                document_version_id=version_id
            )
            is None
        )

    def test_recording_twice_replaces_rather_than_failing(
        self, session: Session
    ) -> None:
        """The corpus command is run repeatedly; a plain insert would fail."""
        version_id = _version(session)
        _record(session, version_id)
        _record(session, version_id, issuer_name="Infosys Ltd")

        found = DocumentMetadataRepository(session).for_version(
            document_version_id=version_id
        )

        assert found is not None
        assert found.issuer_name == "Infosys Ltd"

    def test_the_provenance_of_the_values_is_recorded(
        self, session: Session
    ) -> None:
        """These are a curator's claim, not something read from the filing.

        A consumer filtering on issuer deserves to know which, and the distinction
        must survive until metadata extraction exists to contrast with it.
        """
        version_id = _version(session)
        _record(session, version_id)

        found = DocumentMetadataRepository(session).for_version(
            document_version_id=version_id
        )

        assert found is not None
        assert found.source == SOURCE_CORPUS_MANIFEST


class TestVocabularies:
    def test_the_check_matches_the_domain_enum(self, session: Session) -> None:
        """Every filing class the manifest accepts must be storable.

        The corpus holds three of these today. The other two are reachable through
        the manifest validator, so a CHECK listing only what happens to be on disk
        would reject a valid document at ingest.
        """
        for kind in DocumentType:
            version_id = _version(session)
            _record(session, version_id, document_type=kind.value)
            session.flush()

    def test_every_reporting_basis_is_storable(self, session: Session) -> None:
        for basis in ReportingBasis:
            version_id = _version(session)
            _record(session, version_id, reporting_basis=basis.value)
            session.flush()

    def test_an_unknown_document_type_is_refused(self, session: Session) -> None:
        version_id = _version(session)
        with pytest.raises(IntegrityError, match="document_type_known"):
            _record(session, version_id, document_type="press_release")

    def test_an_unknown_reporting_basis_is_refused(self, session: Session) -> None:
        version_id = _version(session)
        with pytest.raises(IntegrityError, match="reporting_basis_known"):
            _record(session, version_id, reporting_basis="probably_consolidated")

    def test_an_unproducible_provenance_is_refused(self, session: Session) -> None:
        """Nothing extracts metadata from a filing, so nothing may claim it did."""
        version_id = _version(session)
        with pytest.raises(IntegrityError, match="source_known"):
            _record(session, version_id, source="extracted")

    def test_a_blank_issuer_is_refused(self, session: Session) -> None:
        """An empty issuer passes NOT NULL and makes the §20.2 filter useless."""
        version_id = _version(session)
        with pytest.raises(IntegrityError, match="issuer_name_not_blank"):
            _record(session, version_id, issuer_name="")
