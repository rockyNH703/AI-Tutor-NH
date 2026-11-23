"""NH programs: invoice-verified program areas with month tutors (app/programs).

Revision ID: 0028_nh_programs
Revises: 0027_nhprep_video_courses
Create Date: 2026-11-23

- programs, program_modules: NH's programs and their months (rows: 0029, from the owner's curriculum).
- program_module_documents / _books / _courses: what each month may use (Knowledge Library documents, question-bank
  tracks, NHprep courses). One row per pairing, never a copy of the content.
- nh_invoices: the owner's invoice records (SQL or scripts/program_admin.py); the source of truth for access.
- invoice_claims: which account entered an invoice; UNIQUE(invoice_id) makes the first valid claim the only one.
- conversations.program_id / module_id / mode: a program chat (QnA when module_id is NULL); NULL for every other chat.
- users.invoice_prompt_done_at: the first-run invoice prompt was answered with "continue without".

Additive: new tables and nullable columns only, so the release before this one keeps working on this schema. The
conversations index is created plainly (not CONCURRENTLY): env.py runs migrations in one transaction, and the table
is small.
"""

from alembic import op

revision = "0028_nh_programs"
down_revision = "0027_nhprep_video_courses"
branch_labels = None
depends_on = None

# One statement per op.execute: asyncpg prepares each one, and refuses several at once.
UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    """
    CREATE TABLE programs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug varchar(60) NOT NULL CONSTRAINT uq_programs_slug UNIQUE,
        title varchar(120) NOT NULL,
        position smallint NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_programs_slug CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')
    )
    """,
    """
    CREATE TABLE program_modules (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        program_id uuid NOT NULL REFERENCES programs (id) ON DELETE CASCADE,
        month smallint NOT NULL,
        title varchar(200) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_program_modules_month UNIQUE (program_id, month),
        CONSTRAINT uq_program_modules_identity UNIQUE (id, program_id),
        CONSTRAINT ck_program_modules_month CHECK (month BETWEEN 1 AND 12)
    )
    """,
    """
    CREATE TABLE program_module_documents (
        module_id uuid NOT NULL REFERENCES program_modules (id) ON DELETE CASCADE,
        document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        PRIMARY KEY (module_id, document_id)
    )
    """,
    "CREATE INDEX ix_program_module_documents_document ON program_module_documents (document_id)",
    """
    CREATE TABLE program_module_books (
        module_id uuid NOT NULL REFERENCES program_modules (id) ON DELETE CASCADE,
        book_id text NOT NULL REFERENCES qb_books (book_id) ON DELETE CASCADE,
        PRIMARY KEY (module_id, book_id)
    )
    """,
    """
    CREATE TABLE program_module_courses (
        module_id uuid NOT NULL REFERENCES program_modules (id) ON DELETE CASCADE,
        course_id uuid NOT NULL REFERENCES nhprep_courses (id) ON DELETE CASCADE,
        PRIMARY KEY (module_id, course_id)
    )
    """,
    "CREATE INDEX ix_program_module_courses_course ON program_module_courses (course_id)",
    """
    CREATE TABLE nh_invoices (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        invoice_no varchar(40) NOT NULL CONSTRAINT uq_nh_invoices_invoice_no UNIQUE,
        program_id uuid NOT NULL REFERENCES programs (id) ON DELETE RESTRICT,
        status varchar(10) NOT NULL DEFAULT 'active',
        valid_until timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_nh_invoices_number CHECK (invoice_no ~ '^[A-Z0-9/_.-]{3,40}$'),
        CONSTRAINT ck_nh_invoices_status CHECK (status IN ('active', 'revoked', 'refunded', 'expired'))
    )
    """,
    "CREATE INDEX ix_nh_invoices_program ON nh_invoices (program_id)",
    """
    CREATE TABLE invoice_claims (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        invoice_id uuid NOT NULL CONSTRAINT uq_invoice_claims_invoice_id UNIQUE REFERENCES nh_invoices (id) ON DELETE CASCADE,
        claimed_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )
    """,
    "CREATE INDEX ix_invoice_claims_user ON invoice_claims (user_id)",
    "ALTER TABLE conversations ADD COLUMN program_id uuid REFERENCES programs (id) ON DELETE RESTRICT",
    "ALTER TABLE conversations ADD COLUMN module_id uuid",
    "ALTER TABLE conversations ADD COLUMN mode varchar(10)",
    """
    ALTER TABLE conversations ADD CONSTRAINT fk_conversations_module
        FOREIGN KEY (module_id, program_id) REFERENCES program_modules (id, program_id) ON DELETE RESTRICT
    """,
    "ALTER TABLE conversations ADD CONSTRAINT ck_conversations_program_mode CHECK ((program_id IS NULL) = (mode IS NULL))",
    "ALTER TABLE conversations ADD CONSTRAINT ck_conversations_mode CHECK (mode IS NULL OR mode IN ('tutor', 'interview'))",
    "ALTER TABLE conversations ADD CONSTRAINT ck_conversations_module CHECK (module_id IS NULL OR program_id IS NOT NULL)",
    """
    CREATE INDEX ix_conversations_user_program
        ON conversations (user_id, program_id, module_id, updated_at DESC, id) WHERE program_id IS NOT NULL
    """,
    "ALTER TABLE users ADD COLUMN invoice_prompt_done_at timestamptz",
)

# Students' claims, program chats or the content mapping would be lost: refused before any DDL.
DOWNGRADE_CHECKS = (
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM invoice_claims) OR EXISTS (SELECT 1 FROM conversations WHERE program_id IS NOT NULL)
           OR EXISTS (SELECT 1 FROM program_module_documents) OR EXISTS (SELECT 1 FROM program_module_books)
           OR EXISTS (SELECT 1 FROM program_module_courses) OR EXISTS (SELECT 1 FROM nh_invoices) THEN
            RAISE EXCEPTION '0028 downgrade refused: invoices, claims, program chats or mappings exist'
                USING ERRCODE = 'check_violation';
        END IF;
    END $$
    """,
)

DOWNGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "ALTER TABLE users DROP COLUMN invoice_prompt_done_at",
    "DROP INDEX ix_conversations_user_program",
    "ALTER TABLE conversations DROP CONSTRAINT ck_conversations_module",
    "ALTER TABLE conversations DROP CONSTRAINT ck_conversations_mode",
    "ALTER TABLE conversations DROP CONSTRAINT ck_conversations_program_mode",
    "ALTER TABLE conversations DROP CONSTRAINT fk_conversations_module",
    "ALTER TABLE conversations DROP COLUMN mode",
    "ALTER TABLE conversations DROP COLUMN module_id",
    "ALTER TABLE conversations DROP COLUMN program_id",
    "DROP TABLE invoice_claims",
    "DROP TABLE nh_invoices",
    "DROP TABLE program_module_courses",
    "DROP TABLE program_module_books",
    "DROP TABLE program_module_documents",
    "DROP TABLE program_modules",
    "DROP TABLE programs",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in (*DOWNGRADE_CHECKS, *DOWNGRADE):
        op.execute(statement)
