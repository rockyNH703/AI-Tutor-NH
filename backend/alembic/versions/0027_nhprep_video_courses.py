"""NHprep video courses: the catalogue, its study aids and an NHprep-only search index (app/nhprep).

Revision ID: 0027_nhprep_video_courses
Revises: 0026_qbank_certification
Create Date: 2026-11-19

- nhprep_sync_runs: one row per --apply or --purge of scripts/nhprep_import.py (a dry run writes nothing); the audit.
- nhprep_courses, _trainers, _course_trainers, _lessons, _lesson_items: the catalogue as exported (export v1).
  removed_at marks a course or lesson a complete export no longer lists; only --purge deletes catalogue rows.
- nhprep_lessons.video_*: the stable video reference, never a signed URL, `exp`, `sig` or token. video_embed_url is
  generated (canonical Bunny embed URL, NULL for NHprep's own MP4 host), so nothing can write a URL into it.
- nhprep_search_chunks: search text derived from a lesson (kind 'lesson': titles, alias, chapter outline) or from its
  items, with its vector and an english tsvector (title A, content B). NHprep's own index: document_chunks and
  knowledge_passages never hold NHprep text, so normal chat (and the release before this one) cannot read it.
- No quiz tables: NHprep's quizzes are served by the question bank (0026).
- No user data: no table references users (account deletion is unaffected).

Additive: no existing table changes. pg_trgm is shared with the question bank: created IF NOT EXISTS, never dropped.
"""

from alembic import op

revision = "0027_nhprep_video_courses"
down_revision = "0026_qbank_certification"
branch_labels = None
depends_on = None

