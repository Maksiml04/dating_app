"""FR-19: link each event to its host place for inherited exclusions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
revision='0013_event_parent'
down_revision='0012_booking_url'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('canonical', sa.Column('parent_place_id',UUID(as_uuid=True),sa.ForeignKey('canonical.id')))

def downgrade():
    op.drop_column('canonical','parent_place_id')
