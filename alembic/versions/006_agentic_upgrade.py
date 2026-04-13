"""Agentic upgrade: agent_traces, background_jobs, repair_ticket risk columns.

Creates:
  - agent_traces table (M3: explainable observability)
  - background_jobs table (M2: autonomous background jobs)

Alters:
  - repair_tickets: adds risk_level, auto_applied columns (M4: self-healing)

Revision ID: 006_agentic_upgrade
Revises: 005_skill_marketplace
Create Date: 2026-04-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "006_agentic_upgrade"
down_revision = "005_skill_marketplace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── agent_traces ─────────────────────────────────────────────────
    op.create_table(
        "agent_traces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("audit_log_id", sa.Integer(), sa.ForeignKey("audit_log.id"), nullable=True),
        sa.Column("session_key", sa.String(100), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("agent_name", sa.String(100), nullable=True),
        sa.Column("tool_name", sa.String(150), nullable=True),
        sa.Column("tool_args", JSONB(), nullable=True),
        sa.Column("tool_result_preview", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_agent_traces_session_key", "agent_traces", ["session_key"])

    # ── background_jobs ───────────────────────────────────────────────
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("done_condition", sa.Text(), nullable=True),
        sa.Column("check_interval_seconds", sa.Integer(), server_default="600", nullable=False),
        sa.Column("max_iterations", sa.Integer(), server_default="48", nullable=False),
        sa.Column("iterations_run", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(20), server_default="running", nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("apscheduler_id", sa.String(200), unique=True, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── repair_tickets: new columns ───────────────────────────────────
    op.add_column(
        "repair_tickets",
        sa.Column("risk_level", sa.String(10), server_default="high", nullable=False),
    )
    op.add_column(
        "repair_tickets",
        sa.Column("auto_applied", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("repair_tickets", "auto_applied")
    op.drop_column("repair_tickets", "risk_level")
    op.drop_table("background_jobs")
    op.drop_index("ix_agent_traces_session_key", table_name="agent_traces")
    op.drop_table("agent_traces")