# One statement per op.execute: asyncpg prepares each one, and refuses several at once.
UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    """
    CREATE TABLE nhprep_sync_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind varchar(10) NOT NULL,
        status varchar(12) NOT NULL DEFAULT 'running',
        operator varchar(80) NOT NULL,
        complete boolean,
        manifest_sha256 char(64),
        exporter_version varchar(40),
        schema_version smallint,
        embedding_model varchar(200),
        access_basis varchar(500),
        egress_basis varchar(500),
        reason varchar(500),
        report jsonb NOT NULL DEFAULT '{}'::jsonb,
        started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        finished_at timestamptz,
        CONSTRAINT ck_nhprep_sync_runs_kind CHECK (kind IN ('import', 'purge')),
        CONSTRAINT ck_nhprep_sync_runs_status CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted')),
        CONSTRAINT ck_nhprep_sync_runs_finished CHECK ((status = 'running') = (finished_at IS NULL)),
        CONSTRAINT ck_nhprep_sync_runs_operator CHECK (operator ~ '^[a-z0-9][a-z0-9_.-]{0,79}$'),
        CONSTRAINT ck_nhprep_sync_runs_import CHECK (kind <> 'import' OR (complete IS NOT NULL
            AND manifest_sha256 IS NOT NULL AND manifest_sha256 ~ '^[0-9a-f]{64}$'
            AND exporter_version IS NOT NULL AND schema_version IS NOT NULL AND access_basis IS NOT NULL)),
        CONSTRAINT ck_nhprep_sync_runs_purge CHECK (kind <> 'purge' OR reason IS NOT NULL),
        CONSTRAINT ck_nhprep_sync_runs_egress_basis CHECK (embedding_model IS NULL OR egress_basis IS NOT NULL),
        CONSTRAINT ck_nhprep_sync_runs_report CHECK (jsonb_typeof(report) = 'object')
    )
    """,
    """
    CREATE TABLE nhprep_courses (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        external_id varchar(200) NOT NULL CONSTRAINT uq_nhprep_courses_external_id UNIQUE,
        slug varchar(200) NOT NULL,
        title varchar(300) NOT NULL,
        description text NOT NULL DEFAULT '',
        track varchar(20),
        content_hash char(64) NOT NULL,
        removed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_nhprep_courses_slug CHECK (slug ~ '^[a-z0-9]+([_-]+[a-z0-9]+)*$'),
        CONSTRAINT ck_nhprep_courses_track CHECK (track IN ('ccna', 'ccnp', 'ccie', 'security', 'cloud', 'ai', 'other')),
        CONSTRAINT ck_nhprep_courses_content_hash CHECK (content_hash ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    CREATE TABLE nhprep_trainers (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        normalized_name varchar(120) NOT NULL CONSTRAINT uq_nhprep_trainers_normalized_name UNIQUE,
        display_name varchar(120) NOT NULL,
        aliases varchar(120)[] NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_nhprep_trainers_normalized_name CHECK (normalized_name ~ '^[a-z][a-z .-]{0,119}$'),
        CONSTRAINT ck_nhprep_trainers_aliases CHECK (cardinality(aliases) <= 20)
    )
    """,
    """
    CREATE TABLE nhprep_course_trainers (
        course_id uuid NOT NULL REFERENCES nhprep_courses(id) ON DELETE CASCADE,
        trainer_id uuid NOT NULL REFERENCES nhprep_trainers(id) ON DELETE CASCADE,
        provenance varchar(20) NOT NULL,
        PRIMARY KEY (course_id, trainer_id),
        CONSTRAINT ck_nhprep_course_trainers_provenance CHECK (provenance IN ('api', 'title', 'description', 'slug'))
    )
    """,
    "CREATE INDEX ix_nhprep_course_trainers_trainer_id ON nhprep_course_trainers (trainer_id)",
    """
    CREATE TABLE nhprep_lessons (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        external_id uuid NOT NULL CONSTRAINT uq_nhprep_lessons_external_id UNIQUE,
        course_id uuid NOT NULL REFERENCES nhprep_courses(id) ON DELETE CASCADE,
        position smallint NOT NULL,
        source_day smallint,
        title varchar(300) NOT NULL,
        slug varchar(200) NOT NULL,
        duration_seconds integer,
        video_provider text,
        video_id uuid,
        video_library_id integer,
        video_embed_url text GENERATED ALWAYS AS (CASE WHEN video_provider = 'bunny'
            THEN 'https://player.mediadelivery.net/embed/' || video_library_id::text || '/' || video_id::text
            END) STORED,
        availability jsonb NOT NULL DEFAULT '{}'::jsonb,
        content_hash char(64) NOT NULL,
        removed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_nhprep_lessons_id_course_id UNIQUE (id, course_id),
        CONSTRAINT ck_nhprep_lessons_position CHECK (position >= 1),
        CONSTRAINT ck_nhprep_lessons_source_day CHECK (source_day BETWEEN 0 AND 999),
        CONSTRAINT ck_nhprep_lessons_slug CHECK (slug ~ '^[a-z0-9]+([_-]+[a-z0-9]+)*$'),
        CONSTRAINT ck_nhprep_lessons_duration CHECK (duration_seconds >= 0),
        CONSTRAINT ck_nhprep_lessons_video_provider CHECK (video_provider IN ('nhprep-mp4', 'bunny')),
        CONSTRAINT ck_nhprep_lessons_video CHECK ((video_provider IS NULL) = (video_id IS NULL)
            AND (video_library_id IS NOT NULL) = (video_provider IS NOT DISTINCT FROM 'bunny')),
        CONSTRAINT ck_nhprep_lessons_video_library_id CHECK (video_library_id > 0),
        CONSTRAINT ck_nhprep_lessons_availability CHECK (jsonb_typeof(availability) = 'object'),
        CONSTRAINT ck_nhprep_lessons_content_hash CHECK (content_hash ~ '^[0-9a-f]{64}$')
    )
    """,
    "CREATE INDEX ix_nhprep_lessons_course_id_position ON nhprep_lessons (course_id, position)",
    "CREATE INDEX ix_nhprep_lessons_title_trgm ON nhprep_lessons USING gin (title gin_trgm_ops)",
    """
    CREATE TABLE nhprep_lesson_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        lesson_id uuid NOT NULL REFERENCES nhprep_lessons(id) ON DELETE CASCADE,
        kind varchar(20) NOT NULL,
        item_key varchar(100) NOT NULL,
        position integer NOT NULL,
        title varchar(300) NOT NULL DEFAULT '',
        body_md text NOT NULL DEFAULT '',
        start_seconds integer,
        data jsonb NOT NULL DEFAULT '{}'::jsonb,
        content_hash char(64) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_nhprep_lesson_items_lesson_id_kind_item_key UNIQUE (lesson_id, kind, item_key),
        CONSTRAINT uq_nhprep_lesson_items_id_lesson_id UNIQUE (id, lesson_id),
        CONSTRAINT ck_nhprep_lesson_items_kind
            CHECK (kind IN ('note', 'chapter', 'flashcard', 'cheat_sheet', 'interview_question', 'other')),
        CONSTRAINT ck_nhprep_lesson_items_item_key CHECK (item_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$'),
        CONSTRAINT ck_nhprep_lesson_items_position CHECK (position >= 0),
        CONSTRAINT ck_nhprep_lesson_items_start_seconds
            CHECK (start_seconds IS NULL OR (kind = 'chapter' AND start_seconds BETWEEN 0 AND 86400)),
        CONSTRAINT ck_nhprep_lesson_items_body_md CHECK (length(body_md) <= 200000),
        CONSTRAINT ck_nhprep_lesson_items_data CHECK (jsonb_typeof(data) = 'object'),
        CONSTRAINT ck_nhprep_lesson_items_content_hash CHECK (content_hash ~ '^[0-9a-f]{64}$')
    )
    """,
    "CREATE INDEX ix_nhprep_lesson_items_chapter_title_trgm ON nhprep_lesson_items "
    "USING gin (title gin_trgm_ops) WHERE kind = 'chapter'",
    """
    CREATE TABLE nhprep_search_chunks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        lesson_id uuid NOT NULL,
        course_id uuid NOT NULL,
        item_id uuid,
        kind varchar(20) NOT NULL,
        ordinal smallint NOT NULL,
        title text NOT NULL,
        content text NOT NULL,
        content_tsv tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('english'::regconfig, title), 'A')
            || setweight(to_tsvector('english'::regconfig, content), 'B')
        ) STORED NOT NULL,
        embedding vector(1536) NOT NULL,
        embedding_model varchar(200) NOT NULL,
        content_hash char(64) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (lesson_id, course_id) REFERENCES nhprep_lessons (id, course_id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (item_id, lesson_id) REFERENCES nhprep_lesson_items (id, lesson_id) ON DELETE CASCADE,
        CONSTRAINT uq_nhprep_search_chunks_lesson_id_content_hash UNIQUE (lesson_id, content_hash),
        CONSTRAINT ck_nhprep_search_chunks_kind
            CHECK (kind IN ('lesson', 'note', 'flashcard', 'cheat_sheet', 'interview_question', 'other')),
        CONSTRAINT ck_nhprep_search_chunks_item CHECK ((kind = 'lesson') = (item_id IS NULL)),
        CONSTRAINT ck_nhprep_search_chunks_ordinal CHECK (ordinal >= 0),
        CONSTRAINT ck_nhprep_search_chunks_title CHECK (length(title) BETWEEN 1 AND 700),
        CONSTRAINT ck_nhprep_search_chunks_content CHECK (length(content) BETWEEN 1 AND 4000),
        CONSTRAINT ck_nhprep_search_chunks_content_hash CHECK (content_hash ~ '^[0-9a-f]{64}$')
    )
    """,
    # HNSW with pgvector's defaults (m = 16, ef_construction = 64), like 0025; the finder sets ef_search.
    "CREATE INDEX ix_nhprep_search_chunks_embedding_hnsw ON nhprep_search_chunks USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX ix_nhprep_search_chunks_content_tsv_gin ON nhprep_search_chunks USING gin (content_tsv)",
    "CREATE INDEX ix_nhprep_search_chunks_course_id_kind ON nhprep_search_chunks (course_id, kind)",
    "CREATE INDEX ix_nhprep_search_chunks_item_id ON nhprep_search_chunks (item_id) WHERE item_id IS NOT NULL",
)

# The imported catalogue and its audit trail would be lost: refused before any DDL. After --purge only
# nhprep_sync_runs remains (kept on purpose), so the downgrade stays refused until an operator deletes it.
DOWNGRADE_CHECKS = (
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM nhprep_sync_runs) OR EXISTS (SELECT 1 FROM nhprep_courses)
           OR EXISTS (SELECT 1 FROM nhprep_trainers) THEN
            RAISE EXCEPTION '0027 downgrade refused: NHprep imports or catalogue rows exist'
                USING ERRCODE = 'check_violation';
        END IF;
    END $$
    """,
)

DOWNGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "DROP TABLE nhprep_search_chunks",
    "DROP TABLE nhprep_lesson_items",
    "DROP TABLE nhprep_lessons",
    "DROP TABLE nhprep_course_trainers",
    "DROP TABLE nhprep_trainers",
    "DROP TABLE nhprep_courses",
    "DROP TABLE nhprep_sync_runs",
    # pg_trgm stays: extensions are left in place (0001_initial.py), and the question bank (0026) uses it.
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in (*DOWNGRADE_CHECKS, *DOWNGRADE):
        op.execute(statement)
