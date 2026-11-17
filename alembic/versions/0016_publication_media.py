"""FR-22: привязка фото исходного поста к версии публикации."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision='0016_publication_media'
down_revision='0015_opening_hours'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('publication_version',
        sa.Column('source_message_id', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_publication_source_message', 'publication_version',
        'source_message', ['source_message_id'], ['id'])
    # created_at нужен для сводки /liked по уже существующим строкам feedback.
    op.add_column('feedback', sa.Column('created_at', sa.DateTime(timezone=True),
        nullable=False, server_default=sa.func.now()))

def downgrade():
    op.drop_column('feedback', 'created_at')
    op.drop_constraint('fk_publication_source_message', 'publication_version', type_='foreignkey')
    op.drop_column('publication_version', 'source_message_id')
