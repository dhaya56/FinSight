"""create source representation

Creates the citable source representation: extraction runs and the format-neutral
element hierarchy they produce. Chunks, citations, facts and the Evidence Gate
all resolve to source_elements.id, so this is the foreign-key target the rest of
the system is built on.

The partial unique index on (document_version_id, producer_policy,
config_version) excludes failed runs, so re-extraction is idempotent in the
database while a failed attempt stays retryable. document_versions gains a
nullable pointer to the current extraction run; the foreign key is created with
use_alter because the two tables reference each other.

Revision ID: 5ebe436c4357
Revises: a2d5660001ae
Create Date: 2026-09-20 16:13:41.688163+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '5ebe436c4357'
down_revision: str | None = 'a2d5660001ae'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('extraction_runs',
    sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('document_version_id', sa.Uuid(), nullable=False),
    sa.Column('format', sa.String(length=32), nullable=False),
    sa.Column('producer_policy', sa.String(length=64), nullable=False),
    sa.Column('config_version', sa.String(length=64), nullable=False),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('element_count', sa.BigInteger(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("format IN ('pdf')", name=op.f('ck_extraction_runs_format_known')),
    sa.CheckConstraint("state IN ('succeeded', 'partial', 'failed')", name=op.f('ck_extraction_runs_state_known')),
    sa.CheckConstraint('completed_at IS NULL OR completed_at >= started_at', name=op.f('ck_extraction_runs_completed_after_started')),
    sa.CheckConstraint('element_count >= 0', name=op.f('ck_extraction_runs_element_count_non_negative')),
    sa.ForeignKeyConstraint(['document_version_id'], ['document_versions.id'], name=op.f('fk_extraction_runs_document_version_id_document_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_extraction_runs'))
    )
    op.create_index(op.f('ix_extraction_runs_document_version_id'), 'extraction_runs', ['document_version_id'], unique=False)
    op.create_index('uq_extraction_runs_document_version_id', 'extraction_runs', ['document_version_id', 'producer_policy', 'config_version'], unique=True, postgresql_where=sa.text("state <> 'failed'"))
    op.create_table('source_elements',
    sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('extraction_run_id', sa.Uuid(), nullable=False),
    sa.Column('parent_id', sa.Uuid(), nullable=True),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('element_type', sa.String(length=32), nullable=False),
    sa.Column('extraction_method', sa.String(length=64), nullable=False),
    sa.Column('extraction_method_version', sa.String(length=64), nullable=False),
    sa.Column('locator', sa.String(length=255), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('char_count', sa.Integer(), nullable=True),
    sa.Column('failure_reason', sa.String(length=64), nullable=True),
    sa.Column('location', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("element_type IN ('page', 'block')", name=op.f('ck_source_elements_element_type_known')),
    sa.CheckConstraint("jsonb_typeof(location) = 'object'", name=op.f('ck_source_elements_location_is_object')),
    sa.CheckConstraint('(text IS NULL AND char_count IS NULL) OR (text IS NOT NULL AND char_count IS NOT NULL AND char_count = char_length(text))', name=op.f('ck_source_elements_char_count_matches_text')),
    sa.CheckConstraint('ordinal >= 0', name=op.f('ck_source_elements_ordinal_non_negative')),
    sa.CheckConstraint('text IS NULL OR failure_reason IS NULL', name=op.f('ck_source_elements_text_or_failure_reason')),
    sa.ForeignKeyConstraint(['extraction_run_id'], ['extraction_runs.id'], name=op.f('fk_source_elements_extraction_run_id_extraction_runs')),
    sa.ForeignKeyConstraint(['parent_id'], ['source_elements.id'], name=op.f('fk_source_elements_parent_id_source_elements')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_elements'))
    )
    op.create_index('ix_source_elements_extraction_run_id', 'source_elements', ['extraction_run_id', 'parent_id', 'ordinal'], unique=False)
    op.add_column('document_versions', sa.Column('current_extraction_run_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_document_versions_current_extraction_run_id_extraction_runs'), 'document_versions', 'extraction_runs', ['current_extraction_run_id'], ['id'], use_alter=True)


def downgrade() -> None:
    op.drop_constraint(op.f('fk_document_versions_current_extraction_run_id_extraction_runs'), 'document_versions', type_='foreignkey')
    op.drop_column('document_versions', 'current_extraction_run_id')
    op.drop_index('ix_source_elements_extraction_run_id', table_name='source_elements')
    op.drop_table('source_elements')
    op.drop_index('uq_extraction_runs_document_version_id', table_name='extraction_runs', postgresql_where=sa.text("state <> 'failed'"))
    op.drop_index(op.f('ix_extraction_runs_document_version_id'), table_name='extraction_runs')
    op.drop_table('extraction_runs')
