"""Knowledge-base persistence and vector search (PostgreSQL + pgvector).

This is the only module that queries the knowledge-base tables. It knows nothing
about LangChain, LangGraph or HTTP.

Access boundary: every search takes an `AccessScope`. Allowed sources are resolved
first (approved global sources, explicitly selected sources, plus the caller's own sources)
and the vector search runs only inside that set. Name and metadata filters can narrow it,
never widen it.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import delete, exists, func, literal_column, or_, select, text, true, union_all
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Document,
    DocumentChunk,
    DocumentRevision,
    FileChunk,
    KnowledgeConcept,
    KnowledgeCourse,
    KnowledgePassage,
    KnowledgePassageConcept,
    KnowledgePassageEmbedding,
    KnowledgePassageOccurrence,
    KnowledgePassageSource,
    KnowledgeSource,
    KnowledgeSourceUserGrant,
    UserFile,
)
from app.db.session import Database
from app.rag.errors import IngestionError, RetrievalError
from app.rag.lexical import LexicalQuery, reciprocal_rank_fusion
from app.rag.types import AccessScope

_FILTER_KEY = re.compile(r"^[a-z0-9_]{1,64}$")
_SCALARS = (str, int, float, bool)


@dataclass(frozen=True)
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    source_id: uuid.UUID
    source_name: str
    title: str
    uri: str
    ordinal: int
    content: str
    metadata: dict[str, Any]
    score: float  # cosine similarity, 1 - cosine distance


@dataclass(frozen=True)
class HybridSearchResult:
    hits: list[SearchHit]
    timings: dict[str, int | str]


@dataclass(frozen=True)
class FileHit:
    """A passage of a file the student attached (app/files)."""

    file_id: uuid.UUID
    filename: str
    chunk_count: int
    ordinal: int
    content: str
    metadata: dict[str, Any]
    score: float | None  # None when read in order, not searched


@dataclass(frozen=True)
class ChunkInput:
    ordinal: int
    content: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


def _live_in(passage_id, allowed_source_ids: list[uuid.UUID], documents: tuple[uuid.UUID, ...] | None = None):
    """The passage is live (a published revision carries it) in at least one allowed source; in a program chat
    (`documents`), in one of its month's documents (indexed by passage: ix_knowledge_passage_occurrences_passage)."""
    if documents is not None:
        return (
            select(KnowledgePassageOccurrence.passage_id)
            .join(DocumentRevision, DocumentRevision.id == KnowledgePassageOccurrence.revision_id)
            .where(
                KnowledgePassageOccurrence.passage_id == passage_id,
                KnowledgePassageOccurrence.document_id.in_(list(documents)),
                KnowledgePassageOccurrence.source_id.in_(allowed_source_ids),
                DocumentRevision.status == "published",
            )
            .exists()
        )
    return (
        select(KnowledgePassageSource.passage_id)
        .where(
            KnowledgePassageSource.passage_id == passage_id, KnowledgePassageSource.source_id.in_(allowed_source_ids)
        )
        .exists()
    )


def _with_provenance(
    ranked, score_column: str, allowed_source_ids: list[uuid.UUID], documents: tuple[uuid.UUID, ...] | None = None
):
    """Canonical passage hits with where they appear: one published occurrence in an allowed source (lateral,
    k rows only), shaped like a document_chunks row so citations keep book, page and section. In a program chat
    (`documents`) the occurrence is one of the month's documents, so a passage two books share cites the month's."""
    in_month = KnowledgePassageOccurrence.document_id.in_(list(documents)) if documents is not None else true()
    occurrence = (
        select(
            KnowledgePassageOccurrence.document_id,
            KnowledgePassageOccurrence.source_id,
            KnowledgePassageOccurrence.ordinal,
            KnowledgePassageOccurrence.revision_id,
            KnowledgePassageOccurrence.stable_chunk_key,
            KnowledgePassageOccurrence.section_path,
            KnowledgePassageOccurrence.physical_page_start,
            KnowledgePassageOccurrence.physical_page_end,
            KnowledgePassageOccurrence.printed_page_label,
            KnowledgePassageOccurrence.element_type,
        )
        .join(DocumentRevision, DocumentRevision.id == KnowledgePassageOccurrence.revision_id)
        .where(
            KnowledgePassageOccurrence.passage_id == ranked.c.id,
            KnowledgePassageOccurrence.source_id.in_(allowed_source_ids),
            DocumentRevision.status == "published",
            in_month,
        )
        .order_by(KnowledgePassageOccurrence.document_id, KnowledgePassageOccurrence.ordinal)
        .limit(1)
        .lateral("occurrence")
    )
    metadata = func.jsonb_build_object(
        "element_type", occurrence.c.element_type,
        "passage_id", ranked.c.id,
        "revision_id", occurrence.c.revision_id,
        "stable_chunk_key", occurrence.c.stable_chunk_key,
        "section_path", occurrence.c.section_path,
        "physical_page", occurrence.c.physical_page_start,
        "physical_page_end", occurrence.c.physical_page_end,
        "printed_page_label", occurrence.c.printed_page_label,
        type_=JSONB,
    )  # fmt: skip
    return (
        select(
            ranked.c.id,
            occurrence.c.document_id,
            occurrence.c.source_id,
            occurrence.c.ordinal,
            KnowledgePassage.content,
            metadata.label("metadata"),
            ranked.c[score_column],
        )
        .select_from(ranked)
        .join(occurrence, true())
        .join(KnowledgePassage, KnowledgePassage.id == ranked.c.id)
    )


