"""create answers

Records what was answered and why anything was withheld (§27.11, §31). Before this table a
refusal left no trace, which makes "why did it decline that question" unanswerable and a
withheld claim indistinguishable from a model that never spoke.

Six CHECK constraints restate the Evidence Gate's own decision rules, so a row contradicting
them cannot be written:

  - decision_known / support_band_known      the closed sets, as text with a CHECK rather than
                                             a native enum, because PostgreSQL enums cannot
                                             drop values
  - abstention_releases_nothing              an equivalence: abstained if and only if nothing
                                             was released
  - band_none_matches_abstention             the same equivalence for the support band, so a
                                             band cannot imply content that is not there
  - answered_withholds_nothing               an answered answer removed no claim
  - partial_withholds_something              a partial answer removed at least one

And on the child table, a released claim must cite at least one source element — §27.3 removes
a claim that rests on nothing, so a released row with no provenance is unreachable by design.

Citations are stored as source element ids rather than passage numbers. A passage number labels
one answer's evidence set, which is not persisted here, so the number would have no referent; a
source element id resolves to a document region for as long as the document exists (§14.7).

Revision ID: 1a7c3e9f82b4
Revises: 09e6b5dcbabf
Create Date: 2026-10-07 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '1a7c3e9f82b4'
down_revision: str | None = '09e6b5dcbabf'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'answers',
        sa.Column(
            'id',
            sa.Uuid(),
            server_default=sa.text('uuidv7()'),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('decision', sa.String(length=16), nullable=False),
        sa.Column('support_band', sa.String(length=16), nullable=False),
        sa.Column(
            'reason_codes',
            sa.ARRAY(sa.String(length=64)),
            server_default='{}',
            nullable=False,
        ),
        sa.Column(
            'degraded',
            sa.ARRAY(sa.String(length=64)),
            server_default='{}',
            nullable=False,
        ),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('released_claims', sa.Integer(), nullable=False),
        sa.Column('withheld_claims', sa.Integer(), nullable=False),
        sa.Column('evidence_passages', sa.Integer(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False),
        sa.Column('completion_tokens', sa.Integer(), nullable=False),
        sa.Column('elapsed_ms', sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('answered', 'partial', 'abstained')",
            name=op.f('ck_answers_decision_known'),
        ),
        sa.CheckConstraint(
            "support_band IN ('strong', 'moderate', 'weak', 'none')",
            name=op.f('ck_answers_support_band_known'),
        ),
        sa.CheckConstraint(
            "(decision = 'abstained') = (released_claims = 0)",
            name=op.f('ck_answers_abstention_releases_nothing'),
        ),
        sa.CheckConstraint(
            "(support_band = 'none') = (released_claims = 0)",
            name=op.f('ck_answers_band_none_matches_abstention'),
        ),
        sa.CheckConstraint(
            "decision <> 'answered' OR withheld_claims = 0",
            name=op.f('ck_answers_answered_withholds_nothing'),
        ),
        sa.CheckConstraint(
            "decision <> 'partial' OR withheld_claims > 0",
            name=op.f('ck_answers_partial_withholds_something'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_answers')),
    )
    op.create_index(op.f('ix_answers_created_at'), 'answers', ['created_at'])

    op.create_table(
        'answer_claims',
        sa.Column(
            'id',
            sa.Uuid(),
            server_default=sa.text('uuidv7()'),
            nullable=False,
        ),
        sa.Column('answer_id', sa.Uuid(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('released', sa.Boolean(), nullable=False),
        sa.Column(
            'reason_codes',
            sa.ARRAY(sa.String(length=64)),
            server_default='{}',
            nullable=False,
        ),
        sa.Column(
            'cited_source_element_ids',
            sa.ARRAY(sa.Uuid()),
            server_default='{}',
            nullable=False,
        ),
        sa.CheckConstraint(
            'NOT released OR cardinality(cited_source_element_ids) > 0',
            name=op.f('ck_answer_claims_released_claim_cites_something'),
        ),
        sa.ForeignKeyConstraint(
            ['answer_id'],
            ['answers.id'],
            name=op.f('fk_answer_claims_answer_id_answers'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_answer_claims')),
        sa.UniqueConstraint(
            'answer_id',
            'position',
            name=op.f('uq_answer_claims_answer_id_position'),
        ),
    )
    op.create_index(
        op.f('ix_answer_claims_answer_id'), 'answer_claims', ['answer_id']
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_answer_claims_answer_id'), table_name='answer_claims')
    op.drop_table('answer_claims')
    op.drop_index(op.f('ix_answers_created_at'), table_name='answers')
    op.drop_table('answers')
