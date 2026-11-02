"""Токены владения задачами: защита от устаревших исполнителей."""
from alembic import op
import sqlalchemy as sa

revision = '0009_worker_fencing'
down_revision = '0008_verification_tasks'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('request_operation', sa.Column('claim_token', sa.String(36), nullable=True))
    op.add_column('telegram_outbox', sa.Column('reserved_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('telegram_outbox', sa.Column('reservation_token', sa.String(36), nullable=True))
    op.create_index('ix_outbox_reservation', 'telegram_outbox', ['status', 'reserved_at'])
    # Устаревшие строки предыдущих версий не имеют owner token. Не доставляем
    # автоматически то, что могло уже уйти в Telegram до перезапуска.
    op.execute("UPDATE telegram_outbox SET status='ambiguous', last_error='Неизвестный статус после обновления' WHERE status='sending'")
    op.execute("UPDATE telegram_outbox SET status='pending' WHERE status='reserved'")
    op.execute("UPDATE request_operation SET status='queued', claimed_at=NULL WHERE status='processing'")

def downgrade():
    op.drop_index('ix_outbox_reservation', table_name='telegram_outbox')
    op.drop_column('telegram_outbox', 'reservation_token')
    op.drop_column('telegram_outbox', 'reserved_at')
    op.drop_column('request_operation', 'claim_token')
