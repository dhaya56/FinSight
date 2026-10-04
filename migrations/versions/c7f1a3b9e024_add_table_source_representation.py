"""add table source representation

Completes the §14.5 element model with tables and cells, and adds the two
one-to-one semantics extensions the blueprint names: source_tables for the
caption (§12.6) and source_table_cells for header paths, row-label paths and
footnote references (§17.2).

Widening element_type is a drop and recreate rather than an ALTER, because
PostgreSQL has no "alter check constraint" — which is the reason the schema uses
a CHECK over a native enum in the first place.

The downgrade will refuse to run once table or cell rows exist, since restoring
the narrower CHECK would contradict stored data. That is the correct outcome: the
way back from this migration is to delete those elements first, deliberately.

Revision ID: c7f1a3b9e024
Revises: 5ebe436c4357
Create Date: 2026-09-30 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c7f1a3b9e024'
down_revision: str | None = '5ebe436c4357'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f('ck_source_elements_element_type_known'),
        'source_elements',
        type_='check',
    )
    op.create_check_constraint(
        op.f('ck_source_elements_element_type_known'),
        'source_elements',
        "element_type IN ('page', 'block', 'table', 'cell')",
    )
    op.create_table('source_tables',
    sa.Column('source_element_id', sa.Uuid(), nullable=False),
    sa.Column('caption', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['source_element_id'], ['source_elements.id'], name=op.f('fk_source_tables_source_element_id_source_elements')),
    sa.PrimaryKeyConstraint('source_element_id', name=op.f('pk_source_tables'))
    )
    op.create_table('source_table_cells',
    sa.Column('source_element_id', sa.Uuid(), nullable=False),
    sa.Column('header_path', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('row_label_path', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('footnote_refs', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    sa.Column('is_header', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint('array_position(footnote_refs, NULL) IS NULL', name=op.f('ck_source_table_cells_footnote_refs_has_no_nulls')),
    sa.CheckConstraint('array_position(header_path, NULL) IS NULL', name=op.f('ck_source_table_cells_header_path_has_no_nulls')),
    sa.CheckConstraint('array_position(row_label_path, NULL) IS NULL', name=op.f('ck_source_table_cells_row_label_path_has_no_nulls')),
    sa.ForeignKeyConstraint(['source_element_id'], ['source_elements.id'], name=op.f('fk_source_table_cells_source_element_id_source_elements')),
    sa.PrimaryKeyConstraint('source_element_id', name=op.f('pk_source_table_cells'))
    )


def downgrade() -> None:
    op.drop_table('source_table_cells')
    op.drop_table('source_tables')
    op.drop_constraint(
        op.f('ck_source_elements_element_type_known'),
        'source_elements',
        type_='check',
    )
    op.create_check_constraint(
        op.f('ck_source_elements_element_type_known'),
        'source_elements',
        "element_type IN ('page', 'block')",
    )
