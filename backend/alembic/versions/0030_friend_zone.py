"""NH Friend Zone, Phase 1: profiles, friends, requests, blocks, reports, moderation audit and 1v1 battles.

Revision ID: 0030_friend_zone
Revises: 0029_nh_program_curriculum
Create Date: 2026-11-24

Plan: owner-approved Friend Zone plan, revision 3 (sections 6, 8 and 9).
- fz_profiles: one per member. `id` is the public player id; users.id never leaves the API. `cohort` ('live' or
  'qa') is fixed at join (trigger) and every relationship row carries it through composite foreign keys to
  (user_id, cohort), so a QA account and a real student can never be linked, whatever the code does.
- Relationship tables reference fz_profiles, not users: deleting an account cascades through the profile.
- fz_battles has no user column (account deletion never locks a battle row); its participants are
  fz_battle_players, whose (user_id, cohort) becomes NULL when the member goes, keeping the history anonymous.
  `pair_key` exists only while a battle is open (one open battle per pair).
- qb_sets.mode gains 'battle' (real students) and 'fz_qa' (QA accounts, kept out of practice records).
- user_notifications.kind gains the five Phase 1 Friend Zone kinds.

Additive: new tables and wider CHECKs only, so the previous release keeps working on this schema. The downgrade is
refused while any Friend Zone row or battle set exists.
"""

from alembic import op

revision = "0030_friend_zone"
down_revision = "0029_nh_program_curriculum"
branch_labels = None
depends_on = None

KINDS_0029 = (
    "'streak_milestone', 'streak_reminder', 'daily_reminder', 'weekly_summary', 'security_sign_in', "
    "'security_password_changed', 'security_google_linked', 'security_two_factor_on', 'security_two_factor_off'"
)
KINDS_0030 = KINDS_0029 + ", 'friend_request', 'friend_accepted', 'battle_invite', 'battle_accepted', 'battle_finished'"
OPEN = "('invited', 'waiting', 'active')"

