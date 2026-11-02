"""Хранилище векторов публикаций, 384 измерения.
Revision ID: 0003
Revises: 0002
"""
from alembic import op
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.execute("""CREATE TABLE publication_embedding (
        publication_id uuid PRIMARY KEY REFERENCES publication_version(id) ON DELETE CASCADE,
        model_name varchar(150) NOT NULL,
        embedding vector(384) NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute('CREATE INDEX ix_pub_embedding_hnsw ON publication_embedding USING hnsw (embedding vector_cosine_ops)')

def downgrade():
    op.execute('DROP TABLE IF EXISTS publication_embedding')
