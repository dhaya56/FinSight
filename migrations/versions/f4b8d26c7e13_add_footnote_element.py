"""add footnote element

Widens the element_type CHECK to admit 'footnote', completing §14.5's element
model with the type that lets a marker resolve to its text. Until now a cell's
footnote_refs stored a marker and pointed at nothing, which tells a reader a
qualification exists and withholds it.

A drop and recreate rather than an ALTER, because PostgreSQL has no "alter check
constraint" — the same reason the schema uses a CHECK over a native enum.

The downgrade refuses to run once footnote rows exist. Restoring the narrower
CHECK would contradict stored data, and the way back is to delete those elements
first, deliberately.

Revision ID: f4b8d26c7e13
Revises: e3a7c91d5b28
Create Date: 2026-10-04 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f4b8d26c7e13'
down_revision: str | None = 'e3a7c91d5b28'
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
        "element_type IN ('page', 'block', 'table', 'cell', 'footnote')",
    )


def downgrade() -> None:
    remaining = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM source_elements WHERE element_type = 'footnote'"
            )
        )
        .scalar_one()
    )
    if remaining:
        raise RuntimeError(
            f"{remaining} footnote element(s) exist; the narrower CHECK would "
            "contradict stored data. Delete them deliberately before downgrading."
        )

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
