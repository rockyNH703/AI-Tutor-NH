"""The certification question bank: the qb_* tables also hold NetSec tracks, multiple-correct questions,
and the topic search the bank turn uses (app/qbank/finder.py, docs/question_bank.md).

Revision ID: 0026_qbank_certification
Revises: 0025_knowledge_canonical
Create Date: 2026-11-19

The NetSec bank uses the same tables, with these meanings:
- curriculum 'netsec': a book is a certification track; class_level is its tier (1 associate, 2 professional,
  3 expert); a chapter is an exam domain; a topic is a canonical topic that NHprep lessons map to.
- A question has one of two shapes:
  - the CBSE one: option_a..d and correct_option;
  - the new one: options (2-8, jsonb) and correct_keys (letters).
  Format 'mcq' has one key; 'multi_select' has at least 2 keys and fewer keys than options.
- lesson_ref names the NHprep lesson a question was written from, with no foreign key: NHprep's catalog
  is a separate feature. key_origin records whether the key came from NHprep or was written.
- qb_topics: aliases, plus search_title and search_doc (written by the importer), a weighted generated
  tsvector, and trigram indexes (pg_trgm).
- qb_set_items.chosen_keys holds the answer to a new-shape question. The grading trigger compares sets:
  coalesce(correct_keys, {correct_option}) against coalesce(chosen_keys, {chosen}).

Additive and backward compatible:
- CBSE rows stay valid: every relaxed CHECK still holds for them.
- The previous release never reaches a netsec row: its menu, practice record and filters all ask for
  classes 6-12.
- Its inserts still pass: the new columns are nullable or have defaults.

The downgrade refuses while netsec content or new-shape answers exist (it would make them invalid).
pg_trgm is shared with the NHprep catalog, so it is created IF NOT EXISTS and never dropped.
"""

from alembic import op

revision = "0026_qbank_certification"
down_revision = "0025_knowledge_canonical"
branch_labels = None
depends_on = None

# 0017's answer function, exactly: what the downgrade restores.
ANSWER_0017 = """
    CREATE OR REPLACE FUNCTION qb_set_items_answer() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.chosen IS NOT NULL OR NEW.is_correct IS NOT NULL OR NEW.answered_at IS NOT NULL THEN
                RAISE EXCEPTION 'a question is served unanswered' USING ERRCODE = 'check_violation';
            END IF;
            NEW.served_hash := (SELECT q.content_hash FROM qb_questions q WHERE q.question_id = NEW.question_id);
            RETURN NEW;
        END IF;
        IF OLD.answered_at IS NOT NULL THEN
            RAISE EXCEPTION 'set item %/% is already answered', OLD.set_id, OLD.position
                USING ERRCODE = 'check_violation';
        END IF;
        IF (NEW.set_id, NEW.position, NEW.user_id, NEW.question_id, NEW.topic_id, NEW.served_at, NEW.served_hash)
           IS DISTINCT FROM
           (OLD.set_id, OLD.position, OLD.user_id, OLD.question_id, OLD.topic_id, OLD.served_at, OLD.served_hash) THEN
            RAISE EXCEPTION 'a served question cannot change' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.chosen IS NOT NULL THEN
            NEW.is_correct := (SELECT q.correct_option = NEW.chosen FROM qb_questions q
                               WHERE q.question_id = NEW.question_id);
            NEW.answered_at := clock_timestamp();
        ELSE
            NEW.is_correct := NULL;
            NEW.answered_at := NULL;
        END IF;
        RETURN NEW;
    END $$
"""

