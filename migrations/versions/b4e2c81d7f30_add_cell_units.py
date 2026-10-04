"""add cell units

§17.3 requires "table-level and column-level" unit context attached to values. The
previous migration recorded units as a table attribute, which cannot express a
scale note governing a single column — and a table mixing crore figures with
percentages is the ordinary case in a financial statement, not an exotic one.

Nullable because a table may carry no declaration at all. NULL means none was
found, never that the figures are unscaled.

Revision ID: b4e2c81d7f30
Revises: c7f1a3b9e024
Create Date: 2026-10-02 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b4e2c81d7f30'
down_revision: str | None = 'c7f1a3b9e024'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'source_table_cells', sa.Column('units', sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('source_table_cells', 'units')
