"""Chats: pin, archive and share (owner request 2026-11-26).

Revision ID: 0033_chat_pins_archive_shares
Revises: 0032_friend_zone_exam
Create Date: 2026-11-26

- conversations.pinned_at / archived_at: nullable, so every existing chat is neither, and the release before this
  one keeps working while it runs (it never reads them). A chat is never both (CHECK).
- conversation_shares: a shared link's read-only snapshot, found by the SHA-256 of its token (the token itself is
  never stored). One live link per chat (partial unique index); the chat's or the owner's deletion takes it.
Additive only: no row is rewritten, and the new indexes are on empty columns and a new table.
"""

from alembic import op

revision = "0033_chat_pins_archive_shares"
down_revision = "0032_friend_zone_exam"
branch_labels = None
depends_on = None

# One statement per op.execute: asyncpg prepares each one, and refuses several at once.
UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "ALTER TABLE conversations ADD COLUMN pinned_at timestamptz, ADD COLUMN archived_at timestamptz",
    "ALTER TABLE conversations ADD CONSTRAINT ck_conversations_pinned_not_archived "
    "CHECK (pinned_at IS NULL OR archived_at IS NULL)",
    "CREATE INDEX ix_conversations_user_pinned ON conversations (user_id, pinned_at DESC, id) "
    "WHERE pinned_at IS NOT NULL",
    "CREATE INDEX ix_conversations_user_archived ON conversations (user_id, archived_at DESC, id) "
    "WHERE archived_at IS NOT NULL",
    """
    CREATE TABLE conversation_shares (
        id uuid DEFAULT gen_random_uuid() NOT NULL,
        conversation_id uuid NOT NULL,
        user_id uuid NOT NULL,
        token_hash varchar(64) NOT NULL,
        snapshot jsonb NOT NULL,
        message_count integer NOT NULL,
        created_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        revoked_at timestamptz,
        CONSTRAINT pk_conversation_shares PRIMARY KEY (id),
        CONSTRAINT fk_conversation_shares_conversation_id_conversations
            FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
        CONSTRAINT fk_conversation_shares_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        CONSTRAINT uq_conversation_shares_token_hash UNIQUE (token_hash)
    )
    """,
    "CREATE UNIQUE INDEX uq_conversation_shares_live ON conversation_shares (conversation_id) WHERE revoked_at IS NULL",
    "CREATE INDEX ix_conversation_shares_user ON conversation_shares (user_id)",
)

DOWNGRADE = (
    "DROP TABLE conversation_shares",
    "DROP INDEX ix_conversations_user_archived",
    "DROP INDEX ix_conversations_user_pinned",
    "ALTER TABLE conversations DROP CONSTRAINT ck_conversations_pinned_not_archived",
    "ALTER TABLE conversations DROP COLUMN archived_at, DROP COLUMN pinned_at",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
