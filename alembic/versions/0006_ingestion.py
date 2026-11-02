"""Источники Telegram и очередь ручной модерации."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
revision='0006_ingestion'
down_revision='0005_operation_leases'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('telegram_source',sa.Column('id',UUID(as_uuid=True),primary_key=True),sa.Column('channel_id',sa.BigInteger(),nullable=False,unique=True),sa.Column('username',sa.String(150)),sa.Column('enabled',sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column('last_message_id',sa.BigInteger(),nullable=False,server_default='0'),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('source_message',sa.Column('id',UUID(as_uuid=True),primary_key=True),sa.Column('source_id',UUID(as_uuid=True),sa.ForeignKey('telegram_source.id'),nullable=False),sa.Column('message_id',sa.BigInteger(),nullable=False),sa.Column('message_date',sa.DateTime(timezone=True),nullable=False),sa.Column('text',sa.Text(),nullable=False),sa.Column('source_url',sa.Text()),sa.Column('status',sa.String(24),nullable=False,server_default='pending_review'),sa.Column('content_hash',sa.String(64),nullable=False),sa.Column('received_at',sa.DateTime(timezone=True),nullable=False),sa.Column('reviewed_at',sa.DateTime(timezone=True)),sa.Column('reviewed_by',sa.String(100)),sa.UniqueConstraint('source_id','message_id',name='uq_source_message'))
    op.create_index('ix_source_message_review','source_message',['status','received_at'])
def downgrade():
    op.drop_index('ix_source_message_review',table_name='source_message')
    op.drop_table('source_message')
    op.drop_table('telegram_source')
