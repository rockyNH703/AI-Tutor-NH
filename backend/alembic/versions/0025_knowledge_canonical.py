"""Courses, provider-egress approvals, book editions, canonical passages and an evidence-backed concept graph.

Revision ID: 0025_knowledge_canonical
Revises: 0024_knowledge_lifecycle
Create Date: 2026-11-05

One database, one index. The identity of a text (knowledge_passages, unique by normalized-text key, embedded once
per model) is separate from where it appears (knowledge_passage_occurrences: revision, page, section) and from who
may read it (knowledge_passage_sources: live in a source; sources carry rights and the course boundary).

- knowledge_courses + knowledge_sources.course: the logical course boundary (ccna, ccnp, ...); builds_on names the
  foundation course. NULL course = general material, eligible in every course.
- knowledge_sources.embed_egress_* / extract_egress_*: the operator's recorded approval to send a source's text to
  the embedding provider, and (separately) to a model for concept extraction.
- knowledge_editions: book and edition identity of a document (work key, edition, ISBN, exam codes, domain).
- knowledge_passages, _embeddings, _occurrences, _sources, _links: canonical content and its provenance.
- knowledge_page_fingerprints: per-page text identity of every file, for duplicate and edition detection.
- knowledge_concepts, _aliases, knowledge_passage_concepts, knowledge_relations: concepts, what each passage
  mentions, and the relations and values passages state, each with a verbatim evidence span.
- knowledge_duplicate_reviews.classification also accepts subset / superset (a combined book against a volume).

Additive and backward compatible: the old release never reads the new tables and every new column is nullable.
Its retriever reads document_chunks, so passages published by the new release stay invisible to it after a
rollback (documented in docs/rollback.md).
"""

from alembic import op

revision = "0025_knowledge_canonical"
down_revision = "0024_knowledge_lifecycle"
branch_labels = None
depends_on = None

CLASSIFICATIONS_0023 = (
    "'exact','confirmed_content','probable','same_edition_representation','different_edition','related','inconclusive'"
)
CLASSIFICATIONS = CLASSIFICATIONS_0023 + ",'subset','superset'"

UPGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    """
    CREATE TABLE knowledge_courses (
        slug varchar(40) PRIMARY KEY,
        name varchar(120) NOT NULL,
        level smallint NOT NULL DEFAULT 1,
        builds_on varchar(40) REFERENCES knowledge_courses(slug) ON DELETE SET NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_knowledge_courses_slug CHECK (slug ~ '^[a-z][a-z0-9_]{1,39}$'),
        CONSTRAINT ck_knowledge_courses_level CHECK (level BETWEEN 1 AND 9),
        CONSTRAINT ck_knowledge_courses_not_self CHECK (builds_on IS NULL OR builds_on <> slug)
    )
    """,
    """
    ALTER TABLE knowledge_sources
        ADD COLUMN course varchar(40) REFERENCES knowledge_courses(slug) ON DELETE RESTRICT,
        ADD COLUMN embed_egress_approved_at timestamptz,
        ADD COLUMN embed_egress_basis varchar(500),
        ADD COLUMN embed_egress_approved_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN extract_egress_approved_at timestamptz,
        ADD COLUMN extract_egress_basis varchar(500),
        ADD COLUMN extract_egress_approved_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
        ADD CONSTRAINT ck_knowledge_sources_embed_egress_basis
            CHECK (embed_egress_approved_at IS NULL OR embed_egress_basis IS NOT NULL),
        ADD CONSTRAINT ck_knowledge_sources_extract_egress_basis
            CHECK (extract_egress_approved_at IS NULL OR extract_egress_basis IS NOT NULL)
    """,
    "CREATE INDEX ix_knowledge_sources_course ON knowledge_sources (course)",
    """
    CREATE TABLE knowledge_editions (
        document_id uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
        source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
        work_key varchar(200) NOT NULL,
        title varchar(300) NOT NULL,
        edition_number smallint,
        edition_statement varchar(120),
        volume varchar(40),
        isbn13 varchar(13)[] NOT NULL DEFAULT '{}',
        publisher varchar(200),
        year smallint,
        exam_codes varchar(20)[] NOT NULL DEFAULT '{}',
        domain varchar(120),
        language varchar(16) NOT NULL DEFAULT 'en',
        metadata_source varchar(20) NOT NULL DEFAULT 'pdf',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_knowledge_editions_metadata_source CHECK (metadata_source IN ('pdf','title_page','operator'))
    )
    """,
    "CREATE INDEX ix_knowledge_editions_work_key ON knowledge_editions (work_key)",
    "CREATE INDEX ix_knowledge_editions_isbn13 ON knowledge_editions USING gin (isbn13)",
    """
    CREATE TABLE knowledge_passages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        content_key char(64) NOT NULL CONSTRAINT uq_knowledge_passages_content_key UNIQUE,
        content text NOT NULL,
        content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, content)) STORED NOT NULL,
        simhash bigint NOT NULL,
        char_count integer NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_knowledge_passages_char_count CHECK (char_count > 0)
    )
    """,
    "CREATE INDEX ix_knowledge_passages_content_tsv_gin ON knowledge_passages USING gin (content_tsv)",
    """
    CREATE TABLE knowledge_passage_embeddings (
        passage_id uuid NOT NULL REFERENCES knowledge_passages(id) ON DELETE CASCADE,
        embedding_model varchar(200) NOT NULL,
        embedding vector(1536) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (passage_id, embedding_model)
    )
    """,
    "CREATE INDEX ix_knowledge_passage_embeddings_hnsw ON knowledge_passage_embeddings "
    "USING hnsw (embedding vector_cosine_ops)",
    """
    CREATE TABLE knowledge_passage_occurrences (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        passage_id uuid NOT NULL REFERENCES knowledge_passages(id) ON DELETE RESTRICT,
        revision_id uuid NOT NULL,
        document_id uuid NOT NULL,
        source_id uuid NOT NULL,
        ordinal integer NOT NULL,
        stable_chunk_key char(64) NOT NULL,
        physical_page_start integer NOT NULL,
        physical_page_end integer NOT NULL,
        printed_page_label varchar(40),
        section_path varchar(300)[] NOT NULL DEFAULT '{}',
        element_type varchar(20) NOT NULL DEFAULT 'paragraph',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY (revision_id, document_id, source_id)
            REFERENCES document_revisions(id, document_id, source_id) ON DELETE CASCADE,
        CONSTRAINT uq_knowledge_passage_occurrences_ordinal UNIQUE (revision_id, ordinal),
        CONSTRAINT ck_knowledge_passage_occurrences_pages
            CHECK (physical_page_start >= 1 AND physical_page_end >= physical_page_start)
    )
    """,
    "CREATE INDEX ix_knowledge_passage_occurrences_passage ON knowledge_passage_occurrences (passage_id)",
    "CREATE INDEX ix_knowledge_passage_occurrences_source ON knowledge_passage_occurrences (source_id, passage_id)",
    """
    CREATE TABLE knowledge_passage_sources (
        source_id uuid NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
        passage_id uuid NOT NULL REFERENCES knowledge_passages(id) ON DELETE CASCADE,
        PRIMARY KEY (source_id, passage_id)
    )
    """,
    "CREATE INDEX ix_knowledge_passage_sources_passage ON knowledge_passage_sources (passage_id)",
    """
    CREATE TABLE knowledge_passage_links (
        passage_a uuid NOT NULL REFERENCES knowledge_passages(id) ON DELETE CASCADE,
        passage_b uuid NOT NULL REFERENCES knowledge_passages(id) ON DELETE CASCADE,
        kind varchar(30) NOT NULL,
        distance smallint NOT NULL,
        PRIMARY KEY (passage_a, passage_b, kind),
        CONSTRAINT ck_knowledge_passage_links_kind CHECK (kind IN ('near_duplicate','revised_in_edition')),
        CONSTRAINT ck_knowledge_passage_links_order CHECK (passage_a < passage_b)
    )
    """,
    "CREATE INDEX ix_knowledge_passage_links_b ON knowledge_passage_links (passage_b)",
    """
    CREATE TABLE knowledge_page_fingerprints (
        file_id uuid NOT NULL REFERENCES document_files(id) ON DELETE CASCADE,
        physical_page integer NOT NULL,
        page_key char(64) NOT NULL,
        char_count integer NOT NULL,
        page_class varchar(20) NOT NULL DEFAULT 'body',
        PRIMARY KEY (file_id, physical_page),
        CONSTRAINT ck_knowledge_page_fingerprints_page_class CHECK (page_class IN ('body','front','back'))
    )
    """,
    "CREATE INDEX ix_knowledge_page_fingerprints_page_key ON knowledge_page_fingerprints (page_key)",
    """
    CREATE TABLE knowledge_concepts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        key varchar(80) NOT NULL CONSTRAINT uq_knowledge_concepts_key UNIQUE,
        name varchar(160) NOT NULL,
        kind varchar(30) NOT NULL,
        origin varchar(20) NOT NULL DEFAULT 'vocabulary',
        status varchar(20) NOT NULL DEFAULT 'active',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_knowledge_concepts_origin CHECK (origin IN ('vocabulary','learned')),
        CONSTRAINT ck_knowledge_concepts_status CHECK (status IN ('active','candidate'))
    )
    """,
    """
    CREATE TABLE knowledge_concept_aliases (
        alias varchar(160) PRIMARY KEY,
        concept_id uuid NOT NULL REFERENCES knowledge_concepts(id) ON DELETE CASCADE,
        origin varchar(20) NOT NULL DEFAULT 'vocabulary',
        evidence_passage_id uuid REFERENCES knowledge_passages(id) ON DELETE SET NULL,
        CONSTRAINT ck_knowledge_concept_aliases_origin CHECK (origin IN ('vocabulary','learned'))
    )
    """,
    "CREATE INDEX ix_knowledge_concept_aliases_concept ON knowledge_concept_aliases (concept_id)",
    """
    CREATE TABLE knowledge_passage_concepts (
        passage_id uuid NOT NULL REFERENCES knowledge_passages(id) ON DELETE CASCADE,
        concept_id uuid NOT NULL REFERENCES knowledge_concepts(id) ON DELETE CASCADE,
        mentions smallint NOT NULL,
        extractor_version varchar(40) NOT NULL,
        PRIMARY KEY (passage_id, concept_id),
        CONSTRAINT ck_knowledge_passage_concepts_mentions CHECK (mentions > 0)
    )
    """,
    "CREATE INDEX ix_knowledge_passage_concepts_concept ON knowledge_passage_concepts (concept_id)",
    """
    CREATE TABLE knowledge_relations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        subject_concept_id uuid NOT NULL REFERENCES knowledge_concepts(id) ON DELETE CASCADE,
        predicate varchar(20) NOT NULL,
        object_concept_id uuid REFERENCES knowledge_concepts(id) ON DELETE CASCADE,
        attribute varchar(40),
        value varchar(80),
        qualifiers jsonb NOT NULL DEFAULT '{}'::jsonb,
        passage_id uuid NOT NULL REFERENCES knowledge_passages(id) ON DELETE CASCADE,
        evidence_start integer NOT NULL,
        evidence_end integer NOT NULL,
        method varchar(20) NOT NULL,
        extractor_version varchar(40) NOT NULL,
        confidence smallint NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_knowledge_relations_predicate CHECK (predicate IN (
            'uses','is_a','operates_at','part_of','extends','replaces','compared_with','related','has_value')),
        CONSTRAINT ck_knowledge_relations_method CHECK (method IN ('pattern','outline','cooccurrence','model')),
        CONSTRAINT ck_knowledge_relations_object_predicate
            CHECK ((predicate = 'has_value') <> (object_concept_id IS NOT NULL)),
        CONSTRAINT ck_knowledge_relations_value_predicate
            CHECK ((predicate = 'has_value') = (attribute IS NOT NULL AND value IS NOT NULL)),
        CONSTRAINT ck_knowledge_relations_evidence CHECK (evidence_start >= 0 AND evidence_end > evidence_start),
        CONSTRAINT ck_knowledge_relations_confidence CHECK (confidence BETWEEN 0 AND 100)
    )
    """,
    "CREATE INDEX ix_knowledge_relations_subject ON knowledge_relations (subject_concept_id, predicate)",
    "CREATE INDEX ix_knowledge_relations_object ON knowledge_relations (object_concept_id)",
    "CREATE INDEX ix_knowledge_relations_passage ON knowledge_relations (passage_id)",
    "ALTER TABLE knowledge_duplicate_reviews DROP CONSTRAINT knowledge_duplicate_reviews_classification_check",
    "ALTER TABLE knowledge_duplicate_reviews ADD CONSTRAINT ck_knowledge_duplicate_reviews_classification "
    f"CHECK (classification IN ({CLASSIFICATIONS}))",
)

