"""Раздел 9 спеки: продуктовая аналитика. NFR-08: хранение событий 90 дней."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision='0017_analytics'
down_revision='0016_publication_media'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('analytics_event',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(60), nullable=False),
        sa.Column('user_hash', sa.String(64)),
        sa.Column('request_id', sa.String(64)),
        sa.Column('environment', sa.String(20), nullable=False, server_default='production'),
        sa.Column('is_test', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('schema_version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('payload', JSONB(), nullable=False, server_default='{}'),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_analytics_name_time', 'analytics_event', ['name', 'occurred_at'])

def downgrade():
    op.drop_index('ix_analytics_name_time', table_name='analytics_event')
    op.drop_table('analytics_event')
