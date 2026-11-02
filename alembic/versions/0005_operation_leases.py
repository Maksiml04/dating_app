"""Аренда фоновых операций и восстановление после падения."""
from alembic import op
import sqlalchemy as sa
revision='0005_operation_leases'
down_revision='0004'
branch_labels=None
depends_on=None
def upgrade():
    op.add_column('request_operation',sa.Column('claimed_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('request_operation',sa.Column('attempts',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('request_operation',sa.Column('last_error',sa.Text(),nullable=True))
    op.create_index('ix_request_operation_lease','request_operation',['status','claimed_at'])
def downgrade():
    op.drop_index('ix_request_operation_lease',table_name='request_operation')
    op.drop_column('request_operation','last_error')
    op.drop_column('request_operation','attempts')
    op.drop_column('request_operation','claimed_at')
