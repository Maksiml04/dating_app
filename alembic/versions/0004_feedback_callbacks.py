"""Уникальность отзывов по callback_id.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('feedback',sa.Column('callback_id',sa.String(120),nullable=True))
    op.create_unique_constraint('uq_feedback_callback_id','feedback',['callback_id'])

def downgrade():
    op.drop_constraint('uq_feedback_callback_id','feedback',type_='unique')
    op.drop_column('feedback','callback_id')
