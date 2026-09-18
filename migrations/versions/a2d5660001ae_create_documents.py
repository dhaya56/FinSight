"""create documents

Creates the document identity tables. The uniqueness constraint on
(hash_algorithm, content_hash) is what makes re-uploading identical bytes
idempotent, and the surrogate uuidv7 keys keep foreign keys narrow for the
chunk, span, fact and citation tables that will reference them.

Revision ID: a2d5660001ae
Revises:
Create Date: 2026-09-17 18:08:58.655872+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a2d5660001ae'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('documents',
    sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents'))
    )
    op.create_table('document_versions',
    sa.Column('id', sa.Uuid(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('hash_algorithm', sa.String(length=32), nullable=False),
    sa.Column('content_hash', sa.String(length=128), nullable=False),
    sa.Column('byte_size', sa.BigInteger(), nullable=False),
    sa.Column('detected_content_type', sa.String(length=255), nullable=False),
    sa.Column('declared_content_type', sa.String(length=255), nullable=True),
    sa.Column('original_filename', sa.String(length=512), nullable=True),
    sa.Column('object_key', sa.String(length=512), nullable=False),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("state IN ('received')", name=op.f('ck_document_versions_state_known')),
    sa.CheckConstraint('byte_size > 0', name=op.f('ck_document_versions_byte_size_positive')),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_document_versions_document_id_documents')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_versions')),
    sa.UniqueConstraint('hash_algorithm', 'content_hash', name=op.f('uq_document_versions_hash_algorithm'))
    )
    op.create_index(op.f('ix_document_versions_document_id'), 'document_versions', ['document_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_document_versions_document_id'), table_name='document_versions')
    op.drop_table('document_versions')
    op.drop_table('documents')
