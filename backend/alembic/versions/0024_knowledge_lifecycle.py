"""Let accounts with Knowledge Library rows be deleted, and let the pre-0023 release create global sources.

Revision ID: 0024_knowledge_lifecycle
Revises: 0023_knowledge_library
Create Date: 2026-11-04

0023 pointed every actor column of the Knowledge Library (who uploaded, issued, granted, approved,
reviewed or published) at users(id) ON DELETE RESTRICT, while account deletion is one DELETE of
the user that relies on cascades: an account that had touched the library could no longer be
deleted. The actor becomes NULL instead (the row and its own owner's cascade are unchanged), so the
four actor columns that were NOT NULL become nullable.

0023 also made a new source default to owner_only, and a CHECK requires an owner for that; the
0022-and-earlier `ingest_documents.py --global` inserts a source with no owner and no visibility,
which then fails during a deploy window or after a rollback (migrations run before the switch and
are never downgraded by hand). A BEFORE INSERT trigger gives such a row the values the current code
writes itself (store.py get_or_create_source): global and approved by the operator.

Additive and backward compatible: old code never writes these columns as NULL and keeps working.
"""

from alembic import op

revision = "0024_knowledge_lifecycle"
down_revision = "0023_knowledge_library"
branch_labels = None
depends_on = None

UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "ALTER TABLE knowledge_admin_grants DROP CONSTRAINT knowledge_admin_grants_granted_by_user_id_fkey, ADD CONSTRAINT knowledge_admin_grants_granted_by_user_id_fkey FOREIGN KEY (granted_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE knowledge_admin_grants DROP CONSTRAINT knowledge_admin_grants_revoked_by_user_id_fkey, ADD CONSTRAINT knowledge_admin_grants_revoked_by_user_id_fkey FOREIGN KEY (revoked_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE knowledge_sources DROP CONSTRAINT knowledge_sources_rights_approved_by_user_id_fkey, ADD CONSTRAINT knowledge_sources_rights_approved_by_user_id_fkey FOREIGN KEY (rights_approved_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE knowledge_source_user_grants DROP CONSTRAINT knowledge_source_user_grants_granted_by_user_id_fkey, ADD CONSTRAINT knowledge_source_user_grants_granted_by_user_id_fkey FOREIGN KEY (granted_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE knowledge_source_user_grants ALTER COLUMN granted_by_user_id DROP NOT NULL",
    "ALTER TABLE knowledge_source_user_grants DROP CONSTRAINT knowledge_source_user_grants_revoked_by_user_id_fkey, ADD CONSTRAINT knowledge_source_user_grants_revoked_by_user_id_fkey FOREIGN KEY (revoked_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE knowledge_import_batches DROP CONSTRAINT knowledge_import_batches_issued_by_user_id_fkey, ADD CONSTRAINT knowledge_import_batches_issued_by_user_id_fkey FOREIGN KEY (issued_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE knowledge_import_batches ALTER COLUMN issued_by_user_id DROP NOT NULL",
    "ALTER TABLE document_files DROP CONSTRAINT document_files_created_by_user_id_fkey, ADD CONSTRAINT document_files_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE document_files ALTER COLUMN created_by_user_id DROP NOT NULL",
    "ALTER TABLE document_revisions DROP CONSTRAINT document_revisions_created_by_user_id_fkey, ADD CONSTRAINT document_revisions_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE document_revisions ALTER COLUMN created_by_user_id DROP NOT NULL",
    "ALTER TABLE document_revisions DROP CONSTRAINT document_revisions_reviewed_by_user_id_fkey, ADD CONSTRAINT document_revisions_reviewed_by_user_id_fkey FOREIGN KEY (reviewed_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE document_revisions DROP CONSTRAINT document_revisions_published_by_user_id_fkey, ADD CONSTRAINT document_revisions_published_by_user_id_fkey FOREIGN KEY (published_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE knowledge_duplicate_reviews DROP CONSTRAINT knowledge_duplicate_reviews_reviewed_by_user_id_fkey, ADD CONSTRAINT knowledge_duplicate_reviews_reviewed_by_user_id_fkey FOREIGN KEY (reviewed_by_user_id) REFERENCES users(id) ON DELETE SET NULL",
)

