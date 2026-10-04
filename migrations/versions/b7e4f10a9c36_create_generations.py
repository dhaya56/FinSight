"""create generations

Adds the generation: the unit of reprocessing (§11.12) and the granularity §20.2
filters retrieval on. Nothing was queryable before this table, because nothing
could say which evidence was permitted to be seen.

The pointer on document_versions is deliberately separate from
current_extraction_run_id. That one records what a stage produced; this one records
what a reader may receive. §11.12 is explicit that a component run completing does
not make a generation active, and §11.13 that activation follows successful
indexing and validation.

Three CHECK constraints make the unsafe states unrepresentable rather than merely
discouraged:

  - state_known                  the four states, as text with a CHECK rather than
                                 a native enum, because PostgreSQL enums cannot
                                 drop values
  - activated_at_matches_state   an equivalence, so a superseded generation cannot
                                 keep an activation timestamp that reads as live
  - active_requires_components   a generation with no extraction run has no
                                 evidence behind it and must not be activatable

The partial unique index permits one active generation per document version and
any number of shadow, superseded or failed ones. It is enforced by the database
because two concurrent activations would both read "none active" and both proceed.

The foreign keys are mutually circular — generations references document_versions,
document_versions references generations — so the pointer is added with use_alter
and dropped before the table.

Revision ID: b7e4f10a9c36
Revises: a1c5e83f072d
Create Date: 2026-10-04 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7e4f10a9c36'
down_revision: str | None = 'a1c5e83f072d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'generations',
        sa.Column(
            'id',
            sa.Uuid(),
            server_default=sa.text('uuidv7()'),
            nullable=False,
        ),
        sa.Column('document_version_id', sa.Uuid(), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=False),
        sa.Column('extraction_run_id', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('shadow', 'active', 'superseded', 'failed')",
            name=op.f('ck_generations_state_known'),
        ),
        sa.CheckConstraint(
            "(state = 'active') = (activated_at IS NOT NULL)",
            name=op.f('ck_generations_activated_at_matches_state'),
        ),
        sa.CheckConstraint(
            "state <> 'active' OR extraction_run_id IS NOT NULL",
            name=op.f('ck_generations_active_requires_components'),
        ),
        sa.ForeignKeyConstraint(
            ['document_version_id'],
            ['document_versions.id'],
            name=op.f('fk_generations_document_version_id_document_versions'),
        ),
        sa.ForeignKeyConstraint(
            ['extraction_run_id'],
            ['extraction_runs.id'],
            name=op.f('fk_generations_extraction_run_id_extraction_runs'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_generations')),
    )
    op.create_index(
        op.f('ix_generations_document_version_id'),
        'generations',
        ['document_version_id'],
    )
    op.create_index(
        'uq_generations_document_version_id',
        'generations',
        ['document_version_id'],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.add_column(
        'document_versions',
        sa.Column('active_generation_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f('fk_document_versions_active_generation_id_generations'),
        'document_versions',
        'generations',
        ['active_generation_id'],
        ['id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('fk_document_versions_active_generation_id_generations'),
        'document_versions',
        type_='foreignkey',
    )
    op.drop_column('document_versions', 'active_generation_id')
    op.drop_index('uq_generations_document_version_id', table_name='generations')
    op.drop_index(
        op.f('ix_generations_document_version_id'), table_name='generations'
    )
    op.drop_table('generations')