# One statement per op.execute: asyncpg prepares each one, and refuses several at once.
UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    """
    CREATE TABLE fz_profiles (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL CONSTRAINT uq_fz_profiles_user_id UNIQUE REFERENCES users (id) ON DELETE CASCADE,
        cohort varchar(4) NOT NULL,
        display_name varchar(30) NOT NULL,
        discoverable boolean NOT NULL DEFAULT false,
        show_program boolean NOT NULL DEFAULT false,
        show_stats boolean NOT NULL DEFAULT false,
        version integer NOT NULL DEFAULT 0,
        suspended_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_fz_profiles_user_id_cohort UNIQUE (user_id, cohort),
        CONSTRAINT ck_fz_profiles_cohort CHECK (cohort IN ('live', 'qa')),
        CONSTRAINT ck_fz_profiles_display_name CHECK (char_length(display_name) BETWEEN 2 AND 30)
    )
    """,
    """
    CREATE FUNCTION fz_profiles_cohort_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.cohort IS DISTINCT FROM OLD.cohort THEN
            RAISE EXCEPTION 'a Friend Zone cohort never changes' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER fz_profiles_cohort_fixed BEFORE UPDATE OF cohort ON fz_profiles
        FOR EACH ROW EXECUTE FUNCTION fz_profiles_cohort_fixed()
    """,
    """
    CREATE TABLE fz_friend_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        sender_id uuid NOT NULL,
        recipient_id uuid NOT NULL,
        cohort varchar(4) NOT NULL,
        status varchar(10) NOT NULL DEFAULT 'pending',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        responded_at timestamptz,
        CONSTRAINT fk_fz_friend_requests_sender_id_fz_profiles FOREIGN KEY (sender_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT fk_fz_friend_requests_recipient_id_fz_profiles FOREIGN KEY (recipient_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT ck_fz_friend_requests_not_self CHECK (sender_id <> recipient_id),
        CONSTRAINT ck_fz_friend_requests_status
            CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled', 'expired'))
    )
    """,
    """
    CREATE UNIQUE INDEX uq_fz_friend_requests_pending_pair ON fz_friend_requests
        (least(sender_id, recipient_id), greatest(sender_id, recipient_id)) WHERE status = 'pending'
    """,
    "CREATE INDEX ix_fz_friend_requests_recipient_id ON fz_friend_requests (recipient_id) WHERE status = 'pending'",
    "CREATE INDEX ix_fz_friend_requests_sender_id ON fz_friend_requests (sender_id) WHERE status = 'pending'",
    """
    CREATE TABLE fz_friendships (
        user_low uuid NOT NULL,
        user_high uuid NOT NULL,
        cohort varchar(4) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_fz_friendships PRIMARY KEY (user_low, user_high),
        CONSTRAINT fk_fz_friendships_user_low_fz_profiles FOREIGN KEY (user_low, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT fk_fz_friendships_user_high_fz_profiles FOREIGN KEY (user_high, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT ck_fz_friendships_ordered CHECK (user_low < user_high)
    )
    """,
    "CREATE INDEX ix_fz_friendships_user_high ON fz_friendships (user_high)",
    """
    CREATE TABLE fz_blocks (
        blocker_id uuid NOT NULL,
        blocked_id uuid NOT NULL,
        cohort varchar(4) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT pk_fz_blocks PRIMARY KEY (blocker_id, blocked_id),
        CONSTRAINT fk_fz_blocks_blocker_id_fz_profiles FOREIGN KEY (blocker_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT fk_fz_blocks_blocked_id_fz_profiles FOREIGN KEY (blocked_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT ck_fz_blocks_not_self CHECK (blocker_id <> blocked_id)
    )
    """,
    "CREATE INDEX ix_fz_blocks_blocked_id ON fz_blocks (blocked_id)",
    """
    CREATE TABLE fz_battles (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        cohort varchar(4) NOT NULL,
        program_id uuid NOT NULL REFERENCES programs (id) ON DELETE RESTRICT,
        module_id uuid NOT NULL,
        topic_id text REFERENCES qb_topics (topic_id),
        difficulty varchar(6) NOT NULL,
        length smallint NOT NULL,
        question_ids text[],
        seed uuid NOT NULL DEFAULT gen_random_uuid(),
        pair_key text,
        status varchar(10) NOT NULL DEFAULT 'invited',
        end_reason varchar(24),
        result varchar(8),
        expires_at timestamptz NOT NULL,
        starts_at timestamptz,
        ends_at timestamptz,
        round smallint NOT NULL DEFAULT 0,
        ended_at timestamptz,
        version integer NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_fz_battles_id_cohort UNIQUE (id, cohort),
        CONSTRAINT fk_fz_battles_module_id_program_modules FOREIGN KEY (module_id, program_id)
            REFERENCES program_modules (id, program_id) ON DELETE RESTRICT,
        CONSTRAINT ck_fz_battles_cohort CHECK (cohort IN ('live', 'qa')),
        CONSTRAINT ck_fz_battles_difficulty CHECK (difficulty IN ('mixed', 'easy', 'medium', 'hard')),
        CONSTRAINT ck_fz_battles_length CHECK (length IN (5, 10)),
        CONSTRAINT ck_fz_battles_status
            CHECK (status IN ('invited', 'waiting', 'active', 'completed', 'declined', 'expired', 'cancelled')),
        CONSTRAINT ck_fz_battles_result CHECK (result IS NULL OR result IN ('points', 'forfeit', 'draw', 'void')),
        CONSTRAINT ck_fz_battles_pair_key_open CHECK ((pair_key IS NOT NULL) = (status IN ('invited', 'waiting', 'active'))),
        CONSTRAINT ck_fz_battles_question_ids
            CHECK (question_ids IS NULL OR cardinality(question_ids) = length),
        CONSTRAINT ck_fz_battles_questions_set
            CHECK (status NOT IN ('active', 'completed') OR question_ids IS NOT NULL),
        CONSTRAINT ck_fz_battles_round CHECK (round BETWEEN 0 AND length)
    )
    """,
    f"CREATE UNIQUE INDEX uq_fz_battles_open_pair ON fz_battles (pair_key) WHERE status IN {OPEN}",
    """
    CREATE TABLE fz_battle_players (
        battle_id uuid NOT NULL,
        role varchar(10) NOT NULL,
        cohort varchar(4),
        user_id uuid,
        request_id uuid,
        set_id uuid REFERENCES qb_sets (id) ON DELETE SET NULL,
        ready_at timestamptz,
        missed_in_row smallint NOT NULL DEFAULT 0,
        score integer NOT NULL DEFAULT 0,
        correct smallint NOT NULL DEFAULT 0,
        answered smallint NOT NULL DEFAULT 0,
        answer_ms integer NOT NULL DEFAULT 0,
        winner boolean NOT NULL DEFAULT false,
        CONSTRAINT pk_fz_battle_players PRIMARY KEY (battle_id, role),
        CONSTRAINT fk_fz_battle_players_battle_id_fz_battles FOREIGN KEY (battle_id)
            REFERENCES fz_battles (id) ON DELETE CASCADE,
        CONSTRAINT fk_fz_battle_players_battle_cohort FOREIGN KEY (battle_id, cohort)
            REFERENCES fz_battles (id, cohort),
        CONSTRAINT fk_fz_battle_players_user_id_fz_profiles FOREIGN KEY (user_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE SET NULL,
        CONSTRAINT uq_fz_battle_players_battle_id_user_id UNIQUE (battle_id, user_id),
        CONSTRAINT ck_fz_battle_players_role CHECK (role IN ('challenger', 'opponent')),
        CONSTRAINT ck_fz_battle_players_member CHECK ((user_id IS NULL) = (cohort IS NULL))
    )
    """,
    """
    CREATE UNIQUE INDEX uq_fz_battle_players_request ON fz_battle_players (user_id, request_id)
        WHERE request_id IS NOT NULL
    """,
    "CREATE INDEX ix_fz_battle_players_user_id ON fz_battle_players (user_id, battle_id)",
    """
    CREATE TABLE fz_battle_rounds (
        battle_id uuid NOT NULL REFERENCES fz_battles (id) ON DELETE CASCADE,
        round smallint NOT NULL,
        question_id text NOT NULL REFERENCES qb_questions (question_id),
        seconds smallint NOT NULL,
        opened_at timestamptz NOT NULL,
        closes_at timestamptz NOT NULL,
        closed_at timestamptz,
        voided boolean NOT NULL DEFAULT false,
        CONSTRAINT pk_fz_battle_rounds PRIMARY KEY (battle_id, round),
        CONSTRAINT ck_fz_battle_rounds_seconds CHECK (seconds BETWEEN 5 AND 90),
        CONSTRAINT ck_fz_battle_rounds_window CHECK (closes_at > opened_at)
    )
    """,
    """
    CREATE TABLE fz_reports (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        reporter_id uuid NOT NULL,
        reported_id uuid NOT NULL,
        cohort varchar(4) NOT NULL,
        battle_id uuid REFERENCES fz_battles (id),
        reason varchar(20) NOT NULL,
        details varchar(1000),
        status varchar(10) NOT NULL DEFAULT 'open',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        reviewed_at timestamptz,
        CONSTRAINT fk_fz_reports_reporter_id_fz_profiles FOREIGN KEY (reporter_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT fk_fz_reports_reported_id_fz_profiles FOREIGN KEY (reported_id, cohort)
            REFERENCES fz_profiles (user_id, cohort) ON DELETE CASCADE,
        CONSTRAINT ck_fz_reports_not_self CHECK (reporter_id <> reported_id),
        CONSTRAINT ck_fz_reports_reason
            CHECK (reason IN ('harassment', 'cheating', 'inappropriate_name', 'spam', 'other')),
        CONSTRAINT ck_fz_reports_status CHECK (status IN ('open', 'reviewed', 'actioned', 'dismissed'))
    )
    """,
    "CREATE INDEX ix_fz_reports_status_created_at ON fz_reports (status, created_at)",
    """
    CREATE TABLE fz_moderation_actions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        report_id uuid REFERENCES fz_reports (id) ON DELETE SET NULL,
        target_user_id uuid REFERENCES fz_profiles (user_id) ON DELETE SET NULL,
        action varchar(20) NOT NULL,
        note varchar(500),
        actor varchar(40) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_fz_moderation_actions_action
            CHECK (action IN ('review', 'resolve', 'dismiss', 'suspend', 'unsuspend', 'cancel_battle', 'qa_cleanup'))
    )
    """,
    """
    ALTER TABLE qb_sets DROP CONSTRAINT ck_qb_sets_mode,
        ADD CONSTRAINT ck_qb_sets_mode CHECK (mode IN ('adaptive', 'test', 'chat', 'battle', 'fz_qa'))
    """,
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind",
    f"ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind CHECK (kind IN ({KINDS_0030}))",
)

