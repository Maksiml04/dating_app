"""Source edit history, private media metadata, callback acknowledgements."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0010_source_edits_media_callbacks'
down_revision = '0009_worker_fencing'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('source_message', sa.Column('edited_at', sa.DateTime(timezone=True)))
    op.add_column('source_message', sa.Column('revision', sa.Integer(), nullable=False, server_default='1'))
    op.create_table('source_media',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('source_message_id', UUID(as_uuid=True), sa.ForeignKey('source_message.id'), unique=True, nullable=False),
        sa.Column('object_key', sa.Text(), unique=True, nullable=False),
        sa.Column('content_type', sa.String(80), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('telegram_photo_id', sa.BigInteger()),
        sa.Column('stored_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('source_message_revision',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('source_message_id', UUID(as_uuid=True), sa.ForeignKey('source_message.id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('edit_date', sa.DateTime(timezone=True)),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('source_message_id', 'revision'))
    op.create_index('ix_source_message_revision_source_message_id', 'source_message_revision', ['source_message_id'])
    op.create_table('telegram_callback_answer',
        sa.Column('callback_id', sa.String(120), primary_key=True),
        sa.Column('text', sa.String(200), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True)),
        sa.Column('last_error', sa.String(200)))


def downgrade():
    op.drop_table('telegram_callback_answer')
    op.drop_index('ix_source_message_revision_source_message_id', table_name='source_message_revision')
    op.drop_table('source_message_revision')
    op.drop_table('source_media')
    op.drop_column('source_message', 'revision')
    op.drop_column('source_message', 'edited_at')
