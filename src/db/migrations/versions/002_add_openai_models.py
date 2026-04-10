"""Add openai_models and owner_security_config tables

Revision ID: 002
Revises: 001
Create Date: 2026-03-19 14:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── openai_models table ────────────────────────────────────────────
    op.create_table(
        "openai_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=150), nullable=False),
        sa.Column("family", sa.String(length=50), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "strengths",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "use_cases",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "reasoning_effort_levels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "pricing_input",
            sa.Numeric(precision=10, scale=4),
            nullable=True,
        ),
        sa.Column(
            "pricing_cached_input",
            sa.Numeric(precision=10, scale=4),
            nullable=True,
        ),
        sa.Column(
            "pricing_output",
            sa.Numeric(precision=10, scale=4),
            nullable=True,
        ),
        sa.Column(
            "pricing_unit",
            sa.String(length=30),
            nullable=False,
            server_default="per_1m_tokens",
        ),
        sa.Column("pricing_notes", sa.Text(), nullable=True),
        sa.Column("api_docs_url", sa.String(length=500), nullable=True),
        sa.Column("api_endpoint", sa.String(length=200), nullable=True),
        sa.Column(
            "is_deprecated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("atlas_role", sa.String(length=50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id"),
    )

    # ── owner_security_config table ────────────────────────────────────
    op.create_table(
        "owner_security_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pin_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "security_qa",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "challenge_ttl",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Seed openai_models with catalog data ───────────────────────────
    from src.models.seed import OPENAI_MODELS

    models_table = sa.table(
        "openai_models",
        sa.column("model_id", sa.String),
        sa.column("display_name", sa.String),
        sa.column("family", sa.String),
        sa.column("category", sa.String),
        sa.column("description", sa.Text),
        sa.column("strengths", postgresql.JSONB),
        sa.column("use_cases", postgresql.JSONB),
        sa.column("context_window", sa.Integer),
        sa.column("max_output_tokens", sa.Integer),
        sa.column("reasoning_effort_levels", postgresql.JSONB),
        sa.column("pricing_input", sa.Numeric),
        sa.column("pricing_cached_input", sa.Numeric),
        sa.column("pricing_output", sa.Numeric),
        sa.column("pricing_unit", sa.String),
        sa.column("pricing_notes", sa.Text),
        sa.column("api_docs_url", sa.String),
        sa.column("api_endpoint", sa.String),
        sa.column("is_deprecated", sa.Boolean),
        sa.column("is_enabled", sa.Boolean),
        sa.column("atlas_role", sa.String),
        sa.column("notes", sa.Text),
    )

    rows = []
    for m in OPENAI_MODELS:
        rows.append(
            {
                "model_id": m["model_id"],
                "display_name": m["display_name"],
                "family": m["family"],
                "category": m["category"],
                "description": m["description"],
                "strengths": m.get("strengths"),
                "use_cases": m.get("use_cases"),
                "context_window": m.get("context_window"),
                "max_output_tokens": m.get("max_output_tokens"),
                "reasoning_effort_levels": m.get("reasoning_effort_levels"),
                "pricing_input": m.get("pricing_input"),
                "pricing_cached_input": m.get("pricing_cached_input"),
                "pricing_output": m.get("pricing_output"),
                "pricing_unit": m.get("pricing_unit", "per_1m_tokens"),
                "pricing_notes": m.get("pricing_notes"),
                "api_docs_url": m.get("api_docs_url"),
                "api_endpoint": m.get("api_endpoint"),
                "is_deprecated": m.get("is_deprecated", False),
                "is_enabled": m.get("is_enabled", False),
                "atlas_role": m.get("atlas_role"),
                "notes": m.get("notes"),
            }
        )

    op.bulk_insert(models_table, rows)


def downgrade() -> None:
    op.drop_table("owner_security_config")
    op.drop_table("openai_models")
