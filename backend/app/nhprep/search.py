"""The NHprep finder: one typed request (intent.parse) -> the lesson it names, or why none (plan rev 4 §8.3).

One deadline (NHPREP_FIND_TIMEOUT_S, passed in) covers the query embedding and every SQL leg; a timeout or an error is
a status, never an exception. The embedding runs before the transaction opens (no transaction is held across a
provider call); the legs then run in one transaction on one connection, sequentially, so each is timed:
- S0 settings: statement_timeout, the trigram threshold, hnsw.ef_search (transaction-local);
- S1 dense: one masked query embedding -> the top chunks on the HNSW index -> best cosine per lesson;
- S2 lexical: websearch_to_tsquery('english') in OR form, ts_rank_cd, all-terms and per-term coverage;
- S3 titles: pg_trgm `<%` both ways on lesson titles, and on chapter titles; the day-N tier (source_day + course);
- S4 features of the fused top ids (exact cosine, trainers, video id).
Weighted RRF and the confidence rules are pure functions below; weights and thresholds are data (tuning.json).
Trainer hints resolve only through nhprep_trainers (the alias table): an unknown trainer is "none", never a guess.
Static text() statements with bound parameters only. The trace carries ids, counts, ms and reason codes, never text.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NhprepSearchChunk
from app.db.session import Database
from app.nhprep.types import FinderResult, Match, NhprepQuery, Status

TUNING: dict[str, Any] = json.loads(Path(__file__).with_name("tuning.json").read_text(encoding="utf-8"))
ITEM_KINDS = {
    "notes": "note",
    "cheat_sheet": "cheat_sheet",
    "flashcards": "flashcard",
    "interview": "interview_question",
}
_DROP_TOKENS = {"or", "and", "not"}
_HINT = re.compile(r"[a-z0-9 ]{1,40}")


class QueryEmbedder(Protocol):
    """The injected (masked) embeddings provider: app.state.embeddings. Not langchain's type: app/nhprep never
    imports langchain."""

    async def aembed_query(self, text: str) -> list[float]: ...


_VECTOR = NhprepSearchChunk.__table__.c.embedding.type

SETTINGS = text(
    "SELECT set_config('hnsw.ef_search', :ef_search, true), "
    "set_config('hnsw.iterative_scan', 'strict_order', true), "
    "set_config('pg_trgm.word_similarity_threshold', :title_floor, true), "
    "set_config('statement_timeout', :timeout_ms, true)"
)
TRAINER_COURSES = text(
    "SELECT ct.course_id FROM nhprep_trainers t "
    "JOIN nhprep_course_trainers ct ON ct.trainer_id = t.id "
    "JOIN nhprep_courses co ON co.id = ct.course_id "
    "WHERE co.removed_at IS NULL AND (t.normalized_name = :name OR CAST(:name AS varchar) = ANY(t.aliases)) "
    "ORDER BY ct.course_id"
)
HINT_COURSES = text(
    "SELECT id FROM nhprep_courses WHERE removed_at IS NULL "
    r"AND regexp_replace(lower(slug || ' ' || title), '[^a-z0-9]+', ' ', 'g') ~ ('\m' || CAST(:hint AS text) || '\M') "
    "ORDER BY id"
)
DENSE = text(
    """
    SELECT lesson_id, 1 - min(distance) AS cosine,
           (array_agg(item_id ORDER BY distance) FILTER (WHERE kind = ANY(CAST(:kinds AS text[]))))[1] AS item_id
    FROM (
        SELECT c.lesson_id, c.item_id, c.kind, c.embedding <=> :q AS distance
        FROM nhprep_search_chunks c
        WHERE c.embedding_model = :model
          AND (CAST(:courses AS uuid[]) IS NULL OR c.course_id = ANY(CAST(:courses AS uuid[])))
        ORDER BY c.embedding <=> :q
        LIMIT :dense_k
    ) hits
    GROUP BY lesson_id
    ORDER BY min(distance), lesson_id
    """
).bindparams(bindparam("q", type_=_VECTOR))
LEXICAL = text(
    """
    WITH hits AS (
        SELECT c.lesson_id, c.item_id, c.kind, c.content_tsv,
               c.content_tsv @@ websearch_to_tsquery('english'::regconfig, CAST(:topic AS text)) AS all_terms,
               ts_rank_cd(c.content_tsv, websearch_to_tsquery('english'::regconfig, CAST(:any_text AS text)), 1)
                   AS rank_cd
        FROM nhprep_search_chunks c
        WHERE c.content_tsv @@ websearch_to_tsquery('english'::regconfig, CAST(:any_text AS text))
          AND (CAST(:model AS text) IS NULL OR c.embedding_model = CAST(:model AS text))
          AND (CAST(:courses AS uuid[]) IS NULL OR c.course_id = ANY(CAST(:courses AS uuid[])))
          AND (CAST(:lexical_kinds AS text[]) IS NULL OR c.kind = ANY(CAST(:lexical_kinds AS text[])))
        ORDER BY all_terms DESC, rank_cd DESC, c.id
        LIMIT :lexical_k
    ), scored AS (
        SELECT h.lesson_id, h.item_id, h.kind, h.all_terms, h.rank_cd,
               (SELECT count(*) FROM unnest(CAST(:tokens AS text[])) AS tok
                 WHERE h.content_tsv @@ plainto_tsquery('english'::regconfig, tok)) AS terms_hit
        FROM hits h
    )
    SELECT lesson_id, bool_or(all_terms) AS all_terms, max(rank_cd) AS rank_cd, max(terms_hit) AS terms_hit,
           (array_agg(item_id ORDER BY rank_cd DESC) FILTER (WHERE kind = ANY(CAST(:kinds AS text[]))))[1] AS item_id,
           (SELECT count(*) FROM unnest(CAST(:tokens AS text[])) AS tok
             WHERE numnode(plainto_tsquery('english'::regconfig, tok)) > 0) AS n_terms
    FROM scored
    GROUP BY lesson_id
    ORDER BY bool_or(all_terms) DESC, max(rank_cd) DESC, lesson_id
    """
)
TITLES = text(
    """
    SELECT lesson_id,
           max(word_sim) FILTER (WHERE chapter_id IS NULL) AS lesson_word_sim,
           max(sim) FILTER (WHERE chapter_id IS NULL) AS lesson_sim,
           max(word_sim) FILTER (WHERE chapter_id IS NOT NULL) AS chapter_word_sim,
           (array_agg(chapter_id ORDER BY word_sim DESC) FILTER (WHERE chapter_id IS NOT NULL))[1] AS chapter_id
    FROM (
        SELECT l.id AS lesson_id, NULL::uuid AS chapter_id,
               greatest(word_similarity(CAST(:topic AS text), l.title),
                        word_similarity(l.title, CAST(:topic AS text))) AS word_sim,
               similarity(l.title, CAST(:topic AS text)) AS sim
        FROM nhprep_lessons l
        WHERE l.removed_at IS NULL
          AND (CAST(:topic AS text) <% l.title OR l.title <% CAST(:topic AS text))
          AND (CAST(:courses AS uuid[]) IS NULL OR l.course_id = ANY(CAST(:courses AS uuid[])))
        UNION ALL
        SELECT i.lesson_id, i.id, word_similarity(CAST(:topic AS text), i.title),
               similarity(i.title, CAST(:topic AS text))
        FROM nhprep_lesson_items i
        JOIN nhprep_lessons l ON l.id = i.lesson_id
        WHERE i.kind = 'chapter' AND CAST(:topic AS text) <% i.title AND l.removed_at IS NULL
          AND (CAST(:courses AS uuid[]) IS NULL OR l.course_id = ANY(CAST(:courses AS uuid[])))
    ) t
    GROUP BY lesson_id
    ORDER BY max(word_sim) DESC, lesson_id
    LIMIT :title_k
    """
)
DAYS = text(
    "SELECT id FROM nhprep_lessons WHERE removed_at IS NULL AND source_day = :day "
    "AND course_id = ANY(CAST(:courses AS uuid[])) ORDER BY course_id, position, id"
)
FEATURES = text(
    """
    SELECT l.id, l.title, l.course_id, l.video_id,
           ARRAY(SELECT ct.trainer_id FROM nhprep_course_trainers ct
                  WHERE ct.course_id = l.course_id ORDER BY ct.trainer_id) AS trainer_ids,
           (SELECT 1 - min(c.embedding <=> :q) FROM nhprep_search_chunks c
             WHERE c.lesson_id = l.id AND c.embedding_model = :model) AS cosine
    FROM nhprep_lessons l JOIN nhprep_courses co ON co.id = l.course_id
    WHERE l.id = ANY(CAST(:ids AS uuid[])) AND l.removed_at IS NULL AND co.removed_at IS NULL
    """
).bindparams(bindparam("q", type_=_VECTOR))


@dataclass(frozen=True)
class Candidate:
    """One fused lesson and its confidence features (plan §8.3; spec r2-data-search §5)."""

    lesson_id: uuid.UUID
    score: float
    title: str = ""
    trainer_ids: tuple[uuid.UUID, ...] = ()
    video_id: uuid.UUID | None = None
    cosine: float | None = None
    lesson_word_sim: float = 0.0
    lesson_sim: float = 0.0
    chapter_word_sim: float = 0.0
    coverage: float = 0.0
    all_terms: bool = False
    item_id: uuid.UUID | None = None


# ------------------------------------------------------------------ pure: fusion and decision
def weighted_rrf(
    rankings: Mapping[str, Sequence[uuid.UUID]], weights: Mapping[str, float], k: int
) -> list[tuple[uuid.UUID, float]]:
    """Each leg is already one row per lesson, best first; ties break on the id (deterministic)."""
    scores: dict[uuid.UUID, float] = {}
    for leg, lessons in rankings.items():
        for rank, lesson_id in enumerate(lessons, start=1):
            scores[lesson_id] = scores.get(lesson_id, 0.0) + weights[leg] / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], str(item[0])))


_CONNECTORS = {"and", "the", "of", "a", "an", "to", "in", "on", "for", "with", "vs"}


def norm_title(value: str) -> str:
    """Lowercase words without connectors: "Static and Default Routing" == the parsed topic "static default routing"."""
    return " ".join(w for w in re.findall(r"[a-z0-9]+", value.lower()) if w not in _CONNECTORS)


def tokens(topic: str, limit: int) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9.+/_-]*", topic.lower()) if w not in _DROP_TOKENS]
    return list(dict.fromkeys(words))[:limit]


def _alternatives(primary: Candidate, others: Sequence[Candidate], limit: int) -> tuple[Match, ...]:
    """Ranked, at most `limit`, deduped by video id (and lesson id), never repeating the primary."""
    seen_videos = {primary.video_id} if primary.video_id else set()
    seen_lessons = {primary.lesson_id}
    picked: list[Match] = []
    for c in others:
        if c.lesson_id in seen_lessons or (c.video_id is not None and c.video_id in seen_videos):
            continue
        seen_lessons.add(c.lesson_id)
        if c.video_id is not None:
            seen_videos.add(c.video_id)
        picked.append(Match(c.lesson_id, c.score, c.item_id))
        if len(picked) == limit:
            break
    return tuple(picked)


def _evidence(c: Candidate, tuning: Mapping[str, Any]) -> int:
    """How many kinds of evidence back a candidate: meaning (cosine), its title or a chapter title, every term."""
    return (
        int(c.cosine is not None and c.cosine >= tuning["min_cosine"])
        + int(max(c.lesson_word_sim, c.chapter_word_sim) >= tuning["min_title_sim"])
        + int(c.all_terms)
    )


def decide(
    candidates: Sequence[Candidate], topic: str, tuning: Mapping[str, Any]
) -> tuple[Status, Candidate | None, tuple[Match, ...], str]:
    """(status, primary, alternatives, reason) from the fused candidates, best first.

    1. An exact title (normalised equality, or lesson similarity >= exact_title_sim): confident, unless the exact
       matches are taught by different trainers (ambiguous: the student picks the trainer).
    2. Else evidence (cosine >= min_cosine, a lesson or chapter title word similarity >= min_title_sim, or every
       topic term in one chunk): confident when the term coverage >= min_coverage and the next distinct lesson is
       clearly behind (fused margin >= min_margin, or fewer kinds of evidence: RRF margins are tiny when both lessons
       rank in every leg); otherwise ambiguous with up to max_options lessons.
    3. Else a title near enough (word similarity >= min_suggest_sim and term coverage >= min_suggest_coverage, e.g.
       one misspelt word): ambiguous; else none.
    """
    if not candidates:
        return "none", None, (), "no_candidates"
    options = int(tuning["max_options"]) - 1
    wanted = norm_title(topic)
    exact = [
        c for c in candidates if (wanted and norm_title(c.title) == wanted) or c.lesson_sim >= tuning["exact_title_sim"]
    ]
    if exact:
        primary = exact[0]
        rest = [*exact[1:], *(c for c in candidates if c not in exact)]
        if len({c.trainer_ids for c in exact}) > 1:
            return "ambiguous", primary, _alternatives(primary, rest, options), "exact_title_trainers"
        return "confident", primary, _alternatives(primary, rest, options), "exact_title"
    top = candidates[0]
    if not _evidence(top, tuning):
        near = max(top.lesson_word_sim, top.chapter_word_sim) >= tuning["min_suggest_sim"]
        if near and top.coverage >= tuning["min_suggest_coverage"]:  # a misspelt title: most words right, offer it
            return "ambiguous", top, _alternatives(top, candidates[1:], options), "near_title"
        return "none", None, (), "weak_evidence"
    # Same title and same trainers (a lesson repeated in two of one trainer's courses) is one choice, not a rival.
    rival = next(
        (c for c in candidates[1:] if (norm_title(c.title), c.trainer_ids) != (norm_title(top.title), top.trainer_ids)),
        None,
    )
    margin = 1.0 if rival is None else (top.score - rival.score) / top.score
    ahead = margin >= tuning["min_margin"] or _evidence(top, tuning) > _evidence(rival, tuning)  # type: ignore[arg-type]
    if ahead and top.coverage >= tuning["min_coverage"]:
        return "confident", top, _alternatives(top, candidates[1:], options), "clear_winner"
    return "ambiguous", top, _alternatives(top, candidates[1:], options), "close_or_partial"


# ------------------------------------------------------------------ the finder
def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _model(embeddings: object | None) -> str | None:
    if embeddings is None or not getattr(embeddings, "configured", True):
        return None
    return getattr(embeddings, "model", type(embeddings).__name__)


async def _embed(embeddings: QueryEmbedder, topic: str, budget_s: float, trace: dict[str, Any]) -> list[float] | None:
    """The query vector, or None (the dense leg is skipped and the reason recorded; title and lexical still run)."""
    started = time.monotonic()
    try:
        async with asyncio.timeout(budget_s):
            return await embeddings.aembed_query(topic)
    except TimeoutError:
        trace["reasons"].append("embedding_timeout")
    except Exception as error:  # noqa: BLE001 - a failed embedding drops one leg, never the finder
        trace["reasons"].append(f"embedding_error:{type(error).__name__}")
    finally:
        trace["ms"]["embed"] = _ms(started)
    return None


async def _courses(session: AsyncSession, query: NhprepQuery, trace: dict[str, Any]) -> tuple[list | None, list | None]:
    """(hard filter, course-hint courses). A trainer resolves only through the alias table; unknown -> []."""
    hinted = None
    if query.course_hint and _HINT.fullmatch(query.course_hint):
        hinted = list((await session.execute(HINT_COURSES, {"hint": query.course_hint})).scalars()) or None
    if not query.trainer_hint:
        return None, hinted
    name = re.sub("[^a-z]", "", query.trainer_hint.lower())
    taught = list((await session.execute(TRAINER_COURSES, {"name": name})).scalars())
    trace["counts"]["trainer_courses"] = len(taught)
    narrowed = [c for c in taught if hinted and c in hinted]
    return narrowed or taught, hinted


async def _legs(
    session: AsyncSession,
    query: NhprepQuery,
    vector: list[float] | None,
    model: str | None,
    courses: list | None,
    hinted: list | None,
    tuning: Mapping[str, Any],
    trace: dict[str, Any],
) -> tuple[dict[str, list[uuid.UUID]], dict[uuid.UUID, dict[str, Any]]]:
    rankings: dict[str, list[uuid.UUID]] = {}
    features: dict[uuid.UUID, dict[str, Any]] = {}
    kinds = sorted({ITEM_KINDS[v] for v in query.views if v in ITEM_KINDS}) or [""]
    topic = query.text.strip().lower()
    words = tokens(topic, int(tuning["max_tokens"]))
    base = {"courses": courses, "kinds": kinds}

    async def leg(name: str, statement: Any, params: dict[str, Any]) -> list[Any]:
        started = time.monotonic()
        rows = list((await session.execute(statement, {**base, **params})).mappings())
        trace["ms"][name], trace["counts"][name] = _ms(started), len(rows)
        return rows

    if vector is not None and model is not None:
        rows = await leg("dense", DENSE, {"q": vector, "model": model, "dense_k": int(tuning["dense_k"])})
        rankings["dense"] = [r["lesson_id"] for r in rows]
        for r in rows:
            features.setdefault(r["lesson_id"], {}).update(cosine=float(r["cosine"]), dense_item=r["item_id"])
    if words:
        # Lesson headers (titles, chapter outline) plus the asked aids: the long note and card chunks cost the most
        # to rank (tuning lexical_all_kinds puts them back; the dense leg covers their meaning).
        lexical_kinds = None if tuning.get("lexical_all_kinds") else ["lesson", *(k for k in kinds if k)]
        params = {"topic": " ".join(words), "any_text": " or ".join(words), "tokens": words, "model": model}
        params["lexical_kinds"] = lexical_kinds
        rows = await leg("lexical", LEXICAL, {**params, "lexical_k": int(tuning["lexical_k"])})
        rankings["lexical"] = [r["lesson_id"] for r in rows]
        for r in rows:
            coverage = r["terms_hit"] / r["n_terms"] if r["n_terms"] else 0.0
            features.setdefault(r["lesson_id"], {}).update(
                coverage=float(coverage), all_terms=bool(r["all_terms"]), lexical_item=r["item_id"]
            )
    if topic:
        rows = await leg("title", TITLES, {"topic": topic, "title_k": int(tuning["title_k"])})
        rankings["title"] = [r["lesson_id"] for r in rows]
        for r in rows:
            features.setdefault(r["lesson_id"], {}).update(
                lesson_word_sim=float(r["lesson_word_sim"] or 0.0),
                lesson_sim=float(r["lesson_sim"] or 0.0),
                chapter_word_sim=float(r["chapter_word_sim"] or 0.0),
                chapter_id=r["chapter_id"],
            )
    day_courses = courses or hinted
    if query.day is not None and day_courses:
        rows = await leg("day", DAYS, {"day": query.day, "courses": day_courses})
        rankings["day"] = [r["id"] for r in rows]
    return rankings, features


async def answer(
    db: Database,
    embeddings: QueryEmbedder | None,
    query: NhprepQuery,
    timeout_s: float = 1.0,
    *,
    tuning: Mapping[str, Any] | None = None,
    within: Sequence[uuid.UUID] | None = None,
) -> FinderResult:
    """The lesson the request names. Never raises: a timeout or error is a status, and the normal turn runs.
    `within`: a program chat's month (app/programs): only these courses, whatever the message names."""
    tuning = tuning or TUNING
    trace: dict[str, Any] = {"ms": {}, "counts": {}, "reasons": []}
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout_s):
            result = await _find(db, embeddings, query, timeout_s, tuning, trace, started, within)
    except TimeoutError:
        result = FinderResult("timeout", trace={**trace, "reason": "timeout"})
    except Exception as error:  # noqa: BLE001 - the finder never fails a turn
        name = type(error).__name__
        result = FinderResult("error", trace={**trace, "reason": f"error:{name}", "errorType": name})
    result.trace["ms"]["total"] = _ms(started)
    return result


