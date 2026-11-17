"""FR-08: кнопки ослабления условия; FR-12: backoff источников Telegram."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision='0018_relax_and_source_backoff'
down_revision='0017_analytics'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('telegram_outbox', sa.Column('reply_markup', JSONB()))
    op.add_column('telegram_source',
        sa.Column('consecutive_errors', sa.Integer, nullable=False, server_default='0'))
    op.add_column('telegram_source', sa.Column('next_attempt_at', sa.DateTime(timezone=True)))

def downgrade():
    op.drop_column('telegram_source', 'next_attempt_at')
    op.drop_column('telegram_source', 'consecutive_errors')
    op.drop_column('telegram_outbox', 'reply_markup')
