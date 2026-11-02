"""FR-18/19/20/21: permanent exclusions and per-delivery opaque callbacks."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
revision = '0011_user_exclusions_callbacks'
down_revision = '0010_source_edits_media_callbacks'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('user_exclusion',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('canonical_id', UUID(as_uuid=True), sa.ForeignKey('canonical.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('chat_id','canonical_id',name='uq_exclusion_chat_canonical'))
    op.create_index('ix_user_exclusion_chat_id','user_exclusion',['chat_id'])
    op.create_table('feedback_action',
        sa.Column('token', sa.String(36), primary_key=True),
        sa.Column('outbox_id', UUID(as_uuid=True), sa.ForeignKey('telegram_outbox.id'), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('publication_id', UUID(as_uuid=True), sa.ForeignKey('publication_version.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('outbox_id','publication_id',name='uq_callback_outbox_publication'))
    op.create_index('ix_feedback_action_outbox_id','feedback_action',['outbox_id'])

def downgrade():
    op.drop_index('ix_feedback_action_outbox_id',table_name='feedback_action')
    op.drop_table('feedback_action')
    op.drop_index('ix_user_exclusion_chat_id',table_name='user_exclusion')
    op.drop_table('user_exclusion')
