"""Индекс полнотекстового поиска для русского языка.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""CREATE INDEX IF NOT EXISTS ix_publication_search_ru
      ON publication_version USING GIN
      (to_tsvector('russian', coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || coalesce(category, '')))""")

def downgrade():
    op.execute('DROP INDEX IF EXISTS ix_publication_search_ru')
