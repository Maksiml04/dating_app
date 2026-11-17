"""UI-полировка: parse_mode для HTML-карточек."""
from alembic import op
import sqlalchemy as sa

revision='0019_outbox_parse_mode'
down_revision='0018_relax_and_source_backoff'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('telegram_outbox', sa.Column('parse_mode', sa.String(16)))

def downgrade():
    op.drop_column('telegram_outbox', 'parse_mode')
