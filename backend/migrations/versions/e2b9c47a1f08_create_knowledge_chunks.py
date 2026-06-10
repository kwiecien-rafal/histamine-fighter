"""create knowledge chunks

Revision ID: e2b9c47a1f08
Revises: a7c2e9f4d130
Create Date: 2026-06-09 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2b9c47a1f08"
down_revision: str | Sequence[str] | None = "a7c2e9f4d130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Matches app.embeddings.EMBEDDING_DIM at creation time. The stored dimension is
# frozen into the column, so a model change to a different dimension is a new
# migration, not an edit to this one.
_EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        sa.UniqueConstraint(
            "slug", "chunk_index", name="uq_knowledge_chunks_slug_chunk_index"
        ),
    )
    # No ANN index. At a small corpus an exact cosine scan is both faster and
    # exact; add HNSW/IVFFlat only past ~10k chunks.


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    # The vector extension is left installed in case other objects rely on it.