# One statement per op.execute: asyncpg prepares each one, and refuses several at once.
UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    # A new-shape question's options and keys: 2-8 distinct non-empty options, distinct keys among the first
    # letters, one key for 'mcq', 2 <= keys < options for 'multi_select'. IMMUTABLE: it reads only its arguments.
    """
    CREATE FUNCTION qb_options_valid(options jsonb, keys text[], question_format text) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS $$
        SELECT jsonb_typeof(options) = 'array'
           AND jsonb_array_length(options) BETWEEN 2 AND 8
           AND (SELECT bool_and(jsonb_typeof(o) = 'string' AND length(btrim(o #>> '{}')) BETWEEN 1 AND 500)
                     AND count(DISTINCT lower(btrim(o #>> '{}'))) = jsonb_array_length(options)
                FROM jsonb_array_elements(options) AS o)
           AND cardinality(keys) >= 1
           AND (SELECT count(DISTINCT k) FROM unnest(keys) AS k) = cardinality(keys)
           AND keys <@ (ARRAY['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'])[1:jsonb_array_length(options)]
           AND CASE question_format
                   WHEN 'mcq' THEN cardinality(keys) = 1
                   WHEN 'multi_select' THEN cardinality(keys) BETWEEN 2 AND jsonb_array_length(options) - 1
                   ELSE false
               END
    $$
    """,
    """
    ALTER TABLE qb_syllabus_versions DROP CONSTRAINT ck_qb_syllabus_versions_curriculum,
        ADD CONSTRAINT ck_qb_syllabus_versions_curriculum CHECK (curriculum IN ('cbse', 'netsec'))
    """,
    """
    ALTER TABLE qb_books DROP CONSTRAINT ck_qb_books_curriculum,
        ADD CONSTRAINT ck_qb_books_curriculum CHECK (curriculum IN ('cbse', 'netsec')),
        DROP CONSTRAINT ck_qb_books_class_level,
        ADD CONSTRAINT ck_qb_books_class_level CHECK (
            (curriculum = 'cbse' AND class_level BETWEEN 6 AND 12)
            OR (curriculum = 'netsec' AND class_level BETWEEN 1 AND 3))
    """,
    """
    ALTER TABLE qb_chapters DROP CONSTRAINT ck_qb_chapters_max_questions,
        ADD CONSTRAINT ck_qb_chapters_max_questions CHECK (max_questions BETWEEN 1 AND 5000)
    """,
    """
    ALTER TABLE qb_topics
        ADD COLUMN aliases text[] NOT NULL DEFAULT '{}',
        ADD COLUMN lessons jsonb NOT NULL DEFAULT '[]',
        ADD COLUMN search_title text NOT NULL DEFAULT '',
        ADD COLUMN search_doc text NOT NULL DEFAULT '',
        ADD COLUMN search_tsv tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('english'::regconfig, search_title), 'A')
            || setweight(to_tsvector('english'::regconfig, search_doc), 'B')) STORED,
        ADD CONSTRAINT ck_qb_topics_lessons CHECK (jsonb_typeof(lessons) = 'array')
    """,
    "CREATE INDEX ix_qb_topics_search_tsv ON qb_topics USING gin (search_tsv)",
    "CREATE INDEX ix_qb_topics_search_title_trgm ON qb_topics USING gin (search_title gin_trgm_ops)",
    "CREATE INDEX ix_qb_topics_aliases ON qb_topics USING gin (aliases)",
    """
    ALTER TABLE qb_questions
        ALTER COLUMN option_a DROP NOT NULL,
        ALTER COLUMN option_b DROP NOT NULL,
        ALTER COLUMN option_c DROP NOT NULL,
        ALTER COLUMN option_d DROP NOT NULL,
        ALTER COLUMN correct_option DROP NOT NULL,
        ADD COLUMN options jsonb,
        ADD COLUMN correct_keys text[],
        ADD COLUMN lesson_ref text,
        ADD COLUMN key_origin text,
        DROP CONSTRAINT ck_qb_questions_question_format,
        ADD CONSTRAINT ck_qb_questions_question_format CHECK (
            question_format IN ('mcq', 'assertion_reason', 'case_based', 'source_based', 'multi_select')),
        DROP CONSTRAINT ck_qb_questions_source_kind,
        ADD CONSTRAINT ck_qb_questions_source_kind CHECK (
            source_kind IN ('original', 'ncert_exercise', 'cbse_official', 'nhprep_quiz', 'nhprep_lesson')),
        ADD CONSTRAINT ck_qb_questions_shape CHECK (
            (options IS NULL AND correct_keys IS NULL
             AND option_a IS NOT NULL AND option_b IS NOT NULL AND option_c IS NOT NULL AND option_d IS NOT NULL
             AND correct_option IS NOT NULL AND question_format <> 'multi_select')
            OR (options IS NOT NULL AND correct_keys IS NOT NULL
                AND option_a IS NULL AND option_b IS NULL AND option_c IS NULL AND option_d IS NULL
                AND correct_option IS NULL AND qb_options_valid(options, correct_keys, question_format))),
        ADD CONSTRAINT ck_qb_questions_key_origin CHECK (key_origin IN ('nhprep', 'authored')),
        ADD CONSTRAINT ck_qb_questions_lesson_ref CHECK (lesson_ref ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$'),
        ADD CONSTRAINT ck_qb_questions_explanation CHECK (length(btrim(explanation)) > 0)
    """,
    "CREATE INDEX ix_qb_questions_lesson_ref ON qb_questions (lesson_ref) WHERE lesson_ref IS NOT NULL",
    """
    ALTER TABLE qb_set_items
        ADD COLUMN chosen_keys text[],
        DROP CONSTRAINT ck_qb_set_items_answer,
        ADD CONSTRAINT ck_qb_set_items_answer CHECK (
            ((chosen IS NULL AND chosen_keys IS NULL) = (answered_at IS NULL))
            AND ((answered_at IS NULL) = (is_correct IS NULL))
            AND (chosen IS NULL OR chosen_keys IS NULL)),
        ADD CONSTRAINT ck_qb_set_items_chosen_keys CHECK (
            cardinality(chosen_keys) BETWEEN 1 AND 8
            AND chosen_keys <@ ARRAY['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'])
    """,
    # 0017's function with set grading: the old shape answers with chosen, the new one with chosen_keys
    # (a mismatch is refused); right = the same set of letters as the key.
    """
    CREATE OR REPLACE FUNCTION qb_set_items_answer() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        new_shape boolean;
        key text[];
        picked text[];
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.chosen IS NOT NULL OR NEW.chosen_keys IS NOT NULL OR NEW.is_correct IS NOT NULL
               OR NEW.answered_at IS NOT NULL THEN
                RAISE EXCEPTION 'a question is served unanswered' USING ERRCODE = 'check_violation';
            END IF;
            NEW.served_hash := (SELECT q.content_hash FROM qb_questions q WHERE q.question_id = NEW.question_id);
            RETURN NEW;
        END IF;
        IF OLD.answered_at IS NOT NULL THEN
            RAISE EXCEPTION 'set item %/% is already answered', OLD.set_id, OLD.position
                USING ERRCODE = 'check_violation';
        END IF;
        IF (NEW.set_id, NEW.position, NEW.user_id, NEW.question_id, NEW.topic_id, NEW.served_at, NEW.served_hash)
           IS DISTINCT FROM
           (OLD.set_id, OLD.position, OLD.user_id, OLD.question_id, OLD.topic_id, OLD.served_at, OLD.served_hash) THEN
            RAISE EXCEPTION 'a served question cannot change' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.chosen IS NULL AND NEW.chosen_keys IS NULL THEN
            NEW.is_correct := NULL;
            NEW.answered_at := NULL;
            RETURN NEW;
        END IF;
        SELECT q.correct_keys IS NOT NULL, coalesce(q.correct_keys, ARRAY[q.correct_option::text])
            INTO new_shape, key FROM qb_questions q WHERE q.question_id = NEW.question_id;
        IF new_shape <> (NEW.chosen_keys IS NOT NULL) THEN
            RAISE EXCEPTION 'answer % with %', NEW.question_id,
                CASE WHEN new_shape THEN 'chosen_keys' ELSE 'chosen' END USING ERRCODE = 'check_violation';
        END IF;
        picked := coalesce(NEW.chosen_keys, ARRAY[NEW.chosen::text]);
        NEW.is_correct := picked @> key AND picked <@ key;
        NEW.answered_at := clock_timestamp();
        RETURN NEW;
    END $$
    """,
)

