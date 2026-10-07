"""Recording and reading what a document is, within a caller-owned transaction."""

import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import InstrumentedAttribute, Session

from finsight.persistence.tables.document_metadata import DocumentMetadata


class DocumentMetadataRepository:
    """The §20.2 filter values for a document version. Opens no transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        document_version_id: UUID,
        issuer_name: str,
        document_type: str,
        fiscal_period: str,
        reporting_basis: str,
        source: str,
        issuer_identifier: str | None = None,
        jurisdiction: str | None = None,
        period_end: datetime.date | None = None,
        currency: str | None = None,
        units_as_presented: str | None = None,
    ) -> None:
        """Record metadata, replacing any already held for this version.

        Idempotent by upsert rather than by a prior read, matching
        ``DocumentRepository.record_version``. Re-ingesting the same corpus entry
        is a normal operation — the corpus command is run repeatedly — and a plain
        insert would fail on the primary key the second time.

        Replacing rather than ignoring on conflict, because a corrected manifest
        entry should take effect. The values are a curator's claim, not evidence,
        so there is nothing here that a correction would destroy.
        """
        statement = insert(DocumentMetadata).values(
            document_version_id=document_version_id,
            issuer_name=issuer_name,
            issuer_identifier=issuer_identifier,
            document_type=document_type,
            jurisdiction=jurisdiction,
            fiscal_period=fiscal_period,
            period_end=period_end,
            reporting_basis=reporting_basis,
            currency=currency,
            units_as_presented=units_as_presented,
            source=source,
        )
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[DocumentMetadata.document_version_id],
                set_={
                    "issuer_name": statement.excluded.issuer_name,
                    "issuer_identifier": statement.excluded.issuer_identifier,
                    "document_type": statement.excluded.document_type,
                    "jurisdiction": statement.excluded.jurisdiction,
                    "fiscal_period": statement.excluded.fiscal_period,
                    "period_end": statement.excluded.period_end,
                    "reporting_basis": statement.excluded.reporting_basis,
                    "currency": statement.excluded.currency,
                    "units_as_presented": statement.excluded.units_as_presented,
                    "source": statement.excluded.source,
                },
            )
        )

    def for_version(self, *, document_version_id: UUID) -> DocumentMetadata | None:
        """What is known about this version, or None when nothing was established.

        None is a real answer and means metadata was never recorded — a document
        uploaded outside the corpus, for which §20.2's issuer and period filters
        have nothing to match. It does not mean the document has no issuer.
        """
        return self._session.execute(
            select(DocumentMetadata).where(
                DocumentMetadata.document_version_id == document_version_id
            )
        ).scalar_one_or_none()

    def filter_values(self) -> dict[str, list[str]]:
        """The distinct values each §20.2 filter can usefully take, for a chooser.

        **A filter a reader has to guess is not a usable filter.** The issuer field was a
        free-text box, so "Infosys" matched nothing while "Infosys Limited" matched — a
        silent empty result that reads as "the corpus has nothing" rather than "that is not
        how the name is spelled". These are the values that actually exist, so a chooser can
        offer them.

        Drawn from recorded metadata rather than from a configured list: a value that no
        document carries would narrow a search to nothing, which is the same trap in a
        different place.

        Sorted, because a chooser's order is part of its usability and the database's
        physical order is not an order.
        """
        return {
            name: self._distinct(column)
            for name, column in (
                ("issuer_name", DocumentMetadata.issuer_name),
                ("document_type", DocumentMetadata.document_type),
                ("fiscal_period", DocumentMetadata.fiscal_period),
                ("reporting_basis", DocumentMetadata.reporting_basis),
            )
        }

    def _distinct(self, column: InstrumentedAttribute[str]) -> list[str]:
        """Every value present, once, in order."""
        return [
            value
            for value in self._session.execute(
                select(column).distinct().where(column.is_not(None)).order_by(column)
            )
            .scalars()
            .all()
            if value
        ]
