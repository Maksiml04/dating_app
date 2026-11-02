"""FR-12: source success/error diagnostics."""
from alembic import op
import sqlalchemy as sa
revision='0014_source_health'
down_revision='0013_event_parent'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('telegram_source',sa.Column('last_success_at',sa.DateTime(timezone=True)))
    op.add_column('telegram_source',sa.Column('last_error',sa.Text()))

def downgrade():
    op.drop_column('telegram_source','last_error')
    op.drop_column('telegram_source','last_success_at')
