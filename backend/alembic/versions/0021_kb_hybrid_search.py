"""Add PostgreSQL full-text search support for knowledge-base chunks.

Revision ID: 0021_kb_hybrid_search
Revises: 0020_engineering_profiles
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_kb_hybrid_search"
down_revision = "0020_engineering_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_document_chunks_content_tsv_gin",
        "document_chunks",
        ["content_tsv"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_content_tsv_gin", table_name="document_chunks")
    op.drop_column("document_chunks", "content_tsv")
