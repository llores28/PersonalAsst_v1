"""Add user_settings table for budget caps and user preferences.

Revision ID: 006_user_settings
Revises: 005_repair_tickets
Create Date: 2026-04-13 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '006_user_settings'
down_revision = '005_repair_tickets'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_settings',
        sa.Column('id', sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, unique=True),
        sa.Column('daily_cost_cap_usd', sa.Numeric(10, 2), nullable=False, server_default=sa.text('5.00')),
        sa.Column('monthly_cost_cap_usd', sa.Numeric(10, 2), nullable=False, server_default=sa.text('100.00')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_user_settings_user_id', 'user_settings', ['user_id'], unique=True)


def downgrade():
    op.drop_index('ix_user_settings_user_id', table_name='user_settings')
    op.drop_table('user_settings')
