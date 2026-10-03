"""add cell is_total

Marks a cell whose row announces itself as an aggregate — "Total", "Sub-total",
"Aggregate" — so a consumer summing a column can avoid double counting the rows
beneath such a row.

NOT NULL with a false default, because the column is a claim about a row that was
read, and every existing row was read without the rule. False therefore means "not
identified as a total", which is also what it means for a freshly extracted
non-total row. The two are deliberately indistinguishable: the rule under-reads by
design, so false never asserted "this is a line item" even where it was applied.

Revision ID: e3a7c91d5b28
Revises: d8c4b2f16a05
Create Date: 2026-10-04 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e3a7c91d5b28'
down_revision: str | None = 'd8c4b2f16a05'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'source_table_cells',
        sa.Column(
            'is_total',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('source_table_cells', 'is_total')
