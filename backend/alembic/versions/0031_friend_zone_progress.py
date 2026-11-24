"""NH Friend Zone, Phase 2: daily challenges, XP, badges, rematch, and hash-bound AI key checks.

Revision ID: 0031_friend_zone_progress
Revises: 0030_friend_zone
Create Date: 2026-11-24

Plan: owner-approved Friend Zone plan, revision 3 (sections 5, 8, 13; owner decisions CD16-CD18 of 2026-11-24).
- fz_daily_challenges: one per program per India day, ten competitive questions, each with its own window.
- fz_daily_attempts: one per member and challenge (G7), in the member's cohort (composite key to the profile).
- fz_xp_events and fz_badge_unlocks: live members only (CHECK cohort = 'live'), so a QA account can never earn XP
  or a badge; UNIQUE (user_id, reason, ref_id) makes every award happen once.
- fz_battles.rematch_of: a rematch's link to the battle it repeats.
- qb_key_checks: an AI blind check of a question's key, bound to the exact content it checked (content_hash).
  A question counts as AI-verified only while its current content_hash has such a row (U1, option ii).
- qb_sets.mode gains 'daily'; user_notifications.kind gains the Phase 2 kinds (daily challenge ready and done, an XP
  milestone, a new level, a badge).

Additive, like 0030. The downgrade is refused while any of these rows exist.
"""

from alembic import op

revision = "0031_friend_zone_progress"
down_revision = "0030_friend_zone"
branch_labels = None
depends_on = None

KINDS_0030 = (
    "'streak_milestone', 'streak_reminder', 'daily_reminder', 'weekly_summary', 'security_sign_in', "
    "'security_password_changed', 'security_google_linked', 'security_two_factor_on', 'security_two_factor_off', "
    "'friend_request', 'friend_accepted', 'battle_invite', 'battle_accepted', 'battle_finished'"
)
NEW_KINDS = "'daily_available', 'daily_completed', 'xp_milestone', 'level_up', 'badge_unlocked'"
KINDS_0031 = f"{KINDS_0030}, {NEW_KINDS}"
BADGES = "('first_battle', 'quiz_champion', 'daily_challenger', 'consistency_master', 'perfect_score')"

