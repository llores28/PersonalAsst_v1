"""Add missing columns to repair_tickets and background_jobs.

Adds risk_level + auto_applied to repair_tickets (added to model but missed migration).
Creates background_jobs table if it doesn't exist.

Revision ID: 008_add_missing_columns
Revises: 007_governance_spend_ancestry
Create Date: 2026-04-14 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '008_add_missing_columns'
down_revision = '007_governance_spend_ancestry'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ── repair_tickets: add risk_level if missing ─────────────────────
    col_check = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='repair_tickets' AND column_name='risk_level'"
    )).fetchone()
    if not col_check:
        op.add_column('repair_tickets',
            sa.Column('risk_level', sa.String(length=10), nullable=False,
                      server_default='high'))

    # ── repair_tickets: add auto_applied if missing ───────────────────
    col_check2 = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='repair_tickets' AND column_name='auto_applied'"
    )).fetchone()
    if not col_check2:
        op.add_column('repair_tickets',
            sa.Column('auto_applied', sa.Boolean(), nullable=False,
                      server_default=sa.text('false')))

    # ── background_jobs: create if missing ───────────────────────────
    tbl_check = conn.execute(sa.text(
        "SELECT to_regclass('public.background_jobs')"
    )).scalar()
    if not tbl_check:
        op.create_table(
            'background_jobs',
            sa.Column('id', sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('goal', sa.Text(), nullable=False),
            sa.Column('done_condition', sa.Text(), nullable=True),
            sa.Column('check_interval_seconds', sa.Integer(), nullable=False, server_default='600'),
            sa.Column('status', sa.String(length=30), nullable=False, server_default='running'),
            sa.Column('iterations_run', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('max_iterations', sa.Integer(), nullable=False, server_default='10'),
            sa.Column('result', sa.Text(), nullable=True),
            sa.Column('apscheduler_id', sa.String(length=200), nullable=True, unique=True),
            sa.Column('created_at', sa.DateTime(timezone=True),
                      server_default=sa.text('now()'), nullable=False),
            sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index('ix_background_jobs_status', 'background_jobs', ['status'])
    else:
        # Table exists but may be missing columns — add them if absent
        for col_name, col_def in [
            ('check_interval_seconds', sa.Column('check_interval_seconds', sa.Integer(),
                                                  nullable=False, server_default='600')),
            ('apscheduler_id', sa.Column('apscheduler_id', sa.String(length=200), nullable=True)),
        ]:
            exists = conn.execute(sa.text(
                f"SELECT column_name FROM information_schema.columns "
                f"WHERE table_name='background_jobs' AND column_name='{col_name}'"
            )).fetchone()
            if not exists:
                op.add_column('background_jobs', col_def)


def downgrade():
    op.drop_column('repair_tickets', 'risk_level')
    op.drop_column('repair_tickets', 'auto_applied')
    op.drop_table('background_jobs')