async def _find(
    db: Database,
    embeddings: QueryEmbedder | None,
    query: NhprepQuery,
    timeout_s: float,
    tuning: Mapping[str, Any],
    trace: dict[str, Any],
    started: float,
    within: Sequence[uuid.UUID] | None = None,
) -> FinderResult:
    model = _model(embeddings)
    vector = None
    if model is not None and query.text.strip():
        vector = await _embed(embeddings, query.text.strip(), timeout_s * float(tuning["embed_share"]), trace)  # type: ignore[arg-type]
    elif model is None:
        trace["reasons"].append("embeddings_not_configured")
    remaining_ms = max(1, int((timeout_s - (time.monotonic() - started)) * 1000))
    async with db.sessionmaker() as session, session.begin():
        s0 = time.monotonic()
        await session.execute(
            SETTINGS,
            {
                "ef_search": str(int(tuning["ef_search"])),
                "title_floor": str(float(tuning["title_floor"])),
                "timeout_ms": str(min(int(tuning["statement_timeout_ms"]), remaining_ms)),
            },
        )
        courses, hinted = await _courses(session, query, trace)
        if within is not None:  # a hard filter every leg applies; an empty one finds nothing
            allowed = set(within)
            courses = [course for course in (courses if courses is not None else within) if course in allowed]
            hinted = ([course for course in hinted if course in allowed] or None) if hinted else None
        trace["ms"]["settings"] = _ms(s0)
        if query.trainer_hint and not courses:
            return FinderResult("none", trace={**trace, "reason": "trainer_unknown"})
        rankings, features = await _legs(session, query, vector, model, courses, hinted, tuning, trace)
        if "day" in rankings and not rankings["day"]:
            return FinderResult("none", trace={**trace, "reason": "day_not_found"})
        fused = weighted_rrf(rankings, tuning["weights"], tuning["rrf_k"])
        ids = [lesson for lesson, _ in fused[: max(int(tuning["limit"]), len(rankings.get("day", ())))]]
        s4 = time.monotonic()
        rows = (
            (await session.execute(FEATURES, {"ids": ids, "q": vector, "model": model or ""})).mappings() if ids else []
        )
        facts = {r["id"]: r for r in rows}
        trace["ms"]["features"] = _ms(s4)
    if hinted and not courses:  # a soft boost: lessons of the course the student named, in fused order
        rankings["course"] = [lesson for lesson, _ in fused if lesson in facts and facts[lesson]["course_id"] in hinted]
        fused = weighted_rrf(rankings, tuning["weights"], tuning["rrf_k"])
    if "day" in rankings:  # the day names the lesson: nothing outside it
        fused = [(lesson, score) for lesson, score in fused if lesson in set(rankings["day"])]
    candidates = [
        _candidate(lesson, score, facts[lesson], features.get(lesson, {}), query, tuning)
        for lesson, score in fused
        if lesson in facts
    ]
    if "day" in rankings and not query.text.strip() and candidates:  # only the day names it: one lesson, or a choice
        primary = candidates[0]
        alternatives = _alternatives(primary, candidates[1:], int(tuning["max_options"]) - 1)
        status, reason = ("ambiguous" if alternatives else "confident"), "day_only"
    else:
        status, primary, alternatives, reason = decide(candidates, query.text, tuning)
    trace.update(reason=reason, candidates=len(candidates), legs=sorted(rankings))
    if primary is not None:
        trace["features"] = {
            "cosine": primary.cosine,
            "lesson_word_sim": round(primary.lesson_word_sim, 3),
            "lesson_sim": round(primary.lesson_sim, 3),
            "chapter_word_sim": round(primary.chapter_word_sim, 3),
            "coverage": round(primary.coverage, 3),
            "all_terms": primary.all_terms,
        }
    if status not in ("confident", "ambiguous") or primary is None:
        return FinderResult(status, trace=trace)
    return FinderResult(status, Match(primary.lesson_id, primary.score, primary.item_id), alternatives, trace)


def _candidate(
    lesson: uuid.UUID,
    score: float,
    fact: Mapping[str, Any],
    feature: Mapping[str, Any],
    query: NhprepQuery,
    tuning: Mapping[str, Any],
) -> Candidate:
    """item_id: the matched chapter when chapters were asked for (or the chapter title matched), else the matched
    note / cheat-sheet block / card of an asked kind."""
    chapter = (
        feature.get("chapter_id")
        if ("chapters" in query.views or feature.get("chapter_word_sim", 0.0) >= tuning["min_title_sim"])
        else None
    )
    item = chapter or feature.get("lexical_item") or feature.get("dense_item")
    return Candidate(
        lesson_id=lesson,
        score=score,
        title=fact["title"],
        trainer_ids=tuple(fact["trainer_ids"] or ()),
        video_id=fact["video_id"],
        cosine=float(fact["cosine"]) if fact["cosine"] is not None else feature.get("cosine"),
        lesson_word_sim=feature.get("lesson_word_sim", 0.0),
        lesson_sim=feature.get("lesson_sim", 0.0),
        chapter_word_sim=feature.get("chapter_word_sim", 0.0),
        coverage=feature.get("coverage", 0.0),
        all_terms=feature.get("all_terms", False),
        item_id=item,
    )
