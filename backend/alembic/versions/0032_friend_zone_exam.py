"""NH Friend Zone: the battle becomes a JEE/NEET-style exam (one paper, one deadline, editable answers).

Revision ID: 0032_friend_zone_exam
Revises: 0031_friend_zone_progress
Create Date: 2026-11-25

Plan: owner-approved "NH Friend Challenge -> exam battle", revision 2, section 1 (owner decisions D1-D8, O1-O8).
- fz_battles.format: 'rounds' (every battle before this release) or 'exam'. The default stays 'rounds': the release
  before this one keeps serving while 0032 runs, and code rolled back to it creates round battles. New code always
  writes 'exam'. The length CHECK becomes format-aware: rounds 5 or 10, an exam 10, 20 or 30.
- fz_battle_players.submitted_at: an exam's final submission (never later than the battle's ends_at).
- fz_battle_responses: the ungraded exam drafts, one row per player and question, with a server revision (`rev`,
  compare-and-set). Both foreign keys cascade, so deleting a battle (qa-cleanup) takes its drafts. No user column.
- Data: an invited or waiting battle past its expiry becomes `expired` (as expire_due does), or `cancelled`
  ('deleted') when a player has left; an active battle becomes completed, void, 'upgraded' (its answers stay
  practice, as with any void). No row becomes an exam here. An unexpired invitation is left as it is: the release
  turns Friend Zone off and waits out INVITE_TTL first, and the new code cancels any such legacy row as 'upgraded'.

The downgrade (dev and test only; production never downgrades) is refused while any exam, submission or draft
exists.
"""

from alembic import op

revision = "0032_friend_zone_exam"
down_revision = "0031_friend_zone_progress"
branch_labels = None
depends_on = None

LEGACY_LENGTH = "CHECK (length IN (5, 10))"
EXAM_LENGTH = "CHECK ((format = 'rounds' AND length IN (5, 10)) OR (format = 'exam' AND length IN (10, 20, 30)))"

# One statement per op.execute: asyncpg prepares each one, and refuses several at once.
UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "ALTER TABLE fz_battles ADD COLUMN format varchar(6) NOT NULL DEFAULT 'rounds'",
    "ALTER TABLE fz_battles ADD CONSTRAINT ck_fz_battles_format CHECK (format IN ('rounds', 'exam'))",
    f"ALTER TABLE fz_battles DROP CONSTRAINT ck_fz_battles_length, ADD CONSTRAINT ck_fz_battles_length {EXAM_LENGTH}",
    "ALTER TABLE fz_battle_players ADD COLUMN submitted_at timestamptz",
    """
    CREATE TABLE fz_battle_responses (
        battle_id uuid NOT NULL,
        role varchar(10) NOT NULL,
        round smallint NOT NULL,
        choice char(1),
        marked boolean NOT NULL DEFAULT false,
        rev integer NOT NULL,
        saved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_fz_battle_responses PRIMARY KEY (battle_id, role, round),
        CONSTRAINT fk_fz_battle_responses_player FOREIGN KEY (battle_id, role)
            REFERENCES fz_battle_players (battle_id, role) ON DELETE CASCADE,
        CONSTRAINT fk_fz_battle_responses_round FOREIGN KEY (battle_id, round)
            REFERENCES fz_battle_rounds (battle_id, round) ON DELETE CASCADE,
        CONSTRAINT ck_fz_battle_responses_round CHECK (round BETWEEN 1 AND 30),
        CONSTRAINT ck_fz_battle_responses_choice
            CHECK (choice IS NULL OR choice IN ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H')),
        CONSTRAINT ck_fz_battle_responses_rev CHECK (rev >= 1)
    )
    """,
    # In-flight legacy battles (section 1). Expired invitations first: a player who left cancels it ('deleted').
    """
    UPDATE fz_battles SET status = 'cancelled', end_reason = 'deleted', pair_key = NULL,
        ended_at = clock_timestamp(), version = version + 1
    WHERE status IN ('invited', 'waiting') AND expires_at <= clock_timestamp()
      AND EXISTS (SELECT 1 FROM fz_battle_players p WHERE p.battle_id = fz_battles.id AND p.user_id IS NULL)
    """,
    """
    UPDATE fz_battles SET status = 'expired', end_reason = 'expired', pair_key = NULL,
        ended_at = clock_timestamp(), version = version + 1
    WHERE status IN ('invited', 'waiting') AND expires_at <= clock_timestamp()
    """,
    """
    UPDATE qb_sets SET closed_at = now()
    WHERE closed_at IS NULL AND id IN (
        SELECT p.set_id FROM fz_battle_players p JOIN fz_battles b ON b.id = p.battle_id
        WHERE b.status = 'active' AND p.set_id IS NOT NULL
    )
    """,
    """
    UPDATE fz_battles SET status = 'completed', result = 'void', end_reason = 'upgraded', pair_key = NULL,
        ended_at = clock_timestamp(), version = version + 1
    WHERE status = 'active'
    """,
)

DOWNGRADE_CHECKS = (
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM fz_battles WHERE format = 'exam')
           OR EXISTS (SELECT 1 FROM fz_battle_players WHERE submitted_at IS NOT NULL)
           OR EXISTS (SELECT 1 FROM fz_battle_responses) THEN
            RAISE EXCEPTION '0032 downgrade refused: exam battles, submissions or drafts exist'
                USING ERRCODE = 'check_violation';
        END IF;
    END $$
    """,
)

DOWNGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "DROP TABLE fz_battle_responses",
    "ALTER TABLE fz_battle_players DROP COLUMN submitted_at",
    f"ALTER TABLE fz_battles DROP CONSTRAINT ck_fz_battles_length, ADD CONSTRAINT ck_fz_battles_length {LEGACY_LENGTH}",
    "ALTER TABLE fz_battles DROP CONSTRAINT ck_fz_battles_format",
    "ALTER TABLE fz_battles DROP COLUMN format",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in (*DOWNGRADE_CHECKS, *DOWNGRADE):
        op.execute(statement)
