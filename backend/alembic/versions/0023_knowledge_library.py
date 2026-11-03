"""Add the durable Knowledge Library control plane and revision staging tables.

Revision ID: 0023_knowledge_library
Revises: 0022_password_session_revocation
Create Date: 2026-11-03

This migration is additive. Existing knowledge sources remain readable with approved global
visibility; newly created sources default to owner-only and unverified until an operator records
an explicit rights decision.
"""

from alembic import op

revision = "0023_knowledge_library"
down_revision = "0022_password_session_revocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE knowledge_sources
            ADD COLUMN visibility varchar(20) NOT NULL DEFAULT 'owner_only',
            ADD COLUMN rights_status varchar(20) NOT NULL DEFAULT 'unverified',
            ADD COLUMN rights_basis varchar(500),
            ADD COLUMN rights_approved_at timestamptz,
            ADD COLUMN rights_approved_by_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            ADD COLUMN rights_revoked_at timestamptz,
            ADD CONSTRAINT ck_knowledge_sources_visibility
                CHECK (visibility IN ('owner_only', 'selected', 'global')),
            ADD CONSTRAINT ck_knowledge_sources_rights_status
                CHECK (rights_status IN ('unverified', 'approved', 'revoked')),
            ADD CONSTRAINT ck_knowledge_sources_published_owner
                CHECK (visibility = 'global' OR owner_user_id IS NOT NULL),
            ADD CONSTRAINT ck_knowledge_sources_distribution_rights_approved
                CHECK (visibility = 'owner_only' OR rights_status = 'approved')
    """)
    op.execute("""
        UPDATE knowledge_sources
        SET visibility = 'global',
            rights_status = 'approved',
            rights_basis = 'Legacy global source preserved by migration',
            rights_approved_at = clock_timestamp()
        WHERE owner_user_id IS NULL
    """)
    op.execute("CREATE INDEX ix_knowledge_sources_visibility ON knowledge_sources (visibility, rights_status)")

    op.execute("""
        CREATE TABLE knowledge_admin_grants (
            user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            granted_by_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            granted_by_operator varchar(200),
            reason varchar(280) NOT NULL,
            granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz,
            revoked_at timestamptz,
            revoked_by_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            revoke_reason varchar(280)
        )
    """)
    op.execute(
        "CREATE INDEX ix_knowledge_admin_grants_active ON knowledge_admin_grants (user_id, revoked_at, expires_at)"
    )

    op.execute("""
        CREATE TABLE knowledge_source_user_grants (
            source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            granted_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz,
            revoked_at timestamptz,
            revoked_by_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            PRIMARY KEY (source_id, user_id)
        )
    """)
    op.execute(
        "CREATE INDEX ix_knowledge_source_user_grants_user ON knowledge_source_user_grants (user_id, revoked_at)"
    )

    op.execute("""
        CREATE TABLE knowledge_import_batches (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            issued_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            secret_hash char(64) NOT NULL CONSTRAINT uq_knowledge_import_batches_secret_hash UNIQUE,
            status varchar(20) NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'completed', 'revoked', 'expired')),
            expected_file_count integer,
            expected_total_bytes bigint,
            received_file_count integer NOT NULL DEFAULT 0,
            received_total_bytes bigint NOT NULL DEFAULT 0,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            completed_at timestamptz,
            revoked_at timestamptz
        )
    """)
    op.execute("CREATE INDEX ix_knowledge_import_batches_owner ON knowledge_import_batches (owner_user_id, created_at)")

    op.execute("""
        CREATE TABLE document_files (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            import_batch_id uuid REFERENCES knowledge_import_batches(id) ON DELETE SET NULL,
            filename varchar(255) NOT NULL,
            media_type varchar(100) NOT NULL DEFAULT 'application/pdf',
            size_bytes bigint NOT NULL CHECK (size_bytes > 0),
            received_bytes bigint NOT NULL DEFAULT 0
                CHECK (received_bytes >= 0 AND received_bytes <= size_bytes),
            sha256 char(64) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'uploading'
                CHECK (status IN ('uploading', 'ready', 'rejected', 'deleted')),
            page_count integer,
            created_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            completed_at timestamptz,
            deleted_at timestamptz,
            CONSTRAINT uq_document_files_source_sha256 UNIQUE (source_id, sha256)
        )
    """)
    op.execute("CREATE INDEX ix_document_files_source_created ON document_files (source_id, created_at)")
    op.execute("""
        CREATE TABLE document_file_blobs (
            file_id uuid NOT NULL REFERENCES document_files(id) ON DELETE CASCADE,
            ordinal integer NOT NULL,
            data bytea NOT NULL CHECK (octet_length(data) BETWEEN 1 AND 1048576),
            PRIMARY KEY (file_id, ordinal)
        )
    """)

    op.execute("""
        CREATE TABLE document_revisions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
            file_id uuid NOT NULL REFERENCES document_files(id) ON DELETE RESTRICT,
            revision_number integer NOT NULL,
            status varchar(30) NOT NULL DEFAULT 'queued' CHECK (status IN (
                'queued','extracting','needs_review','chunking','embedding','verifying',
                'ready_to_publish','published','retryable','failed','cancelled','retired')),
            processing_signature char(64) NOT NULL,
            parser_name varchar(80) NOT NULL,
            parser_version varchar(80) NOT NULL,
            parser_config_hash char(64) NOT NULL,
            output_digest char(64),
            chunking_config_hash char(64) NOT NULL,
            embedding_model varchar(200) NOT NULL,
            embedding_dimensions integer NOT NULL,
            attempts smallint NOT NULL DEFAULT 0,
            claimed_at timestamptz,
            next_attempt_at timestamptz,
            error_code varchar(80),
            quality_report jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            reviewed_by_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            published_by_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            reviewed_at timestamptz,
            published_at timestamptz,
            retired_at timestamptz,
            CONSTRAINT uq_document_revisions_number UNIQUE (document_id, revision_number),
            CONSTRAINT uq_document_revisions_signature UNIQUE (document_id, processing_signature),
            CONSTRAINT uq_document_revisions_identity UNIQUE (id, document_id, source_id)
        )
    """)
    op.execute("CREATE INDEX ix_document_revisions_pending ON document_revisions (status, next_attempt_at, created_at)")

    op.execute("""
        CREATE TABLE document_revision_chunks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            revision_id uuid NOT NULL,
            document_id uuid NOT NULL,
            source_id uuid NOT NULL,
            stable_chunk_key char(64) NOT NULL,
            ordinal integer NOT NULL,
            content text NOT NULL,
            content_sha256 char(64) NOT NULL,
            section_path varchar(300)[] NOT NULL DEFAULT '{}',
            physical_page_start integer NOT NULL,
            physical_page_end integer NOT NULL,
            printed_page_label varchar(40),
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            embedding vector(1536),
            embedding_model varchar(200),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (revision_id, document_id, source_id)
                REFERENCES document_revisions(id, document_id, source_id) ON DELETE CASCADE,
            CONSTRAINT uq_document_revision_chunks_key UNIQUE (revision_id, stable_chunk_key),
            CONSTRAINT uq_document_revision_chunks_ordinal UNIQUE (revision_id, ordinal)
        )
    """)
    op.execute(
        "CREATE INDEX ix_document_revision_chunks_page ON document_revision_chunks (revision_id, physical_page_start)"
    )

    op.execute("""
        CREATE TABLE knowledge_duplicate_reviews (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id uuid NOT NULL REFERENCES document_files(id) ON DELETE CASCADE,
            candidate_file_id uuid NOT NULL REFERENCES document_files(id) ON DELETE CASCADE,
            classification varchar(40) NOT NULL CHECK (classification IN (
                'exact','confirmed_content','probable','same_edition_representation',
                'different_edition','related','inconclusive')),
            confidence smallint NOT NULL,
            signals jsonb NOT NULL DEFAULT '{}'::jsonb,
            decision varchar(30) NOT NULL DEFAULT 'pending' CHECK (decision IN (
                'pending','reuse','keep_separate','reject','quarantine_approved')),
            reviewed_by_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            reviewed_at timestamptz,
            quarantine_until timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT uq_knowledge_duplicate_reviews_pair UNIQUE (file_id, candidate_file_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE knowledge_duplicate_reviews")
    op.execute("DROP TABLE document_revision_chunks")
    op.execute("DROP TABLE document_revisions")
    op.execute("DROP TABLE document_file_blobs")
    op.execute("DROP TABLE document_files")
    op.execute("DROP TABLE knowledge_import_batches")
    op.execute("DROP TABLE knowledge_source_user_grants")
    op.execute("DROP TABLE knowledge_admin_grants")
    op.execute("DROP INDEX ix_knowledge_sources_visibility")
    op.execute("""
        ALTER TABLE knowledge_sources
            DROP CONSTRAINT ck_knowledge_sources_distribution_rights_approved,
            DROP CONSTRAINT ck_knowledge_sources_published_owner,
            DROP CONSTRAINT ck_knowledge_sources_rights_status,
            DROP CONSTRAINT ck_knowledge_sources_visibility,
            DROP COLUMN rights_revoked_at,
            DROP COLUMN rights_approved_by_user_id,
            DROP COLUMN rights_approved_at,
            DROP COLUMN rights_basis,
            DROP COLUMN rights_status,
            DROP COLUMN visibility
    """)
