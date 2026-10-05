"""create document metadata

Records issuer, document type, fiscal period, reporting basis and currency for a
document version — the values §20.2 requires every retrieval to filter on, and
which until now existed only in data/corpus/manifest.toml.

That mattered more than it looks. The manifest is development-corpus governance,
not application state: filtering from it would have worked for the three corpus
filings and returned unfiltered results for anything uploaded, with nothing to
signal the difference.

A one-to-one extension keyed to the *version*, not the document. A reissued filing
is new bytes and a new version whose period or basis may differ, and keying to the
document would let the later one overwrite the earlier while §27.7 still requires
citations against the earlier to resolve.

The document_type and reporting_basis CHECK lists are generated from the domain
enums, so the manifest validator and the database cannot disagree about which
filing classes exist. The source column has exactly one permitted value because
exactly one route produces metadata today; metadata read from the filing itself
does not exist, and listing it before it does would let a row claim a provenance
nothing can supply.

Revision ID: c9d2a8b45e71
Revises: b7e4f10a9c36
Create Date: 2026-10-04 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c9d2a8b45e71'
down_revision: str | None = 'b7e4f10a9c36'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'document_metadata',
        sa.Column('document_version_id', sa.Uuid(), nullable=False),
        sa.Column('issuer_name', sa.Text(), nullable=False),
        sa.Column('issuer_identifier', sa.Text(), nullable=True),
        sa.Column('document_type', sa.String(length=32), nullable=False),
        sa.Column('jurisdiction', sa.String(length=8), nullable=True),
        sa.Column('fiscal_period', sa.Text(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=True),
        sa.Column('reporting_basis', sa.String(length=32), nullable=False),
        sa.Column('currency', sa.String(length=8), nullable=True),
        sa.Column('units_as_presented', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "document_type IN ('annual_report', 'drhp', 'rhp',"
            " 'quarterly_result', 'form_10k')",
            name=op.f('ck_document_metadata_document_type_known'),
        ),
        sa.CheckConstraint(
            "reporting_basis IN ('consolidated', 'standalone', 'both',"
            " 'undetermined')",
            name=op.f('ck_document_metadata_reporting_basis_known'),
        ),
        sa.CheckConstraint(
            "source IN ('corpus_manifest')",
            name=op.f('ck_document_metadata_source_known'),
        ),
        sa.CheckConstraint(
            'length(issuer_name) > 0',
            name=op.f('ck_document_metadata_issuer_name_not_blank'),
        ),
        sa.ForeignKeyConstraint(
            ['document_version_id'],
            ['document_versions.id'],
            name=op.f(
                'fk_document_metadata_document_version_id_document_versions'
            ),
        ),
        sa.PrimaryKeyConstraint(
            'document_version_id', name=op.f('pk_document_metadata')
        ),
    )
    op.create_index(
        op.f('ix_document_metadata_issuer_name'),
        'document_metadata',
        ['issuer_name'],
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_document_metadata_issuer_name'), table_name='document_metadata'
    )
    op.drop_table('document_metadata')