# Courses, course assignments and egress approvals are operator decisions; subset/superset reviews cannot be
# expressed before 0025. A downgrade would lose them, so it is refused before any DDL. Passages, occurrences,
# fingerprints and the concept graph are derived data (rebuilt by processing the files again).
DOWNGRADE_CHECKS = (
    "DO $$ BEGIN IF EXISTS (SELECT 1 FROM knowledge_courses) OR EXISTS (SELECT 1 FROM knowledge_sources "
    "WHERE course IS NOT NULL OR embed_egress_approved_at IS NOT NULL OR extract_egress_approved_at IS NOT NULL) "
    "OR EXISTS (SELECT 1 FROM knowledge_duplicate_reviews WHERE classification IN ('subset','superset')) "
    "THEN RAISE EXCEPTION '0025 downgrade refused: courses, egress approvals or subset reviews exist'; "
    "END IF; END $$",
)

DOWNGRADE = (
    "SET LOCAL lock_timeout = '5s'",
    "ALTER TABLE knowledge_duplicate_reviews DROP CONSTRAINT ck_knowledge_duplicate_reviews_classification",
    "ALTER TABLE knowledge_duplicate_reviews ADD CONSTRAINT knowledge_duplicate_reviews_classification_check "
    f"CHECK (classification IN ({CLASSIFICATIONS_0023}))",
    "DROP TABLE knowledge_relations",
    "DROP TABLE knowledge_passage_concepts",
    "DROP TABLE knowledge_concept_aliases",
    "DROP TABLE knowledge_concepts",
    "DROP TABLE knowledge_page_fingerprints",
    "DROP TABLE knowledge_passage_links",
    "DROP TABLE knowledge_passage_sources",
    "DROP TABLE knowledge_passage_occurrences",
    "DROP TABLE knowledge_passage_embeddings",
    "DROP TABLE knowledge_passages",
    "DROP TABLE knowledge_editions",
    "DROP INDEX ix_knowledge_sources_course",
    """
    ALTER TABLE knowledge_sources
        DROP CONSTRAINT ck_knowledge_sources_extract_egress_basis,
        DROP CONSTRAINT ck_knowledge_sources_embed_egress_basis,
        DROP COLUMN extract_egress_approved_by_user_id,
        DROP COLUMN extract_egress_basis,
        DROP COLUMN extract_egress_approved_at,
        DROP COLUMN embed_egress_approved_by_user_id,
        DROP COLUMN embed_egress_basis,
        DROP COLUMN embed_egress_approved_at,
        DROP COLUMN course
    """,
    "DROP TABLE knowledge_courses",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in (*DOWNGRADE_CHECKS, *DOWNGRADE):
        op.execute(statement)
