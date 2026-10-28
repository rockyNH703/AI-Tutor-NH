"""Memory proposals, jobs, deletion markers, provenance, and compatibility metadata.

Revision ID: 0019_memory_foundation
Revises: 0018_memory_items
Create Date: 2026-10-27

The downgrade preflight refuses before DDL when any new data cannot be represented by 0018.
"""

from alembic import op

revision = "0019_memory_foundation"
down_revision = "0018_memory_items"
branch_labels = None
depends_on = None

UPGRADE = (
    "ALTER TABLE conversations ADD CONSTRAINT uq_conversations_id_user UNIQUE (id, user_id)",
    "ALTER TABLE messages ADD CONSTRAINT uq_messages_id_conversation UNIQUE (id, conversation_id)",
    "ALTER TABLE memory_items ADD CONSTRAINT uq_memory_items_id_user UNIQUE (id, user_id)",
    "ALTER TABLE memory_items ADD COLUMN origin varchar(32) DEFAULT 'explicit' NOT NULL",
    "ALTER TABLE memory_items ADD COLUMN proposal_id uuid",
    "ALTER TABLE memory_items ADD COLUMN last_confirmed_at timestamptz DEFAULT clock_timestamp() NOT NULL",
    "ALTER TABLE memory_items ADD COLUMN valid_from timestamptz",
    "ALTER TABLE memory_items ADD COLUMN valid_to timestamptz",
    "ALTER TABLE memory_items ADD COLUMN superseded_at timestamptz",
    "ALTER TABLE memory_items ADD COLUMN processor_version varchar(64)",
    "ALTER TABLE memory_items ADD COLUMN embedding_dimensions integer DEFAULT 1536 NOT NULL",
    "ALTER TABLE memory_items ADD COLUMN embedding_version varchar(32) DEFAULT 'v1' NOT NULL",
    "ALTER TABLE memory_items ADD CONSTRAINT ck_memory_items_origin CHECK (origin IN ('explicit', 'explicit_chat', 'approved_extraction'))",
    "ALTER TABLE memory_items ADD CONSTRAINT ck_memory_items_embedding_dimensions CHECK (embedding_dimensions > 0)",
    "ALTER TABLE memory_items ADD CONSTRAINT ck_memory_items_embedding_version CHECK (char_length(embedding_version) BETWEEN 1 AND 32)",
    "ALTER TABLE memory_items ADD CONSTRAINT ck_memory_items_valid_range CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)",
    "CREATE INDEX ix_memory_items_user_embedding_compat ON memory_items (user_id, status, embedding_model, embedding_version)",
    """
    CREATE TABLE memory_proposals (
        id uuid DEFAULT gen_random_uuid() NOT NULL,
        user_id uuid NOT NULL,
        conversation_id uuid NOT NULL,
        source_message_id uuid NOT NULL,
        assistant_message_id uuid NOT NULL,
        operation varchar(16) NOT NULL,
        target_memory_id uuid,
        target_memory_version integer,
        kind varchar(32) NOT NULL,
        content text NOT NULL,
        content_hash char(64) NOT NULL,
        status varchar(16) DEFAULT 'pending' NOT NULL,
        version integer DEFAULT 1 NOT NULL,
        confidence double precision NOT NULL,
        evidence_reason varchar(500) NOT NULL,
        source_hash char(64) NOT NULL,
        source_start integer,
        source_end integer,
        observed_at timestamptz NOT NULL,
        extractor_provider varchar(64) NOT NULL,
        extractor_model varchar(200) NOT NULL,
        prompt_version varchar(64) NOT NULL,
        schema_version varchar(64) NOT NULL,
        processor_version varchar(64) NOT NULL,
        idempotency_key char(64) NOT NULL,
        consent_epoch integer NOT NULL,
        expires_at timestamptz,
        valid_from timestamptz,
        valid_to timestamptz,
        applied_memory_id uuid,
        reviewed_at timestamptz,
        applied_at timestamptz,
        created_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        updated_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        CONSTRAINT pk_memory_proposals PRIMARY KEY (id),
        CONSTRAINT fk_memory_proposals_user FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_proposals_conversation_owner FOREIGN KEY(conversation_id, user_id)
            REFERENCES conversations (id, user_id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_proposals_source_conversation FOREIGN KEY(source_message_id, conversation_id)
            REFERENCES messages (id, conversation_id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_proposals_assistant_conversation FOREIGN KEY(assistant_message_id, conversation_id)
            REFERENCES messages (id, conversation_id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_proposals_target_owner FOREIGN KEY(target_memory_id, user_id)
            REFERENCES memory_items (id, user_id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_proposals_applied_owner FOREIGN KEY(applied_memory_id, user_id)
            REFERENCES memory_items (id, user_id) ON DELETE CASCADE,
        CONSTRAINT uq_memory_proposals_user_idempotency UNIQUE (user_id, idempotency_key),
        CONSTRAINT ck_memory_proposals_operation CHECK (operation IN ('add', 'supersede')),
        CONSTRAINT ck_memory_proposals_status CHECK (status IN ('pending', 'applied', 'rejected', 'invalidated', 'failed')),
        CONSTRAINT ck_memory_proposals_kind CHECK (kind IN ('learning_context', 'study_context')),
        CONSTRAINT ck_memory_proposals_content_length CHECK (char_length(content) BETWEEN 1 AND 2000),
        CONSTRAINT ck_memory_proposals_version CHECK (version >= 1),
        CONSTRAINT ck_memory_proposals_confidence CHECK (confidence >= 0 AND confidence <= 1),
        CONSTRAINT ck_memory_proposals_source_start CHECK (source_start IS NULL OR source_start >= 0),
        CONSTRAINT ck_memory_proposals_source_end CHECK (source_end IS NULL OR source_end >= source_start),
        CONSTRAINT ck_memory_proposals_operation_target CHECK (
            (operation = 'add' AND target_memory_id IS NULL AND target_memory_version IS NULL)
            OR (operation = 'supersede' AND target_memory_id IS NOT NULL AND target_memory_version IS NOT NULL)
        ),
        CONSTRAINT ck_memory_proposals_applied_state CHECK (
            (status = 'applied' AND applied_memory_id IS NOT NULL AND reviewed_at IS NOT NULL AND applied_at IS NOT NULL)
            OR (status = 'rejected' AND applied_memory_id IS NULL AND reviewed_at IS NOT NULL AND applied_at IS NULL)
            OR (status NOT IN ('applied', 'rejected') AND applied_memory_id IS NULL AND applied_at IS NULL)
        ),
        CONSTRAINT ck_memory_proposals_valid_range CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
    )
    """,
    "CREATE INDEX ix_memory_proposals_user_status_created ON memory_proposals (user_id, status, created_at DESC, id)",
    "CREATE INDEX ix_memory_proposals_source ON memory_proposals (user_id, source_message_id)",
    """
    CREATE TABLE memory_extraction_jobs (
        id uuid DEFAULT gen_random_uuid() NOT NULL,
        user_id uuid NOT NULL,
        conversation_id uuid NOT NULL,
        source_message_id uuid NOT NULL,
        assistant_message_id uuid NOT NULL,
        source_hash char(64) NOT NULL,
        idempotency_key char(64) NOT NULL,
        consent_epoch integer NOT NULL,
        source_created_at timestamptz NOT NULL,
        status varchar(16) DEFAULT 'queued' NOT NULL,
        attempt_count integer DEFAULT 0 NOT NULL,
        next_attempt_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        lease_expires_at timestamptz,
        lease_owner varchar(100),
        processor_version varchar(64) NOT NULL,
        extractor_provider varchar(64) NOT NULL,
        extractor_model varchar(200) NOT NULL,
        prompt_version varchar(64) NOT NULL,
        schema_version varchar(64) NOT NULL,
        proposal_count integer DEFAULT 0 NOT NULL,
        error_code varchar(64),
        error_type varchar(100),
        completed_at timestamptz,
        created_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        updated_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        CONSTRAINT pk_memory_extraction_jobs PRIMARY KEY (id),
        CONSTRAINT fk_memory_extraction_jobs_user FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_extraction_jobs_conversation_owner FOREIGN KEY(conversation_id, user_id)
            REFERENCES conversations (id, user_id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_extraction_jobs_source_conversation FOREIGN KEY(source_message_id, conversation_id)
            REFERENCES messages (id, conversation_id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_extraction_jobs_assistant_conversation FOREIGN KEY(assistant_message_id, conversation_id)
            REFERENCES messages (id, conversation_id) ON DELETE CASCADE,
        CONSTRAINT uq_memory_extraction_jobs_user_idempotency UNIQUE (user_id, idempotency_key),
        CONSTRAINT ck_memory_extraction_jobs_status CHECK (status IN ('queued', 'running', 'retryable', 'completed', 'dead', 'invalidated')),
        CONSTRAINT ck_memory_extraction_jobs_attempt_count CHECK (attempt_count >= 0),
        CONSTRAINT ck_memory_extraction_jobs_proposal_count CHECK (proposal_count >= 0)
    )
    """,
    "CREATE INDEX ix_memory_extraction_jobs_claim ON memory_extraction_jobs (status, next_attempt_at, created_at)",
    "CREATE INDEX ix_memory_extraction_jobs_user_created ON memory_extraction_jobs (user_id, created_at DESC)",
    """
    CREATE TABLE memory_command_receipts (
        id uuid DEFAULT gen_random_uuid() NOT NULL,
        user_id uuid NOT NULL,
        conversation_id uuid NOT NULL,
        message_id uuid NOT NULL,
        client_request_id varchar(100) NOT NULL,
        command_kind varchar(32) NOT NULL,
        request_hash char(64) NOT NULL,
        status varchar(16) DEFAULT 'started' NOT NULL,
        result_memory_id uuid,
        created_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        completed_at timestamptz,
        CONSTRAINT pk_memory_command_receipts PRIMARY KEY (id),
        CONSTRAINT fk_memory_command_receipts_user FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_command_receipts_conversation_owner FOREIGN KEY(conversation_id, user_id)
            REFERENCES conversations (id, user_id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_command_receipts_message_conversation FOREIGN KEY(message_id, conversation_id)
            REFERENCES messages (id, conversation_id) ON DELETE CASCADE,
        CONSTRAINT fk_memory_command_receipts_result FOREIGN KEY(result_memory_id)
            REFERENCES memory_items (id) ON DELETE SET NULL,
        CONSTRAINT uq_memory_command_receipts_user_request UNIQUE (user_id, client_request_id),
        CONSTRAINT ck_memory_command_receipts_status CHECK (status IN ('started', 'completed', 'failed'))
    )
    """,
    "CREATE INDEX ix_memory_command_receipts_user_created ON memory_command_receipts (user_id, created_at DESC)",
    """
    CREATE TABLE memory_deletion_markers (
        id uuid DEFAULT gen_random_uuid() NOT NULL,
        user_id uuid NOT NULL,
        content_hmac char(64) NOT NULL,
        policy_version varchar(32) NOT NULL,
        reason varchar(16) NOT NULL,
        created_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        CONSTRAINT pk_memory_deletion_markers PRIMARY KEY (id),
        CONSTRAINT fk_memory_deletion_markers_user FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
        CONSTRAINT uq_memory_deletion_markers_content UNIQUE (user_id, content_hmac, policy_version),
        CONSTRAINT ck_memory_deletion_markers_reason CHECK (reason IN ('delete', 'reject', 'clear'))
    )
    """,
    "CREATE INDEX ix_memory_deletion_markers_user_created ON memory_deletion_markers (user_id, created_at DESC)",
    "ALTER TABLE memory_items ADD CONSTRAINT fk_memory_items_proposal_id_memory_proposals FOREIGN KEY(proposal_id) REFERENCES memory_proposals (id) ON DELETE SET NULL",
    "CREATE INDEX ix_memory_items_proposal_id ON memory_items (proposal_id)",
)

