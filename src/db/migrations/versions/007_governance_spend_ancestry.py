"""Phase 2/3/4: governance approval gates, org budget cap, goal ancestry, org spend.

Creates:
  - approval_gates table (Phase 2: governance human-in-the-loop)
  - org_spend table (Phase 4: per-org cost tracking)

Alters:
  - organizations: adds budget_cap_usd column (Phase 2)
  - org_tasks: adds goal_ancestry JSONB column (Phase 3)

Revision ID: 007_governance_spend_ancestry
Revises: 006_user_settings
Create Date: 2026-04-13 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "007_governance_spend_ancestry"
down_revision = "006_user_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Phase 2: budget cap on organizations ──────────────────────────
    op.add_column(
        "organizations",
        sa.Column(
            "budget_cap_usd",
            sa.Numeric(10, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )

    # ── Phase 2: approval_gates ───────────────────────────────────────
    op.create_table(
        "approval_gates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("org_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("context", JSONB(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_approval_gates_org_id", "approval_gates", ["org_id"])
    op.create_index("ix_approval_gates_status", "approval_gates", ["status"])

    # ── Phase 3: goal_ancestry on org_tasks ───────────────────────────
    op.add_column(
        "org_tasks",
        sa.Column("goal_ancestry", JSONB(), nullable=True),
    )

    # ── Phase 4: org_spend ────────────────────────────────────────────
    op.create_table(
        "org_spend",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("org_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_org_spend_org_id", "org_spend", ["org_id"])
    op.create_index("ix_org_spend_recorded_at", "org_spend", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_org_spend_recorded_at", table_name="org_spend")
    op.drop_index("ix_org_spend_org_id", table_name="org_spend")
    op.drop_table("org_spend")

    op.drop_column("org_tasks", "goal_ancestry")

    op.drop_index("ix_approval_gates_status", table_name="approval_gates")
    op.drop_index("ix_approval_gates_org_id", table_name="approval_gates")
    op.drop_table("approval_gates")

    op.drop_column("organizations", "budget_cap_usd")
