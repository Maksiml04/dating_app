"""Черновики карточек, отделённые от публикаций."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
revision='0007_catalog_drafts'
down_revision='0006_ingestion'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('catalog_draft',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('source_message_id', UUID(as_uuid=True), sa.ForeignKey('source_message.id'), unique=True, nullable=False),
        sa.Column('extracted', JSONB(), nullable=False),
        sa.Column('duplicate_candidates', JSONB(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('canonical_id', UUID(as_uuid=True), sa.ForeignKey('canonical.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
def downgrade():
    op.drop_table('catalog_draft')