# One statement per op.execute: asyncpg prepares each one, and refuses several at once.
UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    """
    CREATE TABLE fz_daily_challenges (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        program_id uuid NOT NULL REFERENCES programs (id) ON DELETE RESTRICT,
        challenge_date date NOT NULL,
        question_ids text[] NOT NULL,
        seconds smallint[] NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_fz_daily_challenges_program_id_challenge_date UNIQUE (program_id, challenge_date),
        CONSTRAINT ck_fz_daily_challenges_questions
            CHECK (cardinality(question_ids) = 10 AND cardinality(seconds) = 10)
    )
    """,
    """
    CREATE TABLE fz_daily_attempts (
        challenge_id uuid NOT NULL REFERENCES fz_daily_challenges (id) ON DELETE CASCADE,
        user_id uuid NOT NULL,
        cohort varchar(4) NOT NULL,
        set_id uuid REFERENCES qb_sets (id) ON DELETE SET NULL,
        started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        finished_at timestamptz,
        served smallint NOT NULL DEFAULT 0,
        answered smallint NOT NULL DEFAULT 0,
        correct smallint NOT NULL DEFAULT 0,
        score integer NOT NULL DEFAULT 0,
        voided smallint[] NOT NULL DEFAULT '{}',
        PRIMARY KEY (challenge_id, user_id),
        CONSTRAINT fk_fz_daily_attempts_user_id_fz_profiles FOREIGN KEY (user_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT ck_fz_daily_attempts_counts
            CHECK (served BETWEEN 0 AND 10 AND answered BETWEEN 0 AND served AND correct BETWEEN 0 AND answered)
    )
    """,
    "CREATE INDEX ix_fz_daily_attempts_user_id ON fz_daily_attempts (user_id, finished_at)",
    """
    CREATE TABLE fz_xp_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL,
        cohort varchar(4) NOT NULL,
        reason varchar(24) NOT NULL,
        ref_id varchar(80) NOT NULL,
        amount smallint NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_fz_xp_events_user_id_reason_ref_id UNIQUE (user_id, reason, ref_id),
        CONSTRAINT fk_fz_xp_events_user_id_fz_profiles FOREIGN KEY (user_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT ck_fz_xp_events_live CHECK (cohort = 'live'),
        CONSTRAINT ck_fz_xp_events_amount CHECK (amount BETWEEN 1 AND 500)
    )
    """,
    "CREATE INDEX ix_fz_xp_events_user_id_created_at ON fz_xp_events (user_id, created_at)",
    f"""
    CREATE TABLE fz_badge_unlocks (
        user_id uuid NOT NULL,
        badge varchar(32) NOT NULL,
        cohort varchar(4) NOT NULL,
        unlocked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (user_id, badge),
        CONSTRAINT fk_fz_badge_unlocks_user_id_fz_profiles FOREIGN KEY (user_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT ck_fz_badge_unlocks_live CHECK (cohort = 'live'),
        CONSTRAINT ck_fz_badge_unlocks_badge CHECK (badge IN {BADGES})
    )
    """,
    """
    ALTER TABLE fz_battles ADD COLUMN rematch_of uuid
        CONSTRAINT fk_fz_battles_rematch_of_fz_battles REFERENCES fz_battles (id) ON DELETE SET NULL
    """,
    """
    CREATE TABLE qb_key_checks (
        question_id text NOT NULL REFERENCES qb_questions (question_id) ON DELETE CASCADE,
        content_hash text NOT NULL,
        method varchar(32) NOT NULL,
        single_defensible boolean NOT NULL,
        checked_by varchar(80) NOT NULL,
        checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (question_id, content_hash),
        CONSTRAINT ck_qb_key_checks_hash CHECK (content_hash ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    ALTER TABLE qb_sets DROP CONSTRAINT ck_qb_sets_mode,
        ADD CONSTRAINT ck_qb_sets_mode CHECK (mode IN ('adaptive', 'test', 'chat', 'battle', 'fz_qa', 'daily'))
    """,
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind",
    f"ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind CHECK (kind IN ({KINDS_0031}))",
)

DOWNGRADE_CHECKS = (
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM fz_daily_challenges) OR EXISTS (SELECT 1 FROM fz_xp_events)
           OR EXISTS (SELECT 1 FROM fz_badge_unlocks) OR EXISTS (SELECT 1 FROM qb_key_checks)
           OR EXISTS (SELECT 1 FROM fz_battles WHERE rematch_of IS NOT NULL)
           OR EXISTS (SELECT 1 FROM qb_sets WHERE mode = 'daily') THEN
            RAISE EXCEPTION '0031 downgrade refused: daily challenges, XP, badges, rematches or key checks exist'
                USING ERRCODE = 'check_violation';
        END IF;
    END $$
    """,
)

DOWNGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    """
    DELETE FROM user_notifications
    WHERE kind IN ('daily_available', 'daily_completed', 'xp_milestone', 'level_up', 'badge_unlocked')
    """,
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind",
    f"ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind CHECK (kind IN ({KINDS_0030}))",
    """
    ALTER TABLE qb_sets DROP CONSTRAINT ck_qb_sets_mode,
        ADD CONSTRAINT ck_qb_sets_mode CHECK (mode IN ('adaptive', 'test', 'chat', 'battle', 'fz_qa'))
    """,
    "DROP TABLE qb_key_checks",
    "ALTER TABLE fz_battles DROP COLUMN rematch_of",
    "DROP TABLE fz_badge_unlocks",
    "DROP TABLE fz_xp_events",
    "DROP TABLE fz_daily_attempts",
    "DROP TABLE fz_daily_challenges",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in (*DOWNGRADE_CHECKS, *DOWNGRADE):
        op.execute(statement)