DOWNGRADE_BLOCKERS = """
SELECT
    (SELECT count(*) FROM memory_proposals) AS proposals,
    (SELECT count(*) FROM memory_extraction_jobs) AS jobs,
    (SELECT count(*) FROM memory_command_receipts) AS receipts,
    (SELECT count(*) FROM memory_deletion_markers) AS markers,
    (SELECT count(*) FROM memory_items WHERE origin <> 'explicit') AS non_explicit,
    (SELECT count(*) FROM memory_items WHERE proposal_id IS NOT NULL OR valid_from IS NOT NULL OR valid_to IS NOT NULL
        OR superseded_at IS NOT NULL OR processor_version IS NOT NULL OR embedding_dimensions <> 1536
        OR embedding_version <> 'v1') AS enriched
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    connection = op.get_bind()
    blockers = connection.exec_driver_sql(DOWNGRADE_BLOCKERS).mappings().one()
    populated = {name: int(count) for name, count in blockers.items() if count}
    if populated:
        details = ", ".join(f"{name}={count}" for name, count in sorted(populated.items()))
        raise RuntimeError(f"0019 downgrade refused: nonrepresentable memory data exists ({details})")

    statements = (
        "ALTER TABLE memory_items DROP CONSTRAINT fk_memory_items_proposal_id_memory_proposals",
        "DROP INDEX ix_memory_items_proposal_id",
        "DROP TABLE memory_command_receipts",
        "DROP TABLE memory_extraction_jobs",
        "DROP TABLE memory_proposals",
        "DROP TABLE memory_deletion_markers",
        "DROP INDEX ix_memory_items_user_embedding_compat",
        "ALTER TABLE memory_items DROP CONSTRAINT ck_memory_items_valid_range",
        "ALTER TABLE memory_items DROP CONSTRAINT ck_memory_items_embedding_version",
        "ALTER TABLE memory_items DROP CONSTRAINT ck_memory_items_embedding_dimensions",
        "ALTER TABLE memory_items DROP CONSTRAINT ck_memory_items_origin",
        "ALTER TABLE memory_items DROP COLUMN embedding_version",
        "ALTER TABLE memory_items DROP COLUMN embedding_dimensions",
        "ALTER TABLE memory_items DROP COLUMN processor_version",
        "ALTER TABLE memory_items DROP COLUMN superseded_at",
        "ALTER TABLE memory_items DROP COLUMN valid_to",
        "ALTER TABLE memory_items DROP COLUMN valid_from",
        "ALTER TABLE memory_items DROP COLUMN last_confirmed_at",
        "ALTER TABLE memory_items DROP COLUMN proposal_id",
        "ALTER TABLE memory_items DROP COLUMN origin",
        "ALTER TABLE memory_items DROP CONSTRAINT uq_memory_items_id_user",
        "ALTER TABLE messages DROP CONSTRAINT uq_messages_id_conversation",
        "ALTER TABLE conversations DROP CONSTRAINT uq_conversations_id_user",
    )
    for statement in statements:
        op.execute(statement)
