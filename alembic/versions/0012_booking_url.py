"""FR-10/11 separate verified booking URL."""
from alembic import op
import sqlalchemy as sa
revision='0012_booking_url'
down_revision='0011_user_exclusions_callbacks'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('publication_version',sa.Column('booking_url',sa.Text()))

def downgrade():
    op.drop_column('publication_version','booking_url')
