"""Explicit personal learning memory.

Revision ID: 0018_memory_items
Revises: 0017_learning_engine
Create Date: 2026-10-26

Additive and backward compatible: the previous release ignores this user-owned table.
Downgrade physically deletes optional memories and is for disposable test databases only.
"""

from alembic import op

revision = "0018_memory_items"
down_revision = "0017_learning_engine"
branch_labels = None
depends_on = None

# One statement per op.execute: asyncpg prepares each one and refuses several at once.
UPGRADE = (
    """
    CREATE TABLE memory_items (
        id uuid DEFAULT gen_random_uuid() NOT NULL,
        user_id uuid NOT NULL,
        kind varchar(32) NOT NULL,
        content text NOT NULL,
        content_hash char(64) NOT NULL,
        status varchar(16) DEFAULT 'active' NOT NULL,
        version integer DEFAULT 1 NOT NULL,
        supersedes_id uuid,
        source_message_id uuid,
        expires_at timestamptz,
        embedding vector(1536) NOT NULL,
        embedding_model varchar(200) NOT NULL,
        created_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        updated_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        CONSTRAINT pk_memory_items PRIMARY KEY (id),
        CONSTRAINT fk_memory_items_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_items_supersedes_id_memory_items FOREIGN KEY(supersedes_id)
            REFERENCES memory_items (id) ON DELETE SET NULL,
        CONSTRAINT fk_memory_items_source_message_id_messages FOREIGN KEY(source_message_id)
            REFERENCES messages (id) ON DELETE SET NULL,
        CONSTRAINT ck_memory_items_kind CHECK (kind IN ('learning_context', 'study_context')),
        CONSTRAINT ck_memory_items_status CHECK (status IN ('active', 'superseded')),
        CONSTRAINT ck_memory_items_content_length CHECK (char_length(content) BETWEEN 1 AND 2000),
        CONSTRAINT ck_memory_items_version CHECK (version >= 1),
        CONSTRAINT ck_memory_items_expires_after_create CHECK (expires_at IS NULL OR expires_at > created_at),
        CONSTRAINT uq_memory_items_supersedes_id UNIQUE (supersedes_id)
    )
    """,
    """
    CREATE UNIQUE INDEX uq_memory_items_active_content
    ON memory_items (user_id, kind, content_hash) WHERE status = 'active'
    """,
    """
    CREATE INDEX ix_memory_items_user_status_updated
    ON memory_items (user_id, status, updated_at DESC, id)
    """,
    "CREATE INDEX ix_memory_items_user_kind_status ON memory_items (user_id, kind, status)",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE memory_items")
