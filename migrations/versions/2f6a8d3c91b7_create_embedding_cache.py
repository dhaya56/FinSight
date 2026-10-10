"""create embedding cache

Keeps a vector for every exact text already embedded, so a re-index recomputes only what
changed (§29.2). Measured need: indexing 4,867 child chunks took 40.7 minutes (ENV-011),
and re-extracting under a new configuration alters only 770 of them — 84.2% of the next
run would recompute vectors that already exist. A re-chunk renumbers every chunk, so the
key is the content and not the chunk id. Measured on 200 real chunks, a hit runs at 825
texts/s against 1.93 cold.

The table is a derived cache and holds no evidence: every row can be recomputed from the
text in ``chunks`` or from a question, and dropping it costs time only. It is therefore
the one table with no foreign key to anything.

Four constraints keep a hit indistinguishable from a fresh embedding:

  - uq ... input_digest          the identity is the text digest *plus* model, config
                                version and kind; the model applies different task
                                prefixes to documents and queries, so one string has two
                                correct vectors
  - kind_known                   the closed set, as text with a CHECK rather than a native
                                enum, because PostgreSQL enums cannot drop values
  - vector_not_empty             a zero-length vector would be accepted by the index and
                                 match nothing
  - dimensions_match_vector      a model that changed width fails here rather than at the
                                 vector store

``vector`` is ``double precision`` and not ``real``. The port returns Python floats and
the vector store receives them unchanged, so a float32 round trip would make a cached
vector differ from a fresh one in its last bits and could reorder two near-identical
candidates. 768 doubles is 6 KB a row, about 30 MB for this corpus.

``input_text`` is stored beside its digest and compared on read. A SHA-256 collision is
not a practical worry; a financial passage silently ranked as though it said something
else is, and the check costs one string comparison.

Revision ID: 2f6a8d3c91b7
Revises: 1a7c3e9f82b4
Create Date: 2026-10-10 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '2f6a8d3c91b7'
down_revision: str | None = '1a7c3e9f82b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'embedding_cache',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('input_digest', sa.LargeBinary(length=32), nullable=False),
        sa.Column('input_text', sa.Text(), nullable=False),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('config_version', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('vector', sa.ARRAY(sa.Double()), nullable=False),
        sa.Column('dimensions', sa.Integer(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'last_used_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('document', 'query')",
            name=op.f('ck_embedding_cache_kind_known'),
        ),
        sa.CheckConstraint(
            'cardinality(vector) > 0',
            name=op.f('ck_embedding_cache_vector_not_empty'),
        ),
        sa.CheckConstraint(
            'cardinality(vector) = dimensions',
            name=op.f('ck_embedding_cache_dimensions_match_vector'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_embedding_cache')),
        sa.UniqueConstraint(
            'input_digest',
            'model',
            'config_version',
            'kind',
            name=op.f('uq_embedding_cache_input_digest'),
        ),
    )
    op.create_index(
        op.f('ix_embedding_cache_last_used_at'),
        'embedding_cache',
        ['last_used_at'],
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_embedding_cache_last_used_at'), table_name='embedding_cache'
    )
    op.drop_table('embedding_cache')
