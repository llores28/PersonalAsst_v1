"""Add tts_voice column to user_settings.

Revision ID: 009_add_tts_voice
Revises: 008_add_missing_columns
Create Date: 2026-04-22 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '009_add_tts_voice'
down_revision = '008_add_missing_columns'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'user_settings',
        sa.Column('tts_voice', sa.String(20), server_default='alloy', nullable=False),
    )


def downgrade():
    op.drop_column('user_settings', 'tts_voice')