# Words a question is made of that say nothing about its subject: a passage need not contain them.
_QUESTION_WORDS = frozenset(
    "a an and are as at be between can could do does for from had has have how i in is it its me my of on or "
    "should so tell than that the their them then there these this those to was we were what when where which who "
    "why will with would you your about explain show give please vs versus".split()
)


def _any_of(query: LexicalQuery):
    """A passage matches when it holds any informative word of the question (OR, ranked by ts_rank); each word is
    its own bound plainto_tsquery, so no query syntax (or, quotes, minus) ever reaches PostgreSQL."""
    words = [token for token in query.tokens if token not in _QUESTION_WORDS] or list(query.tokens)
    config = literal_column("'simple'::regconfig")
    combined = func.plainto_tsquery(config, words[0])
    for word in words[1:]:
        combined = combined.op("||")(func.plainto_tsquery(config, word))
    return combined


_IDENTIFIER = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9]+(?:[./:-][a-z0-9]+)+$|^\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?$")


def _identifiers(query: LexicalQuery | None) -> tuple[str, ...]:
    """Engineering identifiers in the question (Gi1/0/24, xe-0/0/7, 192.0.2.1): an exact one admits a passage."""
    return tuple(token for token in (query.tokens if query else ()) if _IDENTIFIER.match(token))


def _with_course(row) -> dict[str, Any]:
    """A hit's own attributes plus its source's course (ccna, ccnp), when the source has one."""
    metadata = dict(row.metadata or {})
    if row.source_course:
        metadata["course"] = row.source_course
    return metadata


def validate_metadata_filter(metadata_filter: dict[str, Any] | None) -> dict[str, Any] | None:
    """Flat {key: scalar} only. Bound as a JSON parameter; never interpolated into SQL."""
    if not metadata_filter:
        return None
    for key, value in metadata_filter.items():
        if not isinstance(key, str) or not _FILTER_KEY.match(key) or not isinstance(value, _SCALARS):
            raise RetrievalError("invalid_filter", "metadata filters must be flat {lowercase_key: scalar}")
    return dict(metadata_filter)


