"""The NHprep finder (app/nhprep/search.py, plan rev 4 §8.3; spec r2-data-search §5-§6): fusion and decision rules
(pure), then the SQL legs against the test database on a synthetic catalogue (never NHprep's text): index plans,
English stemming, partial titles, chapter matches, the trainer filter (precision 1.0), day-N, timeout and error."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter

import pytest
from sqlalchemy import bindparam, text

from app.db.session import Database
from app.nhprep import search
from app.nhprep.importer import ImportOptions, run_import
from app.nhprep.search import Candidate, decide, tokens, weighted_rrf
from app.nhprep.types import NhprepQuery
from app.nhprep.validate import read_export
from app.rag.embeddings import KeywordHashEmbeddings
from tests.conftest import make_settings

T = search.TUNING
A, B, C, D = (uuid.UUID(int=n) for n in (1, 2, 3, 4))
AJAY, SUSHIL = (uuid.UUID(int=n) for n in (101, 102))


# ------------------------------------------------------------------ pure
def test_weighted_rrf_adds_weighted_reciprocal_ranks_and_breaks_ties_by_id() -> None:
    fused = weighted_rrf({"lexical": [A, B], "title": [B, C]}, {"lexical": 1.0, "title": 2.0}, 10)
    assert [lesson for lesson, _ in fused] == [B, C, A]
    assert fused[0][1] == pytest.approx(1 / 12 + 2 / 11)
    assert [lesson for lesson, _ in weighted_rrf({"x": [B], "y": [A]}, {"x": 1.0, "y": 1.0}, 60)] == [A, B]


def test_tokens_drop_boolean_words_and_duplicates() -> None:
    assert tokens("OSPF or BGP and not -rip ospf sd-wan", 8) == ["ospf", "bgp", "rip", "sd-wan"]
    assert tokens("a b c d e f g h i j", 8) == list("abcdefgh")


def cand(lesson: uuid.UUID, score: float, title: str, trainers=(AJAY,), **features) -> Candidate:
    return Candidate(lesson, score, title, tuple(trainers), uuid.uuid4(), **features)


def test_an_exact_title_is_confident_unless_two_trainers_teach_it() -> None:
    one = [cand(A, 0.05, "Static Routes"), cand(B, 0.06, "OSPF Areas", coverage=1.0, all_terms=True)]
    status, primary, _, reason = decide(sorted(one, key=lambda c: -c.score), "static routes", T)
    assert (status, primary.lesson_id, reason) == ("confident", A, "exact_title")
    two = [cand(A, 0.06, "Static Routes"), cand(B, 0.05, "Static routes", trainers=(SUSHIL,))]
    status, primary, alternatives, reason = decide(two, "static routes", T)
    assert (status, primary.lesson_id, [m.lesson_id for m in alternatives]) == ("ambiguous", A, [B])
    assert reason == "exact_title_trainers"


def test_a_clear_covered_winner_is_confident_and_a_close_one_ambiguous() -> None:
    clear = [cand(A, 0.10, "OSPF DR/BDR Election", coverage=1.0, all_terms=True), cand(B, 0.02, "OSPF Areas")]
    assert decide(clear, "ospf dr", T)[0] == "confident"
    close = [
        cand(A, 0.10, "OSPF DR/BDR Election", coverage=1.0, all_terms=True),
        cand(B, 0.099, "OSPF Areas", all_terms=True),
    ]
    status, _, alternatives, _ = decide(close, "ospf dr", T)
    assert status == "ambiguous" and [m.lesson_id for m in alternatives] == [B]
    behind = [close[0], cand(B, 0.099, "OSPF Areas")]  # as close, but with less evidence: clearly behind
    assert decide(behind, "ospf dr", T)[0] == "confident"
    partial = [cand(A, 0.10, "OSPF DR/BDR Election", lesson_word_sim=0.9, coverage=0.5)]
    assert decide(partial, "ospf dr timers", T)[0] == "ambiguous"


def test_a_near_title_with_most_words_right_is_offered_not_asserted() -> None:
    typo = [cand(A, 0.1, "Unicast Reverse Path Forwarding", lesson_word_sim=0.55, coverage=0.67)]
    assert decide(typo, "uniast reverse path", T)[::3] == ("ambiguous", "near_title")
    off_topic = [cand(A, 0.1, "Cloud Computing Threats", lesson_word_sim=0.55, coverage=0.5)]
    assert decide(off_topic, "quantum computing", T)[0] == "none"


def test_no_evidence_is_none_and_alternatives_are_capped_and_deduped_by_video() -> None:
    assert decide([cand(A, 0.1, "Linux Users", coverage=0.5)], "kubernetes helm", T)[:2] == ("none", None)
    assert decide([], "x", T)[0] == "none"
    video = uuid.uuid4()
    many = [
        Candidate(uuid.UUID(int=n), 1.0 / n, f"t{n}", (AJAY,), video if n in (2, 3) else None) for n in range(1, 12)
    ]
    many[0] = Candidate(many[0].lesson_id, 1.0, "t1", (AJAY,), None, all_terms=True, coverage=1.0)
    _, _, alternatives, _ = decide(many, "zzz", T)
    ids = [m.lesson_id.int for m in alternatives]
    assert len(ids) == T["max_options"] - 1 and 3 not in ids and ids[0] == 2


# ------------------------------------------------------------------ database
V = {
    name: uuid.UUID(f"{n:08d}-0000-4000-8000-{n:012d}")
    for n, name in enumerate("dr areas static vlan s_static".split(), 1)
}


def aids(notes=(), chapters=(), flashcards=()) -> dict:
    def kind(items):
        return (
            {"availability": "available", "reason": None, "items": list(items)}
            if items
            else {"availability": "not_applicable", "reason": "Synthetic.", "items": []}
        )

    return {
        "schemaVersion": 2, "language": "en", "generatedAt": "2026-11-19T00:00:00.000Z", "status": "ready",
        "reviewNotice": False,
        "artifacts": {
            "schemaVersion": 2,
            "notes": kind({"id": f"n{i}", "title": t, "markdown": b} for i, (t, b) in enumerate(notes)),
            "chapters": kind({"id": f"c{i}", "title": t, "start": 60 * i, "end": 60 * i + 50}
                             for i, t in enumerate(chapters)),
            "cheatsheet": kind(()),
            "flashcards": kind({"id": f"f{i}", "front": f, "back": b} for i, (f, b) in enumerate(flashcards)),
            "interviewQuestions": kind(()),
        },
    }  # fmt: skip


def lesson(key: str, course: str, position: int, title: str, day: int | None, **kwargs) -> dict:
    return {
        "type": "lesson", "schema": 1, "external_id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)), "course": course,
        "slug": f"day-{day}-{key.replace('_', '-')}mp4", "title": title, "position": position,
        "day": day, "lessonType": None, "durationSeconds": 1200,
        "video": {"provider": "nhprep-mp4", "videoId": str(V[key])}, "aids": aids(**kwargs),
    }  # fmt: skip


def course(slug: str, title: str, trainer: str, count: int) -> dict:
    hints = {"titleParens": trainer, "aboutBy": None, "slugToken": trainer.lower()}
    return {"type": "course", "schema": 1, "slug": slug, "title": title, "description": "", "trainerHints": hints,
            "lessonCount": count}  # fmt: skip


ROWS = [
    course("routing-by-ajay", "CCNA Routing (Ajay)", "Ajay", 3),
    lesson("dr", "routing-by-ajay", 1, "OSPF DR/BDR Election", 1,
           chapters=["Why a designated router", "Priority and tie breaks"],
           notes=[("Election", "The highest priority wins.")]),
    lesson("areas", "routing-by-ajay", 2, "OSPF Areas", 2,
           notes=[("Areas", "Configuring OSPF areas on the area border router.")]),
    lesson("static", "routing-by-ajay", 3, "Static Routes", 3),
    course("switching-by-sushil", "Switching Basics (Sushil)", "Sushil", 2),
    lesson("vlan", "switching-by-sushil", 1, "VLAN Trunking", 1,
           flashcards=[("Which protocol tags frames on a trunk?", "802.1Q adds a four-byte tag.")]),
    lesson("s_static", "switching-by-sushil", 2, "Static Routes", 2),
]  # fmt: skip
ID = {row["video"]["videoId"]: uuid.UUID(row["external_id"]) for row in ROWS if row["type"] == "lesson"}
EMBEDDINGS = KeywordHashEmbeddings(1536, model="fake:nhprep-search-test")


@pytest.fixture
async def db(database):
    handle = Database(make_settings(database_url=database))
    data = "".join(json.dumps(row) + "\n" for row in ROWS).encode()
    manifest = {
        "schema": 1, "exporterVersion": "nhprep-export/1", "complete": {"lessons": True, "quiz": True},
        "sha256": hashlib.sha256(data).hexdigest(), "counts": dict(Counter(row["type"] for row in ROWS)),
    }  # fmt: skip
    options = ImportOptions(apply=True, egress_basis="test: fake embeddings", batch_size=8)
    await run_import(handle, read_export(data, manifest, None), EMBEDDINGS, options)
    yield handle
    await handle.dispose()


async def lesson_id(db: Database, video: str) -> uuid.UUID:
    async with db.sessionmaker() as session:
        return await session.scalar(text("SELECT id FROM nhprep_lessons WHERE video_id = :v"), {"v": V[video]})


async def find(db: Database, embeddings=None, **query) -> search.FinderResult:
    return await search.answer(db, embeddings, NhprepQuery(**{"views": ("video",), **query}), 5.0)


async def test_english_stemming_finds_a_lesson_by_its_notes(db) -> None:
    result = await find(db, text="configure ospf area")
    assert result.status in ("confident", "ambiguous")
    assert result.primary.lesson_id == await lesson_id(db, "areas")


async def test_a_partial_title_and_a_chapter_title_are_found(db) -> None:
    result = await find(db, text="ospf dr")
    assert (result.status, result.primary.lesson_id) == ("confident", await lesson_id(db, "dr"))
    chapter = await find(db, text="designated router", views=("chapters",))
    assert chapter.primary.lesson_id == await lesson_id(db, "dr") and chapter.primary.item_id is not None
    async with db.sessionmaker() as session:
        kind = await session.scalar(
            text("SELECT kind FROM nhprep_lesson_items WHERE id = :i"), {"i": chapter.primary.item_id}
        )
    assert kind == "chapter"


async def test_two_trainers_teaching_one_title_is_ambiguous_and_a_named_trainer_filters(db) -> None:
    both = await find(db, text="static routes")
    assert both.status == "ambiguous" and both.trace["reason"] == "exact_title_trainers"
    ajay, sushil = await lesson_id(db, "static"), await lesson_id(db, "s_static")
    assert {both.primary.lesson_id, *(m.lesson_id for m in both.alternatives)} >= {ajay, sushil}
    only = await find(db, text="static routes", trainer_hint="Mr. Sushil")
    assert (only.status, only.primary.lesson_id) == ("confident", sushil)
    sushil_lessons = {sushil, await lesson_id(db, "vlan")}
    for topic in ("static routes", "ospf", "routes"):  # precision 1.0: nothing outside the trainer's courses
        result = await find(db, text=topic, trainer_hint="sushil")
        found = {m.lesson_id for m in (result.primary, *result.alternatives) if m is not None}
        assert found <= sushil_lessons, topic


async def test_an_unknown_trainer_is_none_never_a_guess(db) -> None:
    result = await find(db, text="static routes", trainer_hint="mr bharath")
    assert (result.status, result.primary, result.trace["reason"]) == ("none", None, "trainer_unknown")


async def test_day_n_with_a_trainer_or_course_names_the_lesson(db) -> None:
    result = await find(db, text="", trainer_hint="ajay", day=2)
    assert (result.status, result.primary.lesson_id) == ("confident", await lesson_id(db, "areas"))
    hinted = await find(db, text="", course_hint="ccna", day=1)
    assert (hinted.status, hinted.primary.lesson_id) == ("confident", await lesson_id(db, "dr"))
    missing = await find(db, text="", trainer_hint="ajay", day=9)
    assert (missing.status, missing.trace["reason"]) == ("none", "day_not_found")


async def test_a_requested_study_aid_sets_the_matched_item_and_dense_runs_with_embeddings(db) -> None:
    result = await find(db, EMBEDDINGS, text="trunk tag frames", views=("flashcards",))
    assert result.primary.lesson_id == await lesson_id(db, "vlan")
    assert result.primary.item_id is not None and result.trace["counts"]["dense"] > 0
    assert set(json.dumps(result.trace)) and "trunk" not in json.dumps(result.trace)  # ids and numbers, never text


async def test_unrelated_topics_are_none(db) -> None:
    assert (await find(db, text="kubernetes helm charts")).status == "none"


async def test_timeout_and_errors_are_statuses_not_exceptions(db) -> None:
    class Slow:
        model = EMBEDDINGS.model

        async def aembed_query(self, text):
            import asyncio

            await asyncio.sleep(1)
            return EMBEDDINGS.embed_query(text)

    tuning = {**T, "embed_share": 10.0}  # the embedding outlives the whole deadline
    slow = await search.answer(db, Slow(), NhprepQuery("ospf dr", ("video",)), 0.05, tuning=tuning)
    assert slow.status == "timeout" and slow.primary is None
    broken = Database(make_settings(database_url="postgresql+asyncpg://nobody:x@127.0.0.1:1/none_test"))
    try:
        failed = await search.answer(broken, None, NhprepQuery("ospf dr", ("video",)), 5.0)
    finally:
        await broken.dispose()
    assert failed.status == "error" and failed.trace["reason"].startswith("error:")


async def test_each_leg_uses_its_index(db) -> None:
    vector = EMBEDDINGS.embed_query("ospf")
    params = {"courses": None, "kinds": [""], "lexical_kinds": None, "model": EMBEDDINGS.model}
    cases = [
        ("embedding_hnsw", search.DENSE, {**params, "q": vector, "dense_k": 200}, ("seqscan", "sort", "bitmapscan")),
        ("content_tsv_gin", search.LEXICAL, {**params, "topic": "ospf", "any_text": "ospf", "tokens": ["ospf"],
                                             "lexical_k": 200}, ("seqscan", "indexscan")),
    ]  # fmt: skip
    for index, statement, values, off in cases:
        async with db.sessionmaker() as session, session.begin():  # one transaction each: SET LOCAL stays put
            for setting in off:
                await session.execute(text(f"SET LOCAL enable_{setting} = off"))
            explain = text("EXPLAIN " + statement.text)
            if "q" in values:
                explain = explain.bindparams(bindparam("q", type_=search._VECTOR))
            plan = "\n".join((await session.execute(explain, values)).scalars())
            assert f"ix_nhprep_search_chunks_{index}" in plan
    # The S3 chapter branch (`:topic <% i.title`, commuted to `title %> :topic`) uses
    # ix_nhprep_lesson_items_chapter_title_trgm on a real catalogue; on this tiny one the planner rightly prefers the
    # unique (lesson_id, kind, item_key) index, so it is checked with scripts/eval_nhprep.py's database instead.
