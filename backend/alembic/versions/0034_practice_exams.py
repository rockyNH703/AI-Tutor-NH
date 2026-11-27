"""Practice exams: a program month's bank questions as one timed paper, outside the chat (session 62, Task 1C).

Revision ID: 0034_practice_exams
Revises: 0033_chat_pins_archive_shares
Create Date: 2026-11-27

- practice_exams: one row per exam (app/exams). Its questions live in a `test` practice set (qb_sets mode 'test',
  allowed since 0015), served whole at the start; the drafts (`responses`) are graded into that set once, at the
  submit, and the result is stored with the exam. One exam per (user, request id): a retried start finds it.
Additive only: a new table, nothing existing is read or rewritten, so the release before this one keeps working.
The downgrade (dev and test only; production never downgrades) is refused while any exam exists.
"""

from alembic import op

revision = "0034_practice_exams"
down_revision = "0033_chat_pins_archive_shares"
branch_labels = None
depends_on = None

# One statement per op.execute: asyncpg prepares each one, and refuses several at once.
UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    """
    CREATE TABLE practice_exams (
        id uuid DEFAULT gen_random_uuid() NOT NULL,
        user_id uuid NOT NULL,
        request_id uuid NOT NULL,
        set_id uuid NOT NULL,
        program_id uuid NOT NULL,
        month smallint NOT NULL,
        topic_ids text[] NOT NULL,
        difficulty varchar(6) NOT NULL,
        length smallint NOT NULL,
        minutes smallint NOT NULL,
        paper text[] NOT NULL,
        started_at timestamptz DEFAULT clock_timestamp() NOT NULL,
        ends_at timestamptz NOT NULL,
        submitted_at timestamptz,
        auto_submitted boolean DEFAULT false NOT NULL,
        rev integer DEFAULT 0 NOT NULL,
        responses jsonb DEFAULT '[]'::jsonb NOT NULL,
        result jsonb,
        CONSTRAINT pk_practice_exams PRIMARY KEY (id),
        CONSTRAINT fk_practice_exams_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        CONSTRAINT fk_practice_exams_set_id_qb_sets FOREIGN KEY (set_id) REFERENCES qb_sets (id) ON DELETE CASCADE,
        CONSTRAINT fk_practice_exams_program_id_programs
            FOREIGN KEY (program_id) REFERENCES programs (id) ON DELETE RESTRICT,
        CONSTRAINT uq_practice_exams_user_id_request_id UNIQUE (user_id, request_id),
        CONSTRAINT uq_practice_exams_set_id UNIQUE (set_id),
        CONSTRAINT ck_practice_exams_month CHECK (month BETWEEN 1 AND 12),
        CONSTRAINT ck_practice_exams_difficulty CHECK (difficulty IN ('mixed', 'easy', 'medium', 'hard')),
        CONSTRAINT ck_practice_exams_length CHECK (length BETWEEN 1 AND 30),
        CONSTRAINT ck_practice_exams_minutes CHECK (minutes BETWEEN 1 AND 60),
        CONSTRAINT ck_practice_exams_paper CHECK (cardinality(paper) = length),
        CONSTRAINT ck_practice_exams_topic_ids CHECK (cardinality(topic_ids) BETWEEN 1 AND 50),
        CONSTRAINT ck_practice_exams_rev CHECK (rev >= 0),
        CONSTRAINT ck_practice_exams_submitted CHECK ((submitted_at IS NULL) = (result IS NULL))
    )
    """,
)

DOWNGRADE_CHECKS = (
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM practice_exams) THEN
            RAISE EXCEPTION '0034 downgrade refused: practice exams exist' USING ERRCODE = 'check_violation';
        END IF;
    END $$
    """,
)

DOWNGRADE = ("DROP TABLE practice_exams",)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in (*DOWNGRADE_CHECKS, *DOWNGRADE):
        op.execute(statement)
