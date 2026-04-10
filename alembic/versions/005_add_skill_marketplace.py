"""Add skill marketplace tables for Phase 1+ skill system.

Creates marketplace_skills and installed_skills tables to support
filesystem-based skills with marketplace discovery and lifecycle management.

Revision ID: 005_skill_marketplace
Revises: 004
Create Date: 2025-01-20 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = '005_skill_marketplace'
down_revision = '004_orchestration'
branch_labels = None
depends_on = None


def upgrade():
    # ── Marketplace Skills ──────────────────────────────────────────
    op.create_table(
        'marketplace_skills',
        sa.Column('id', sa.String(100), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('author', sa.String(100), nullable=False),
        sa.Column('version', sa.String(20), server_default='1.0.0', nullable=False),
        sa.Column('tags', JSONB(), server_default='[]', nullable=False),
        sa.Column('git_url', sa.String(500), nullable=True),
        sa.Column('filesystem_path', sa.String(500), nullable=True),
        sa.Column('skill_group', sa.String(50), server_default='user', nullable=False),
        sa.Column('is_knowledge_only', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('requires_connection', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('install_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rating_avg', sa.Numeric(3, 2), nullable=True),
        sa.Column('rating_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_official', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_verified', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', 'version'),
    )
    op.create_index('ix_marketplace_skills_author', 'marketplace_skills', ['author'])
    op.create_index('ix_marketplace_skills_tags', 'marketplace_skills', ['tags'], postgresql_using='gin')
    op.create_index('ix_marketplace_skills_official', 'marketplace_skills', ['is_official'])

    # ── Installed Skills ─────────────────────────────────────────────
    op.create_table(
        'installed_skills',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('marketplace_skill_id', sa.String(100), nullable=False),
        sa.Column('version_installed', sa.String(20), nullable=False),
        sa.Column('filesystem_path', sa.String(500), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('config', JSONB(), server_default='{}', nullable=False),
        sa.Column('installed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_installed_skills_user_id', 'installed_skills', ['user_id'])
    op.create_index('ix_installed_skills_marketplace_id', 'installed_skills', ['marketplace_skill_id'])
    op.create_index('ix_installed_skills_active', 'installed_skills', ['user_id', 'is_active'])


def downgrade():
    op.drop_index('ix_installed_skills_active', table_name='installed_skills')
    op.drop_index('ix_installed_skills_marketplace_id', table_name='installed_skills')
    op.drop_index('ix_installed_skills_user_id', table_name='installed_skills')
    op.drop_table('installed_skills')

    op.drop_index('ix_marketplace_skills_official', table_name='marketplace_skills')
    op.drop_index('ix_marketplace_skills_tags', table_name='marketplace_skills')
    op.drop_index('ix_marketplace_skills_author', table_name='marketplace_skills')
    op.drop_table('marketplace_skills')
