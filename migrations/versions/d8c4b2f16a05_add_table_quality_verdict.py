"""add table quality verdict

Gives ``source_tables`` the quality verdict a detected table now carries: whether
it may be used as evidence, the machine-readable grounds, and the signals the
judgement rested on.

The columns are nullable or defaulted because tables extracted before this
migration were never assessed, and an unassessed table is not the same as a clean
one. Backfilling ``accepted`` would assert a judgement nobody made about rows
nothing has looked at; NULL says what is true, which is that the question was not
asked. Re-extraction under a new config version is how those rows gain a verdict.

``verdict_reasons`` is NOT NULL with an empty-array default instead, because an
accepted table genuinely has no reasons and NULL would make "no grounds" and "not
asked" indistinguishable — the distinction ``verdict`` itself already carries.

Revision ID: d8c4b2f16a05
Revises: b4e2c81d7f30
Create Date: 2026-10-03 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd8c4b2f16a05'
down_revision: str | None = 'b4e2c81d7f30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'source_tables',
        sa.Column('verdict', sa.String(length=32), nullable=True),
    )
    op.add_column(
        'source_tables',
        sa.Column(
            'verdict_reasons',
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )
    op.add_column(
        'source_tables',
        sa.Column('quality_signals', postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        op.f('ck_source_tables_verdict_known'),
        'source_tables',
        "verdict IS NULL OR verdict IN ('accepted', 'review_required', 'rejected')",
    )
    op.create_check_constraint(
        op.f('ck_source_tables_verdict_reasons_has_no_nulls'),
        'source_tables',
        'array_position(verdict_reasons, NULL) IS NULL',
    )
    op.create_check_constraint(
        op.f('ck_source_tables_verdict_reasons_need_a_verdict'),
        'source_tables',
        'verdict IS NOT NULL OR cardinality(verdict_reasons) = 0',
    )
    op.create_check_constraint(
        op.f('ck_source_tables_quality_signals_is_object'),
        'source_tables',
        "quality_signals IS NULL OR jsonb_typeof(quality_signals) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f('ck_source_tables_quality_signals_is_object'),
        'source_tables',
        type_='check',
    )
    op.drop_constraint(
        op.f('ck_source_tables_verdict_reasons_need_a_verdict'),
        'source_tables',
        type_='check',
    )
    op.drop_constraint(
        op.f('ck_source_tables_verdict_reasons_has_no_nulls'),
        'source_tables',
        type_='check',
    )
    op.drop_constraint(
        op.f('ck_source_tables_verdict_known'), 'source_tables', type_='check'
    )
    op.drop_column('source_tables', 'quality_signals')
    op.drop_column('source_tables', 'verdict_reasons')
    op.drop_column('source_tables', 'verdict')
