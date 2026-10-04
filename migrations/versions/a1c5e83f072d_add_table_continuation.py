"""add table continuation

Records the title of the statement a table continues, taken from the document's
own "(continued)" heading (§12.8).

A title rather than a foreign key to the earlier table. The document *states* what
is continued; which stored region holds the earlier part is an inference across
regions measured as frequently mis-bounded, and a foreign key would present that
inference as provenance.

Nullable, and NULL means no heading was found — never that the table stands alone.
One development filing declares continuations 91 times and another declares none
at all, so absence is a property of the publisher's house style rather than of the
table.

Revision ID: a1c5e83f072d
Revises: f4b8d26c7e13
Create Date: 2026-10-04 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a1c5e83f072d'
down_revision: str | None = 'f4b8d26c7e13'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'source_tables',
        sa.Column('continuation_of', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('source_tables', 'continuation_of')
