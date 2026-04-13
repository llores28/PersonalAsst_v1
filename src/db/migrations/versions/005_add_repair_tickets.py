"""Add repair_tickets table for durable repair ticketing workflow.

Revision ID: 005_repair_tickets
Revises: 004_orchestration
Create Date: 2026-04-12 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '005_repair_tickets'
down_revision = '004_orchestration'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'repair_tickets',
        sa.Column('id', sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False, server_default='telegram'),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='open'),
        sa.Column('priority', sa.String(length=10), nullable=False, server_default='medium'),
        sa.Column('error_context', JSONB(), nullable=True),
        sa.Column('plan', JSONB(), nullable=True),
        sa.Column('branch_name', sa.String(length=120), nullable=True),
        sa.Column('verification_results', JSONB(), nullable=True),
        sa.Column('approval_required', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('approved_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deployed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_repair_tickets_status', 'repair_tickets', ['status'])
    op.create_index('ix_repair_tickets_user_id', 'repair_tickets', ['user_id'])


def downgrade():
    op.drop_index('ix_repair_tickets_status', table_name='repair_tickets')
    op.drop_index('ix_repair_tickets_user_id', table_name='repair_tickets')
    op.drop_table('repair_tickets')