# Members, friendships, battles (and the practice sets they made) would be lost: refused before any DDL.
DOWNGRADE_CHECKS = (
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM fz_profiles) OR EXISTS (SELECT 1 FROM fz_battles)
           OR EXISTS (SELECT 1 FROM qb_sets WHERE mode IN ('battle', 'fz_qa')) THEN
            RAISE EXCEPTION '0030 downgrade refused: Friend Zone members, battles or battle sets exist'
                USING ERRCODE = 'check_violation';
        END IF;
    END $$
    """,
)

DOWNGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    """
    DELETE FROM user_notifications
    WHERE kind IN ('friend_request', 'friend_accepted', 'battle_invite', 'battle_accepted', 'battle_finished')
    """,
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind",
    f"ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind CHECK (kind IN ({KINDS_0029}))",
    """
    ALTER TABLE qb_sets DROP CONSTRAINT ck_qb_sets_mode,
        ADD CONSTRAINT ck_qb_sets_mode CHECK (mode IN ('adaptive', 'test', 'chat'))
    """,
    "DROP TABLE fz_moderation_actions",
    "DROP TABLE fz_reports",
    "DROP TABLE fz_battle_rounds",
    "DROP TABLE fz_battle_players",
    "DROP TABLE fz_battles",
    "DROP TABLE fz_blocks",
    "DROP TABLE fz_friendships",
    "DROP TABLE fz_friend_requests",
    "DROP TRIGGER fz_profiles_cohort_fixed ON fz_profiles",
    "DROP FUNCTION fz_profiles_cohort_fixed()",
    "DROP TABLE fz_profiles",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in (*DOWNGRADE_CHECKS, *DOWNGRADE):
        op.execute(statement)