DOWNGRADE_CHECKS = (
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM qb_books WHERE curriculum = 'netsec')
           OR EXISTS (SELECT 1 FROM qb_syllabus_versions WHERE curriculum = 'netsec')
           OR EXISTS (SELECT 1 FROM qb_questions WHERE options IS NOT NULL)
           OR EXISTS (SELECT 1 FROM qb_set_items WHERE chosen_keys IS NOT NULL)
           OR EXISTS (SELECT 1 FROM qb_chapters WHERE max_questions > 1000) THEN
            RAISE EXCEPTION '0026 downgrade refused: the certification bank or its answers exist'
                USING ERRCODE = 'check_violation';
        END IF;
    END $$
    """,
)

DOWNGRADE = (
    ANSWER_0017,
    """
    ALTER TABLE qb_set_items DROP CONSTRAINT ck_qb_set_items_chosen_keys,
        DROP CONSTRAINT ck_qb_set_items_answer,
        ADD CONSTRAINT ck_qb_set_items_answer
            CHECK ((chosen IS NULL) = (answered_at IS NULL) AND (chosen IS NULL) = (is_correct IS NULL)),
        DROP COLUMN chosen_keys
    """,
    "DROP INDEX ix_qb_questions_lesson_ref",
    """
    ALTER TABLE qb_questions
        DROP CONSTRAINT ck_qb_questions_explanation,
        DROP CONSTRAINT ck_qb_questions_lesson_ref,
        DROP CONSTRAINT ck_qb_questions_key_origin,
        DROP CONSTRAINT ck_qb_questions_shape,
        DROP CONSTRAINT ck_qb_questions_source_kind,
        ADD CONSTRAINT ck_qb_questions_source_kind CHECK (source_kind IN ('original', 'ncert_exercise', 'cbse_official')),
        DROP CONSTRAINT ck_qb_questions_question_format,
        ADD CONSTRAINT ck_qb_questions_question_format
            CHECK (question_format IN ('mcq', 'assertion_reason', 'case_based', 'source_based')),
        DROP COLUMN key_origin,
        DROP COLUMN lesson_ref,
        DROP COLUMN correct_keys,
        DROP COLUMN options,
        ALTER COLUMN correct_option SET NOT NULL,
        ALTER COLUMN option_d SET NOT NULL,
        ALTER COLUMN option_c SET NOT NULL,
        ALTER COLUMN option_b SET NOT NULL,
        ALTER COLUMN option_a SET NOT NULL
    """,
    "DROP INDEX ix_qb_topics_aliases",
    "DROP INDEX ix_qb_topics_search_title_trgm",
    "DROP INDEX ix_qb_topics_search_tsv",
    """
    ALTER TABLE qb_topics DROP CONSTRAINT ck_qb_topics_lessons, DROP COLUMN search_tsv, DROP COLUMN search_doc,
        DROP COLUMN search_title, DROP COLUMN lessons, DROP COLUMN aliases
    """,
    """
    ALTER TABLE qb_chapters DROP CONSTRAINT ck_qb_chapters_max_questions,
        ADD CONSTRAINT ck_qb_chapters_max_questions CHECK (max_questions BETWEEN 1 AND 1000)
    """,
    """
    ALTER TABLE qb_books DROP CONSTRAINT ck_qb_books_class_level,
        ADD CONSTRAINT ck_qb_books_class_level CHECK (class_level BETWEEN 6 AND 12),
        DROP CONSTRAINT ck_qb_books_curriculum,
        ADD CONSTRAINT ck_qb_books_curriculum CHECK (curriculum IN ('cbse'))
    """,
    """
    ALTER TABLE qb_syllabus_versions DROP CONSTRAINT ck_qb_syllabus_versions_curriculum,
        ADD CONSTRAINT ck_qb_syllabus_versions_curriculum CHECK (curriculum IN ('cbse'))
    """,
    "DROP FUNCTION qb_options_valid(jsonb, text[], text)",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in (*DOWNGRADE_CHECKS, *DOWNGRADE):
        op.execute(statement)