# A NULL actor means its account was deleted under 0024: refused before any DDL.
DOWNGRADE_CHECKS = (
    "DO $$ BEGIN IF EXISTS (SELECT 1 FROM knowledge_source_user_grants WHERE granted_by_user_id IS NULL) THEN RAISE EXCEPTION "
    "'0024 downgrade refused: knowledge_source_user_grants.granted_by_user_id has rows of deleted accounts'; END IF; END $$",
    "DO $$ BEGIN IF EXISTS (SELECT 1 FROM knowledge_import_batches WHERE issued_by_user_id IS NULL) THEN RAISE EXCEPTION "
    "'0024 downgrade refused: knowledge_import_batches.issued_by_user_id has rows of deleted accounts'; END IF; END $$",
    "DO $$ BEGIN IF EXISTS (SELECT 1 FROM document_files WHERE created_by_user_id IS NULL) THEN RAISE EXCEPTION "
    "'0024 downgrade refused: document_files.created_by_user_id has rows of deleted accounts'; END IF; END $$",
    "DO $$ BEGIN IF EXISTS (SELECT 1 FROM document_revisions WHERE created_by_user_id IS NULL) THEN RAISE EXCEPTION "
    "'0024 downgrade refused: document_revisions.created_by_user_id has rows of deleted accounts'; END IF; END $$",
)

DOWNGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "DROP TRIGGER knowledge_sources_global_without_owner ON knowledge_sources",
    "DROP FUNCTION knowledge_sources_global_without_owner()",
    "ALTER TABLE knowledge_admin_grants DROP CONSTRAINT knowledge_admin_grants_granted_by_user_id_fkey, ADD CONSTRAINT knowledge_admin_grants_granted_by_user_id_fkey FOREIGN KEY (granted_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE knowledge_admin_grants DROP CONSTRAINT knowledge_admin_grants_revoked_by_user_id_fkey, ADD CONSTRAINT knowledge_admin_grants_revoked_by_user_id_fkey FOREIGN KEY (revoked_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE knowledge_sources DROP CONSTRAINT knowledge_sources_rights_approved_by_user_id_fkey, ADD CONSTRAINT knowledge_sources_rights_approved_by_user_id_fkey FOREIGN KEY (rights_approved_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE knowledge_source_user_grants ALTER COLUMN granted_by_user_id SET NOT NULL",
    "ALTER TABLE knowledge_source_user_grants DROP CONSTRAINT knowledge_source_user_grants_granted_by_user_id_fkey, ADD CONSTRAINT knowledge_source_user_grants_granted_by_user_id_fkey FOREIGN KEY (granted_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE knowledge_source_user_grants DROP CONSTRAINT knowledge_source_user_grants_revoked_by_user_id_fkey, ADD CONSTRAINT knowledge_source_user_grants_revoked_by_user_id_fkey FOREIGN KEY (revoked_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE knowledge_import_batches ALTER COLUMN issued_by_user_id SET NOT NULL",
    "ALTER TABLE knowledge_import_batches DROP CONSTRAINT knowledge_import_batches_issued_by_user_id_fkey, ADD CONSTRAINT knowledge_import_batches_issued_by_user_id_fkey FOREIGN KEY (issued_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE document_files ALTER COLUMN created_by_user_id SET NOT NULL",
    "ALTER TABLE document_files DROP CONSTRAINT document_files_created_by_user_id_fkey, ADD CONSTRAINT document_files_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE document_revisions ALTER COLUMN created_by_user_id SET NOT NULL",
    "ALTER TABLE document_revisions DROP CONSTRAINT document_revisions_created_by_user_id_fkey, ADD CONSTRAINT document_revisions_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE document_revisions DROP CONSTRAINT document_revisions_reviewed_by_user_id_fkey, ADD CONSTRAINT document_revisions_reviewed_by_user_id_fkey FOREIGN KEY (reviewed_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE document_revisions DROP CONSTRAINT document_revisions_published_by_user_id_fkey, ADD CONSTRAINT document_revisions_published_by_user_id_fkey FOREIGN KEY (published_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE knowledge_duplicate_reviews DROP CONSTRAINT knowledge_duplicate_reviews_reviewed_by_user_id_fkey, ADD CONSTRAINT knowledge_duplicate_reviews_reviewed_by_user_id_fkey FOREIGN KEY (reviewed_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
)

TRIGGER_FUNCTION = """
CREATE FUNCTION knowledge_sources_global_without_owner() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.owner_user_id IS NULL AND NEW.visibility = 'owner_only' THEN
        NEW.visibility := 'global';
        NEW.rights_status := 'approved';
        NEW.rights_basis := coalesce(NEW.rights_basis, 'Global source created by an operator');
        NEW.rights_approved_at := coalesce(NEW.rights_approved_at, clock_timestamp());
    END IF;
    RETURN NEW;
END $$
"""
TRIGGER = """
CREATE TRIGGER knowledge_sources_global_without_owner
BEFORE INSERT ON knowledge_sources
FOR EACH ROW EXECUTE FUNCTION knowledge_sources_global_without_owner()
"""


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)
    op.execute(TRIGGER_FUNCTION)
    op.execute(TRIGGER)


def downgrade() -> None:
    for statement in (*DOWNGRADE_CHECKS, *DOWNGRADE):
        op.execute(statement)