class KnowledgeStore:
    def __init__(self, db: Database, hnsw_ef_search: int = 100) -> None:
        self.db = db
        self.hnsw_ef_search = int(hnsw_ef_search)

    # ------------------------------------------------------------------ search
    @staticmethod
    def _allowed_sources_query(
        scope: AccessScope,
        source_names: list[str] | None,
        courses: Sequence[str] | None = None,
        include_general: bool = True,
    ):
        """courses: the turn's course boundary (None = every course). General sources (no course) belong to
        every course unless include_general is False (a foundation-course search adds only that course).
        A program chat (scope.documents): only approved global sources holding the month's documents, never anyone's
        private or granted ones; names and courses don't apply (the mapping is the boundary)."""
        if scope.documents is not None:
            holding = select(Document.source_id).where(Document.id.in_(list(scope.documents)))
            return select(KnowledgeSource.id).where(
                KnowledgeSource.rights_revoked_at.is_(None),
                KnowledgeSource.visibility == "global",
                KnowledgeSource.rights_status == "approved",
                KnowledgeSource.id.in_(holding),
            )
        if scope.user_id is None:
            visible = (KnowledgeSource.visibility == "global") & (KnowledgeSource.rights_status == "approved")
        else:
            now = func.clock_timestamp()
            selected = exists().where(
                KnowledgeSourceUserGrant.source_id == KnowledgeSource.id,
                KnowledgeSourceUserGrant.user_id == scope.user_id,
                KnowledgeSourceUserGrant.revoked_at.is_(None),
                or_(KnowledgeSourceUserGrant.expires_at.is_(None), KnowledgeSourceUserGrant.expires_at > now),
            )
            visible = or_(
                KnowledgeSource.owner_user_id == scope.user_id,
                (KnowledgeSource.visibility == "global") & (KnowledgeSource.rights_status == "approved"),
                (KnowledgeSource.visibility == "selected") & (KnowledgeSource.rights_status == "approved") & selected,
            )
        query = select(KnowledgeSource.id).where(KnowledgeSource.rights_revoked_at.is_(None), visible)
        if source_names:
            query = query.where(KnowledgeSource.name.in_(source_names))
        if courses is not None:
            in_course = KnowledgeSource.course.in_(list(courses))
            query = query.where(or_(in_course, KnowledgeSource.course.is_(None)) if include_general else in_course)
        return query

    async def allowed_source_ids(
        self,
        session: AsyncSession,
        scope: AccessScope,
        source_names: list[str] | None = None,
        courses: Sequence[str] | None = None,
        include_general: bool = True,
    ) -> list[uuid.UUID]:
        return list(await session.scalars(self._allowed_sources_query(scope, source_names, courses, include_general)))

    async def foundation_courses(self, courses: Sequence[str]) -> tuple[str, ...]:
        """The courses these build on (ccnp -> ccna), not already among them."""
        async with self.db.sessionmaker() as session:
            found = await session.scalars(
                select(KnowledgeCourse.builds_on).where(
                    KnowledgeCourse.slug.in_(list(courses)), KnowledgeCourse.builds_on.is_not(None)
                )
            )
            return tuple(sorted({slug for slug in found if slug not in courses}))

    async def has_chunks(self, scope: AccessScope, embedding_model: str) -> bool:
        """Cheap check (indexed EXISTS) so an empty knowledge base never costs an embedding call. A program chat
        whose month has no mapped document has nothing to search."""
        if scope.documents is not None and not scope.documents:
            return False
        try:
            async with self.db.sessionmaker() as session:
                allowed = self._allowed_sources_query(scope, None).scalar_subquery()
                chunks = (
                    select(DocumentChunk.id)
                    .where(DocumentChunk.embedding_model == embedding_model, DocumentChunk.source_id.in_(allowed))
                    .exists()
                )
                passages = (
                    select(KnowledgePassageSource.passage_id)
                    .join(
                        KnowledgePassageEmbedding,
                        (KnowledgePassageEmbedding.passage_id == KnowledgePassageSource.passage_id)
                        & (KnowledgePassageEmbedding.embedding_model == embedding_model),
                    )
                    .where(KnowledgePassageSource.source_id.in_(allowed))
                    .exists()
                )
                return bool(await session.scalar(select(chunks | passages)))
        except Exception as error:  # noqa: BLE001 - classified by the caller
            raise RetrievalError("database_unavailable", type(error).__name__) from error

    async def apply_search_settings(self, session: AsyncSession) -> None:
        """Keep filtered searches on the HNSW index (pgvector >= 0.8 iterative scans). Transaction-local."""
        # set_config(..., true) is SET LOCAL with a bound value (SET itself takes no parameters).
        await session.execute(
            text("SELECT set_config('hnsw.ef_search', :value, true)"), {"value": str(self.hnsw_ef_search)}
        )
        await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))

    @staticmethod
    def search_statement(
        vector: list[float],
        embedding_model: str,
        allowed_source_ids: list[uuid.UUID],
        k: int,
        metadata_filter: dict[str, Any] | None = None,
        documents: tuple[uuid.UUID, ...] | None = None,
    ):
        """Nearest texts inside the allowed sources; titles are joined for the k hits only. `documents`: a program
        chat's month (every branch keeps to those documents, before ranking as well as in the citation).

        Two indexed branches, one statement: ingested documents (document_chunks) and Knowledge Library books
        (canonical passages, each stored and indexed once however many books carry it). Each orders by
        `embedding <=> :q` on its own HNSW index (vector_cosine_ops); the embedding column is never selected.
        A metadata filter narrows to ingested documents (book passages carry no such attributes).
        """
        chunks = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.source_id,
                DocumentChunk.ordinal,
                DocumentChunk.content,
                DocumentChunk.metadata_.label("metadata"),
                DocumentChunk.embedding.cosine_distance(vector).label("distance"),
            )
            .where(
                DocumentChunk.embedding_model == embedding_model,
                DocumentChunk.source_id.in_(allowed_source_ids),
                DocumentChunk.document_id.in_(list(documents)) if documents is not None else true(),
            )
            .order_by(DocumentChunk.embedding.cosine_distance(vector))
            .limit(k)
        )
        if metadata_filter:
            chunks = chunks.where(DocumentChunk.metadata_.contains(metadata_filter))
        branches = [select(chunks.subquery("chunk_hits"))]
        if not metadata_filter:
            distance = KnowledgePassageEmbedding.embedding.cosine_distance(vector)
            near = (
                select(KnowledgePassageEmbedding.passage_id.label("id"), distance.label("distance"))
                .where(
                    KnowledgePassageEmbedding.embedding_model == embedding_model,
                    _live_in(KnowledgePassageEmbedding.passage_id, allowed_source_ids, documents),
                )
                .order_by(distance)
                .limit(k)
                .subquery("near")
            )
            branches.append(
                select(_with_provenance(near, "distance", allowed_source_ids, documents).subquery("passage_hits"))
            )
        hits = union_all(*branches).cte("hits")
        return (
            select(
                hits,
                Document.title,
                Document.uri,
                KnowledgeSource.name.label("source_name"),
                KnowledgeSource.course.label("source_course"),
            )
            .join(Document, (Document.id == hits.c.document_id) & (Document.source_id == hits.c.source_id))
            .join(KnowledgeSource, KnowledgeSource.id == hits.c.source_id)
            .order_by(hits.c.distance)
            .limit(k)
        )

    @staticmethod
    def lexical_search_statement(
        query: LexicalQuery,
        embedding_model: str,
        allowed_source_ids: list[uuid.UUID],
        k: int,
        metadata_filter: dict[str, Any] | None = None,
        documents: tuple[uuid.UUID, ...] | None = None,
    ):
        """Rank plain lexical query text against generated tsvectors with bound values (`documents`: as above)."""
        tsquery = _any_of(query)
        rank = func.ts_rank(DocumentChunk.content_tsv, tsquery).label("lexical_rank")
        chunks = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                DocumentChunk.source_id,
                DocumentChunk.ordinal,
                DocumentChunk.content,
                DocumentChunk.metadata_.label("metadata"),
                rank,
            )
            .where(
                DocumentChunk.content_tsv.op("@@")(tsquery),
                DocumentChunk.embedding_model == embedding_model,
                DocumentChunk.source_id.in_(allowed_source_ids),
                DocumentChunk.document_id.in_(list(documents)) if documents is not None else true(),
            )
            .order_by(rank.desc(), DocumentChunk.id)
            .limit(k)
        )
        if metadata_filter:
            chunks = chunks.where(DocumentChunk.metadata_.contains(metadata_filter))
        branches = [select(chunks.subquery("chunk_lexical"))]
        if not metadata_filter:
            passage_rank = func.ts_rank(KnowledgePassage.content_tsv, tsquery)
            vectorized = (
                select(KnowledgePassageEmbedding.passage_id)
                .where(
                    KnowledgePassageEmbedding.passage_id == KnowledgePassage.id,
                    KnowledgePassageEmbedding.embedding_model == embedding_model,
                )
                .exists()
            )
            ranked = (
                select(KnowledgePassage.id, passage_rank.label("lexical_rank"))
                .where(
                    KnowledgePassage.content_tsv.op("@@")(tsquery),
                    vectorized,
                    _live_in(KnowledgePassage.id, allowed_source_ids, documents),
                )
                .order_by(passage_rank.desc(), KnowledgePassage.id)
                .limit(k)
                .subquery("ranked")
            )
            branches.append(
                select(
                    _with_provenance(ranked, "lexical_rank", allowed_source_ids, documents).subquery("passage_lexical")
                )
            )
        hits = union_all(*branches).cte("lexical_hits")
        return (
            select(
                hits,
                Document.title,
                Document.uri,
                KnowledgeSource.name.label("source_name"),
                KnowledgeSource.course.label("source_course"),
            )
            .join(Document, (Document.id == hits.c.document_id) & (Document.source_id == hits.c.source_id))
            .join(KnowledgeSource, KnowledgeSource.id == hits.c.source_id)
            .order_by(hits.c.lexical_rank.desc(), hits.c.id)
            .limit(k)
        )

    @staticmethod
    def concept_search_statement(
        concept_keys: Sequence[str],
        embedding_model: str,
        allowed_source_ids: list[uuid.UUID],
        k: int,
        documents: tuple[uuid.UUID, ...] | None = None,
    ):
        """Book passages that mention the question's concepts (the concept graph): most of them covered first, then
        the most mentions. Live in an allowed source and embedded for this model, like the other legs."""
        covered = func.count(KnowledgePassageConcept.concept_id)
        weight = func.sum(KnowledgePassageConcept.mentions)
        vectorized = (
            select(KnowledgePassageEmbedding.passage_id)
            .where(
                KnowledgePassageEmbedding.passage_id == KnowledgePassageConcept.passage_id,
                KnowledgePassageEmbedding.embedding_model == embedding_model,
            )
            .exists()
        )
        ranked = (
            select(KnowledgePassageConcept.passage_id.label("id"), (covered * 1000 + weight).label("concept_rank"))
            .join(KnowledgeConcept, KnowledgeConcept.id == KnowledgePassageConcept.concept_id)
            .where(
                KnowledgeConcept.key.in_(list(concept_keys)),
                vectorized,
                _live_in(KnowledgePassageConcept.passage_id, allowed_source_ids, documents),
            )
            .group_by(KnowledgePassageConcept.passage_id)
            .order_by(covered.desc(), weight.desc(), KnowledgePassageConcept.passage_id)
            .limit(k)
            .subquery("concept_ranked")
        )
        hits = select(_with_provenance(ranked, "concept_rank", allowed_source_ids, documents).subquery("concept_rows"))
        hits = hits.cte("concept_hits")
        return (
            select(
                hits,
                Document.title,
                Document.uri,
                KnowledgeSource.name.label("source_name"),
                KnowledgeSource.course.label("source_course"),
            )
            .join(Document, (Document.id == hits.c.document_id) & (Document.source_id == hits.c.source_id))
            .join(KnowledgeSource, KnowledgeSource.id == hits.c.source_id)
            .order_by(hits.c.concept_rank.desc(), hits.c.id)
            .limit(k)
        )

    async def _exact_scores(self, ids: list[uuid.UUID], vector: list[float], embedding_model: str) -> dict:
        """The cosine similarity of candidates only a lexical or concept leg found (k rows at most, by key)."""
        if not ids:
            return {}
        chunks = select(
            DocumentChunk.id.label("id"), (1 - DocumentChunk.embedding.cosine_distance(vector)).label("score")
        ).where(DocumentChunk.id.in_(ids), DocumentChunk.embedding_model == embedding_model)
        passages = select(
            KnowledgePassageEmbedding.passage_id.label("id"),
            (1 - KnowledgePassageEmbedding.embedding.cosine_distance(vector)).label("score"),
        ).where(
            KnowledgePassageEmbedding.passage_id.in_(ids), KnowledgePassageEmbedding.embedding_model == embedding_model
        )
        async with self.db.sessionmaker() as session:
            rows = await session.execute(union_all(chunks, passages))
            return {row.id: float(row.score) for row in rows}

    @staticmethod
    def _dense_hits(rows, min_score: float) -> list[SearchHit]:
        results = []
        for row in rows:
            score = 1.0 - float(row.distance)
            if score < min_score:
                continue
            results.append(
                SearchHit(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    source_id=row.source_id,
                    source_name=row.source_name,
                    title=row.title,
                    uri=row.uri,
                    ordinal=row.ordinal,
                    content=row.content,
                    metadata=_with_course(row),
                    score=round(score, 6),
                )
            )
        return results

    @staticmethod
    def _lexical_hits(rows) -> list[SearchHit]:
        return [
            SearchHit(
                chunk_id=row.id,
                document_id=row.document_id,
                source_id=row.source_id,
                source_name=row.source_name,
                title=row.title,
                uri=row.uri,
                ordinal=row.ordinal,
                content=row.content,
                metadata=_with_course(row),
                score=0.0,
            )
            for row in rows
        ]

    async def search(
        self,
        scope: AccessScope,
        vector: list[float],
        *,
        embedding_model: str,
        k: int,
        source_names: list[str] | None = None,
        metadata_filter: dict[str, Any] | None = None,
        min_score: float = 0.0,
        courses: Sequence[str] | None = None,
        include_general: bool = True,
    ) -> list[SearchHit]:
        metadata_filter = validate_metadata_filter(metadata_filter)
        try:
            async with self.db.sessionmaker() as session, session.begin():
                allowed = await self.allowed_source_ids(session, scope, source_names, courses, include_general)
                if not allowed:
                    return []
                await self.apply_search_settings(session)
                rows = (
                    await session.execute(
                        self.search_statement(vector, embedding_model, allowed, k, metadata_filter, scope.documents)
                    )
                ).all()
        except RetrievalError:
            raise
        except Exception as error:  # noqa: BLE001
            raise RetrievalError("database_unavailable", type(error).__name__) from error

        return self._dense_hits(rows, min_score)

    async def hybrid_search(
        self,
        scope: AccessScope,
        vector: list[float],
        lexical_query: LexicalQuery | None,
        *,
        embedding_model: str,
        k: int,
        dense_candidates: int,
        lexical_candidates: int,
        rrf_k: int,
        source_names: list[str] | None = None,
        metadata_filter: dict[str, Any] | None = None,
        min_score: float = 0.0,
        courses: Sequence[str] | None = None,
        include_general: bool = True,
        concept_keys: Sequence[str] = (),
    ) -> HybridSearchResult:
        """Fuse bounded dense, lexical and concept rankings; a failed optional leg degrades to the others. A candidate
        only the lexical or concept leg found must still clear the cosine floor (measured exactly), unless it holds
        an engineering identifier the question names verbatim: keyword overlap alone never admits a passage.
        Every floor-passing fused candidate is returned (dynamic selection): the bounded candidate pools cap the
        work, and the prompt's context budget (app/rag/context.py) makes the final cut. `k` is kept for callers
        that still cap (the retriever's foundation-fill threshold); it no longer cuts the fused result.
        """
        metadata_filter = validate_metadata_filter(metadata_filter)
        try:
            async with self.db.sessionmaker() as session, session.begin():
                allowed = await self.allowed_source_ids(session, scope, source_names, courses, include_general)
            if not allowed:
                return HybridSearchResult([], {"dense_ms": 0, "lexical_ms": 0, "lexical_reason": "no_sources"})

            async def run_dense() -> tuple[list[SearchHit], int]:
                started = time.perf_counter()
                async with self.db.sessionmaker() as session, session.begin():
                    await self.apply_search_settings(session)
                    rows = (
                        await session.execute(
                            self.search_statement(
                                vector, embedding_model, allowed, dense_candidates, metadata_filter, scope.documents
                            )
                        )
                    ).all()
                return self._dense_hits(rows, min_score), int((time.perf_counter() - started) * 1000)

            async def run_lexical() -> tuple[list[SearchHit], int, str]:
                if lexical_query is None:
                    return [], 0, "empty_query"
                started = time.perf_counter()
                try:
                    async with self.db.sessionmaker() as session, session.begin():
                        rows = (
                            await session.execute(
                                self.lexical_search_statement(
                                    lexical_query,
                                    embedding_model,
                                    allowed,
                                    lexical_candidates,
                                    metadata_filter,
                                    scope.documents,
                                )
                            )
                        ).all()
                    return self._lexical_hits(rows), int((time.perf_counter() - started) * 1000), "ok"
                except Exception as error:  # noqa: BLE001 - optional leg must not abort dense retrieval
                    return [], int((time.perf_counter() - started) * 1000), f"error:{type(error).__name__}"

            async def run_concepts() -> tuple[list[SearchHit], int, str]:
                if not concept_keys or metadata_filter:
                    return [], 0, "no_concepts"
                started = time.perf_counter()
                try:
                    async with self.db.sessionmaker() as session, session.begin():
                        statement = self.concept_search_statement(
                            concept_keys, embedding_model, allowed, lexical_candidates, scope.documents
                        )
                        rows = (await session.execute(statement)).all()
                    return self._lexical_hits(rows), int((time.perf_counter() - started) * 1000), "ok"
                except Exception as error:  # noqa: BLE001 - optional leg must not abort dense retrieval
                    return [], int((time.perf_counter() - started) * 1000), f"error:{type(error).__name__}"

            dense_task = asyncio.create_task(run_dense())
            optional = [asyncio.create_task(run_lexical()), asyncio.create_task(run_concepts())]
            try:
                dense_hits, dense_ms = await dense_task
            except BaseException:
                for task in optional:
                    task.cancel()
                await asyncio.gather(*optional, return_exceptions=True)
                raise
            (lexical_hits, lexical_ms, lexical_reason), (concept_hits, concept_ms, concept_reason) = [
                await task for task in optional
            ]
            fused = reciprocal_rank_fusion(
                (dense_hits, lexical_hits, concept_hits),
                key=lambda hit: hit.chunk_id,
                k=rrf_k,
                # Every fused candidate gets its floor check: the bounded candidate pools cap the work, and the
                # prompt's context budget (app/rag/context.py) makes the final selection. No fixed final top-k.
                limit=min(3 * max(dense_candidates, lexical_candidates), 1_000),
                candidates_per_ranking=max(dense_candidates, lexical_candidates),
            )
            dense_scores = {hit.chunk_id: hit.score for hit in dense_hits}
            unscored = [c.item.chunk_id for c in fused if c.item.chunk_id not in dense_scores]
            exact = await self._exact_scores(unscored, vector, embedding_model)
        except RetrievalError:
            raise
        except Exception as error:  # noqa: BLE001
            raise RetrievalError("database_unavailable", type(error).__name__) from error

        identifiers = _identifiers(lexical_query)
        hits: list[SearchHit] = []
        for candidate in fused:
            hit = candidate.item
            score = dense_scores.get(hit.chunk_id, exact.get(hit.chunk_id))
            named = any(identifier in hit.content.casefold() for identifier in identifiers)
            if score is None or (score < min_score and not named):
                continue
            hits.append(replace(hit, score=round(score, 6)))
        # Dynamic selection: every fused candidate that clears the floor (or carries an identifier the question
        # names verbatim) is returned; the bounded candidate pools above cap the work, and the prompt's context
        # budget (app/rag/context.py) decides what finally enters the model. There is no fixed final top-k cut.
        return HybridSearchResult(
            hits,
            {
                "dense_ms": dense_ms,
                "lexical_ms": lexical_ms,
                "lexical_reason": lexical_reason,
                "concept_ms": concept_ms,
                "concept_reason": concept_reason,
                "turned_away": len(fused) - len(hits),
            },
        )

    # ------------------------------------------------------------ attached files
    # A turn reads only files attached to its conversation, owned by its student and ready. The
    # few chunks involved are searched exactly (no vector index), ranked within each file.
    @staticmethod
    def _file_query(user_id: uuid.UUID, file_ids: list[uuid.UUID], embedding_model: str, *extra):
        columns = (
            FileChunk.file_id,
            UserFile.filename,
            UserFile.chunk_count,
            FileChunk.ordinal,
            FileChunk.content,
            FileChunk.metadata_.label("metadata"),
            *extra,
        )
        return (
            select(*columns)
            .join(UserFile, UserFile.id == FileChunk.file_id)
            .where(
                UserFile.user_id == user_id,
                UserFile.id.in_(file_ids),
                UserFile.status == "ready",
                FileChunk.embedding_model == embedding_model,
            )
        )

    async def file_chunks_in_order(
        self, user_id: uuid.UUID, file_ids: list[uuid.UUID], *, embedding_model: str, per_file_limit: int
    ) -> list[FileHit]:
        """The first chunks of each file, in reading order (whole small files, or no query vector)."""
        if not file_ids:
            return []
        statement = (
            self._file_query(user_id, file_ids, embedding_model)
            .where(FileChunk.ordinal < per_file_limit)
            .order_by(FileChunk.file_id, FileChunk.ordinal)
        )
        return await self._file_hits(statement, scored=False)

    async def search_files(
        self,
        user_id: uuid.UUID,
        file_ids: list[uuid.UUID],
        vector: list[float],
        *,
        embedding_model: str,
        per_file_k: int,
    ) -> list[FileHit]:
        """The per_file_k closest chunks of each file, exactly: every attached file is represented."""
        if not file_ids:
            return []
        distance = FileChunk.embedding.cosine_distance(vector)
        ranked = self._file_query(
            user_id,
            file_ids,
            embedding_model,
            distance.label("distance"),
            func.row_number().over(partition_by=FileChunk.file_id, order_by=distance).label("rank"),
        ).cte("ranked")
        statement = select(ranked).where(ranked.c.rank <= per_file_k).order_by(ranked.c.file_id, ranked.c.ordinal)
        return await self._file_hits(statement, scored=True)

    async def _file_hits(self, statement, *, scored: bool) -> list[FileHit]:
        try:
            async with self.db.sessionmaker() as session:
                rows = (await session.execute(statement)).all()
        except Exception as error:  # noqa: BLE001
            raise RetrievalError("database_unavailable", type(error).__name__) from error
        return [
            FileHit(
                file_id=row.file_id,
                filename=row.filename,
                chunk_count=int(row.chunk_count or 0),
                ordinal=row.ordinal,
                content=row.content,
                metadata=dict(row.metadata or {}),
                score=round(1.0 - float(row.distance), 6) if scored else None,
            )
            for row in rows
        ]

    async def count_other_model_chunks(self, embedding_model: str) -> int:
        async with self.db.sessionmaker() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(DocumentChunk)
                    .where(DocumentChunk.embedding_model != embedding_model)
                )
                or 0
            )

    # --------------------------------------------------------------- ingestion
    async def get_or_create_source(
        self, name: str, *, owner_user_id: uuid.UUID | None, description: str | None = None
    ) -> KnowledgeSource:
        async with self.db.sessionmaker() as session, session.begin():
            await session.execute(
                pg_insert(KnowledgeSource)
                .values(
                    name=name,
                    owner_user_id=owner_user_id,
                    description=description,
                    visibility="global" if owner_user_id is None else "owner_only",
                    rights_status="approved" if owner_user_id is None else "unverified",
                    rights_basis="legacy shared ingestion" if owner_user_id is None else None,
                )
                .on_conflict_do_nothing(index_elements=["name"])
            )
            source = await session.scalar(select(KnowledgeSource).where(KnowledgeSource.name == name))
        if source.owner_user_id != owner_user_id:
            # Never silently change who can see a source.
            raise IngestionError("source_owner_mismatch", "the source exists with a different owner")
        return source

    async def document_signature(self, source_id: uuid.UUID, uri: str) -> str | None:
        async with self.db.sessionmaker() as session:
            return await session.scalar(
                select(Document.ingest_signature).where(Document.source_id == source_id, Document.uri == uri)
            )

    async def upsert_document(
        self,
        *,
        source_id: uuid.UUID,
        uri: str,
        title: str,
        content_hash: str,
        ingest_signature: str,
        metadata: dict[str, Any],
        chunks: list[ChunkInput],
        embedding_model: str,
    ) -> str:
        """Create or replace a document and ALL its chunks in one transaction.

        INSERT ... ON CONFLICT DO UPDATE takes a row lock on an existing document, so
        concurrent ingestions of the same (source, uri) serialise: the last commit wins
        and no mix of versions or stale chunks can remain. Any failure rolls back and
        leaves the previous version intact.
        """
        async with self.db.sessionmaker() as session, session.begin():
            statement = pg_insert(Document).values(
                source_id=source_id,
                uri=uri,
                title=title,
                content_hash=content_hash,
                ingest_signature=ingest_signature,
                metadata_=metadata,
            )
            statement = statement.on_conflict_do_update(
                index_elements=["source_id", "uri"],
                set_={
                    "title": statement.excluded.title,
                    "content_hash": statement.excluded.content_hash,
                    "ingest_signature": statement.excluded.ingest_signature,
                    "metadata": statement.excluded.metadata,
                    "updated_at": func.clock_timestamp(),
                },
            ).returning(Document.id, literal_column("(xmax = 0)").label("inserted"))
            row = (await session.execute(statement)).one()
            await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == row.id))
            if chunks:
                await session.execute(
                    DocumentChunk.__table__.insert(),
                    [
                        {
                            "document_id": row.id,
                            "source_id": source_id,
                            "ordinal": chunk.ordinal,
                            "content": chunk.content,
                            "metadata": chunk.metadata,
                            "embedding": chunk.embedding,
                            "embedding_model": embedding_model,
                        }
                        for chunk in chunks
                    ],
                )
        return "created" if row.inserted else "replaced"

    async def delete_document(self, source_name: str, uri: str) -> bool:
        async with self.db.sessionmaker() as session, session.begin():
            source_id = await session.scalar(select(KnowledgeSource.id).where(KnowledgeSource.name == source_name))
            if source_id is None:
                return False
            result = await session.execute(delete(Document).where(Document.source_id == source_id, Document.uri == uri))
            return result.rowcount > 0
