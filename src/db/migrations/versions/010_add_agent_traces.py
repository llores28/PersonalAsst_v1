"""Add agent_traces table for per-tool-call trace logging.

Revision ID: 010_add_agent_traces
Revises: 009_add_tts_voice
Create Date: 2026-04-23 22:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '010_add_agent_traces'
down_revision = '009_add_tts_voice'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_traces',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('audit_log_id', sa.Integer(), sa.ForeignKey('audit_log.id'), nullable=True),
        sa.Column('session_key', sa.String(100), nullable=False),
        sa.Column('step_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('agent_name', sa.String(100), nullable=True),
        sa.Column('tool_name', sa.String(150), nullable=True),
        sa.Column('tool_args', postgresql.JSONB(), nullable=True),
        sa.Column('tool_result_preview', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_agent_traces_session_key', 'agent_traces', ['session_key'])


def downgrade():
    op.drop_index('ix_agent_traces_session_key', table_name='agent_traces')
    op.drop_table('agent_traces')
