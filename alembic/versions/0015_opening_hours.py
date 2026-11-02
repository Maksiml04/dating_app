"""FR-05/06: verified opening hours and date-specific closures."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0015_opening_hours'
down_revision='0014_source_health'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('publication_version',sa.Column('opening_hours',JSONB()))
    op.add_column('publication_version',sa.Column('calendar_exceptions',JSONB()))

def downgrade():
    op.drop_column('publication_version','calendar_exceptions')
    op.drop_column('publication_version','opening_hours')
