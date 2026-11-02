"""Очередь перепроверки актуальности публикаций.

Revision ID: 0008_verification_tasks
Revises: 0007_catalog_drafts
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
revision = '0008_verification_tasks'
down_revision = '0007_catalog_drafts'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('verification_task',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('publication_id', UUID(as_uuid=True), sa.ForeignKey('publication_version.id'), nullable=False, unique=True),
        sa.Column('reason', sa.String(24), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True)),
        sa.Column('reviewed_by', sa.String(100)),
        sa.Column('notes', sa.Text()),
        sa.CheckConstraint("reason IN ('expired', 'due')", name='verification_reason'),
        sa.CheckConstraint("status IN ('pending','verified','needs_update','unavailable')", name='verification_status'),
    )
    op.create_index('ix_verification_pending', 'verification_task', ['status', 'created_at'])

def downgrade():
    op.drop_index('ix_verification_pending', table_name='verification_task')
    op.drop_table('verification_task')
