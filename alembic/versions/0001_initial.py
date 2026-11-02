"""Зафиксированная начальная схема (v0.1).

Revision ID: 0001
Revises:
"""
from alembic import op
from app.initial_schema_v0001 import Base as InitialBase

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    InitialBase.metadata.create_all(bind=op.get_bind())

def downgrade():
    InitialBase.metadata.drop_all(bind=op.get_bind())
