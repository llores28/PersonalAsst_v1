"""Add repair_tickets.debug_analysis JSONB + repair_tickets queue index.

The `debug_analysis` column was added to the ORM model (see RepairTicket in
src/db/models.py) without a paired migration. Result: every read via
`select(RepairTicket)` raised UndefinedColumnError, the dashboard's
/api/repairs endpoint silently swallowed it and returned `[]`, and the
Repairs tab appeared empty even when 3 tickets existed.

Defensive guard mirrors 008_add_missing_columns.py — only adds the column
if it's not already present, so re-running on a hand-patched DB is a no-op.

Also adds (status, created_at) composite index so the new repair-queue
worker (src/scheduler/maintenance.py:process_repair_queue) can do the
SKIP LOCKED claim cheaply: PG can satisfy the
``WHERE status IN (...) ORDER BY created_at`` predicate from the index
without touching the heap.

Revision ID: 011_add_repair_debug_analysis
Revises: 010_add_agent_traces
Create Date: 2026-04-30 14:45:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '011_add_repair_debug_analysis'
down_revision = '010_add_agent_traces'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    col_exists = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='repair_tickets' AND column_name='debug_analysis'"
    )).fetchone()
    if not col_exists:
        op.add_column(
            'repair_tickets',
            sa.Column('debug_analysis', JSONB(), nullable=True),
        )

    idx_exists = conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname='ix_repair_tickets_status_created_at'"
    )).fetchone()
    if not idx_exists:
        op.create_index(
            'ix_repair_tickets_status_created_at',
            'repair_tickets',
            ['status', 'created_at'],
        )


def downgrade():
    op.drop_index('ix_repair_tickets_status_created_at', table_name='repair_tickets')
    op.drop_column('repair_tickets', 'debug_analysis')
