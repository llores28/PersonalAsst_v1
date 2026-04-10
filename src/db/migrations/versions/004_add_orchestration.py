"""Replace PaperClip orchestration with Atlas Dashboard organization tables.

Drops old companies/agents/tasks tables, creates new organizations,
org_agents, org_tasks, org_activity tables.

Revision ID: 004_orchestration
Revises: 003
Create Date: 2026-04-01 11:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '004_orchestration'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade():
    # Drop old PaperClip tables if they exist (safe for fresh installs too)
    op.execute("DROP TABLE IF EXISTS tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS agents CASCADE")
    op.execute("DROP TABLE IF EXISTS companies CASCADE")

    # ── Organizations ─────────────────────────────────────────────────
    op.create_table('organizations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('goal', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), server_default='active', nullable=False),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('config', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_organizations_status', 'organizations', ['status'])

    # ── Org Agents ────────────────────────────────────────────────────
    op.create_table('org_agents',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('role', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('instructions', sa.Text(), nullable=True),
        sa.Column('tools_config', JSONB(), nullable=True),
        sa.Column('model_tier', sa.String(50), server_default='general', nullable=False),
        sa.Column('status', sa.String(20), server_default='active', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_org_agents_org_id', 'org_agents', ['org_id'])

    # ── Org Tasks ─────────────────────────────────────────────────────
    op.create_table('org_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('priority', sa.String(20), server_default='medium', nullable=False),
        sa.Column('status', sa.String(20), server_default='pending', nullable=False),
        sa.Column('result', JSONB(), nullable=True),
        sa.Column('source', sa.String(20), server_default='dashboard', nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['org_agents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_org_tasks_org_id', 'org_tasks', ['org_id'])
    op.create_index('ix_org_tasks_status', 'org_tasks', ['status'])

    # ── Org Activity ──────────────────────────────────────────────────
    op.create_table('org_activity',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=True),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('source', sa.String(20), server_default='system', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['org_agents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['task_id'], ['org_tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_org_activity_org_id', 'org_activity', ['org_id'])


def downgrade():
    op.drop_table('org_activity')
    op.drop_table('org_tasks')
    op.drop_table('org_agents')
    op.drop_table('organizations')
