"""NHprep grounding for a month tutor (owner decision D5, 2026-11-23; PROGRAMS_NHPREP_GROUNDING).

Only the month's mapped NHprep courses: their notes, cheat sheets, flashcards and interview questions, ranked for the
student's question (dense and keyword legs, fused). Plain rows out and no model call: the query vector is passed in,
and the composition root (app/main.py, the only importer: tests/test_architecture_guards.py) hands the rows to the
month chat's retriever. General chat never calls this. NHprep text still never enters document_chunks or
knowledge_passages; it reaches the chat model masked, like every model input (app/llm/redact.py).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, text

from app.db.models import NhprepSearchChunk
from app.db.session import Database

KINDS = ["note", "cheat_sheet", "interview_question", "flashcard"]
RRF_K = 60
_VECTOR = NhprepSearchChunk.__table__.c.embedding.type
_WORD = re.compile(r"[a-z0-9]+")

# Literal SQL only (the security gate refuses SQL built from strings); both legs read the same columns and joins.
DENSE = text(
    """
    SELECT c.id, c.lesson_id, c.course_id, c.kind, c.ordinal, c.content,
           l.title AS lesson_title, co.title AS course_title,
           1 - (c.embedding <=> :q) AS cosine
    FROM nhprep_search_chunks c
    JOIN nhprep_lessons l ON l.id = c.lesson_id AND l.removed_at IS NULL
    JOIN nhprep_courses co ON co.id = c.course_id AND co.removed_at IS NULL
    WHERE c.embedding_model = :model
      AND c.course_id = ANY(CAST(:courses AS uuid[]))
      AND c.kind = ANY(CAST(:kinds AS text[]))
    ORDER BY c.embedding <=> :q
    LIMIT :k
    """
).bindparams(bindparam("q", type_=_VECTOR))

LEXICAL = text(
    """
    SELECT c.id, c.lesson_id, c.course_id, c.kind, c.ordinal, c.content,
           l.title AS lesson_title, co.title AS course_title,
           ts_rank_cd(c.content_tsv, query, 1) AS rank
    FROM nhprep_search_chunks c
    JOIN nhprep_lessons l ON l.id = c.lesson_id AND l.removed_at IS NULL
    JOIN nhprep_courses co ON co.id = c.course_id AND co.removed_at IS NULL
    CROSS JOIN websearch_to_tsquery('english'::regconfig, CAST(:words AS text)) AS query
    WHERE c.course_id = ANY(CAST(:courses AS uuid[]))
      AND c.kind = ANY(CAST(:kinds AS text[]))
      AND c.content_tsv @@ query
    ORDER BY rank DESC, c.id
    LIMIT :k
    """
)


def _row(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "lesson_id": row.lesson_id,
        "course_id": row.course_id,
        "kind": row.kind,
        "ordinal": row.ordinal,
        "content": row.content,
        "lesson_title": row.lesson_title,
        "course_title": row.course_title,
    }


async def passages(
    db: Database,
    courses: Sequence[uuid.UUID],
    vector: list[float] | None,
    model: str | None,
    question: str,
    *,
    limit: int = 4,
    candidates: int = 20,
    min_cosine: float = 0.0,
    ef_search: int = 100,
) -> list[dict[str, Any]]:
    """The best NHprep passages of these courses for the question, best first (at most `limit`). A passage the dense
    leg found must clear the cosine floor; one only the keyword leg found is kept (it names the question's words)."""
    if not courses:
        return []
    words = _WORD.findall(question.lower())[:12]
    base = {"courses": [str(course) for course in courses], "kinds": KINDS, "k": candidates}
    dense: list[Any] = []
    lexical: list[Any] = []
    async with db.sessionmaker() as session, session.begin():
        if vector is not None and model is not None:
            await session.execute(text("SELECT set_config('hnsw.ef_search', :value, true)"), {"value": str(ef_search)})
            await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            dense = list((await session.execute(DENSE, {**base, "q": vector, "model": model})).all())
        if words:  # "or" between words: any of them, ranked by how well each passage covers them
            lexical = list((await session.execute(LEXICAL, {**base, "words": " or ".join(words)})).all())
    cosine = {row.id: float(row.cosine) for row in dense}
    rows = {row.id: row for row in (*lexical, *dense)}
    scores: dict[uuid.UUID, float] = {}
    for ranking in (dense, lexical):
        for rank, row in enumerate(ranking, start=1):
            scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (RRF_K + rank)
    kept = [
        chunk
        for chunk in sorted(scores, key=lambda chunk: (-scores[chunk], str(chunk)))
        if vector is None or cosine.get(chunk, min_cosine) >= min_cosine
    ]
    return [{**_row(rows[chunk]), "score": cosine.get(chunk)} for chunk in kept[:limit]]
