"""NHprep import (plan rev 4 §8.2, G2): export v1 validation, normalisation, chunking, vectors, and the importer
against the test database: idempotent reruns, the lock, removals, capacity, precomputed vectors and purge.

The fixture (tests/fixtures/nhprep/export-v1-sample.jsonl) is synthetic: shapes from the frozen export v1 contract,
never NHprep's text.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import uuid
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.db.session import Database
from app.nhprep import importer
from app.nhprep.importer import ImportBusy, ImportOptions, ImportRefused, purge, run_import
from app.nhprep.normalize import clean, lesson_chunks, plan_lesson, resolve_trainer
from app.nhprep.repository import NHPREP_LOCK_KEY, LockLost
from app.nhprep.validate import CourseRecord, LessonRecord, read_export
from app.nhprep.vectors import PrecomputedEmbeddings, read_vectors, text_key, write_vectors
from app.rag.embeddings import KeywordHashEmbeddings
from tests.conftest import SpyEmbeddings, make_settings

FIXTURE = Path(__file__).parent / "fixtures" / "nhprep" / "export-v1-sample.jsonl"
L1, L2, L4 = (uuid.UUID(f"{n * 8}-{n * 4}-4{n * 3}-8{n * 3}-{n * 12}") for n in "124")
EGRESS = "test: fake embeddings, nothing leaves"


def records() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def bundle(rows: list[dict], *, complete: bool = True, counts: dict | None = None):
    data = "".join(json.dumps(row) + "\n" for row in rows).encode()
    manifest = {
        "schema": 1,
        "exporterVersion": "nhprep-export/1",
        "complete": {"lessons": complete, "quiz": complete},
        "sha256": hashlib.sha256(data).hexdigest(),
        "counts": counts if counts is not None else dict(Counter(row["type"] for row in rows)),
    }
    return read_export(data, manifest, None)


def spy() -> SpyEmbeddings:
    return SpyEmbeddings(KeywordHashEmbeddings(1536), model="fake:nhprep-test")


def options(**overrides) -> ImportOptions:
    return ImportOptions(**{"apply": True, "egress_basis": EGRESS, "batch_size": 4, **overrides})


@pytest.fixture
async def db(database):
    handle = Database(make_settings(database_url=database))
    yield handle
    await handle.dispose()


async def scalar(db: Database, sql: str, **params):
    async with db.sessionmaker() as session:
        return await session.scalar(text(sql), params)


async def counts(db: Database) -> dict[str, int]:
    tables = ("courses", "trainers", "course_trainers", "lessons", "lesson_items", "search_chunks", "sync_runs")
    return {name: await scalar(db, f"SELECT count(*) FROM nhprep_{name}") for name in tables}


# ------------------------------------------------------------------ pure: validation, normalisation, chunks, vectors
def test_normalisation_neutralises_widgets_directives_invisible_characters_and_nhprep_links():
    found: Counter[str] = Counter()
    raw = (
        "a\u200bb\u202ec\x00\n```video\nx\n```\n- ```quiz\n```\n> :::note\n::: tip\n"
        "[l](https://www.nhprep.com/course/x) <https://nhprep.com/a> http://cdn.nhprep.com/v.mp4 www.nhprep.com/z\n"
        "[ref]: https://nhprep.com/r\nkeep https://example.com/a and nhprep.com.evil.tld/p\n```bash\n::kept\n```"
    )
    out = clean(raw, found)
    assert out.startswith("abc\n```text\nx\n```\n- ```text\n```\n> \\:::note\n\\::: tip\n")
    assert "l nhprep.com/a cdn.nhprep.com/v.mp4 nhprep.com/z\n" in out
    assert "[ref]" not in out and "https://example.com/a" in out and " nhprep.com.evil.tld/p" in out
    assert "\n::kept\n" in out  # code inside a fence is left alone
    assert "http" not in out.replace("https://example.com/a", "") and "www." not in out
    assert found == Counter(invisible=3, fence_info=2, directive=2, nhprep_url=5)


@pytest.mark.parametrize(
    ("hints", "expected"),
    [
        ({"titleParens": "Tamil", "aboutBy": "Mr.AJAY", "slugToken": "x"}, ("Ajay", "description")),
        ({"titleParens": "Sushil", "aboutBy": None, "slugToken": None}, ("Sushil", "title")),
        ({"titleParens": None, "aboutBy": "Admin", "slugToken": "amit"}, ("Amit", "slug")),
        ({"titleParens": "Tamil", "aboutBy": "Admin", "slugToken": "b30"}, None),
    ],
)
def test_trainers_resolve_only_through_the_alias_table(hints, expected):
    course = CourseRecord.model_validate(
        {"type": "course", "schema": 1, "slug": "c", "title": "C", "trainerHints": hints, "lessonCount": 0}
    )
    assert resolve_trainer(course) == expected


@pytest.mark.parametrize(
    "video",
    [
        {"provider": "nhprep-mp4", "videoId": str(uuid.uuid4()), "url": "https://videos.example/x.mp4"},
        {"provider": "nhprep-mp4", "videoId": str(uuid.uuid4()), "exp": 1, "sig": "x"},
        {"provider": "bunny", "videoId": str(uuid.uuid4())},
        {"provider": "nhprep-mp4", "videoId": str(uuid.uuid4()), "libraryId": 7},
        {"provider": "youtube", "videoId": str(uuid.uuid4())},
        {"provider": "bunny", "libraryId": 7, "videoId": "not-a-uuid"},
    ],
)
def test_a_video_is_a_stable_reference_never_a_url_or_signature(video):
    lesson = records()[1] | {"video": video}
    with pytest.raises(ValidationError):
        LessonRecord.model_validate(lesson)


def test_read_export_checks_bytes_counts_and_completeness_and_counts_rejects():
    rows = records()
    good = bundle(rows)
    assert good.errors == [] and good.complete
    assert good.rejects == Counter({"lesson:lessonType": 1}) and good.ignored == Counter(
        quiz_question=1, quiz_coverage=1
    )
    assert [lesson.external_id for lesson in good.lessons] == [L1, L2, L4]

    data = FIXTURE.read_bytes()
    assert read_export(data, None, None).errors  # a stream needs --sha256
    assert read_export(data, None, "0" * 64).errors  # ...and the right one
    streamed = read_export(data, None, hashlib.sha256(data).hexdigest())
    assert streamed.errors == [] and not streamed.complete  # a stream never removes anything
    assert bundle(rows, counts={"course": 2, "lesson": 9}).errors  # the manifest's counts must match

    broken = copy.deepcopy(rows)
    broken[2]["slug"] = "Bad Slug"  # a reject keeps its lesson "present": never removed because it failed
    broken[4]["lessonCount"] = 2  # a course whose lesson list is short: not complete
    export = bundle(broken)
    assert export.rejects["lesson:slug"] == 1 and L2 in export.present_lesson_ids and not export.complete


def test_an_underscore_upload_name_is_a_lesson_and_its_day_is_read_from_it():
    # 40 real NHprep lessons were rejected for slugs like "day_01_osi-modelmp4" (export v1 left their day null).
    rows = records()
    lesson = next(row for row in rows if row["type"] == "lesson")
    lesson["slug"], lesson["day"] = "day_03_static_routes-basicsmp4", None
    export = bundle(rows)
    assert export.rejects.get("lesson:slug", 0) == 0
    planned = plan_lesson(next(item for item in export.lessons if item.slug == lesson["slug"]), Counter())
    assert planned is not None and planned.values["source_day"] == 3


def test_chunks_group_items_dedupe_and_key_the_exact_embedded_text():
    export = bundle(records())
    found: Counter[str] = Counter()
    lesson = plan_lesson(export.lessons[0], found)
    chunks = lesson_chunks(lesson, "Routing Basics", "Ajay")
    kinds = [chunk.kind for chunk in chunks]
    assert kinds == ["lesson", "note", "cheat_sheet", "flashcard", "flashcard", "interview_question"]  # 12 cards: 10+2
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].item_key is None and chunks[4].item_key == ("flashcard", "f0011")
    assert "Trainer: Ajay" in chunks[0].content and "0:45 Why static routes" in chunks[0].content
    assert "day 1 static routes" in chunks[0].content  # the de-slugged alias, for the lexical leg
    assert all(chunk.key == text_key(f"{chunk.title}\n\n{chunk.content}") for chunk in chunks)
    assert all(0 < len(chunk.content) <= 4000 for chunk in chunks)


async def test_precomputed_vectors_round_trip_and_malformed_files_are_refused():
    vector = [0.5] * 1536
    stream = io.StringIO()
    write_vectors(stream, "model-a", [(text_key("hello"), vector)])
    lines = stream.getvalue().splitlines()
    embeddings = PrecomputedEmbeddings(*read_vectors(lines))
    assert embeddings.model == "model-a" and embeddings.has("hello") and not embeddings.has("other")
    assert await embeddings.aembed_documents(["hello"]) == [vector]
    for bad in (lines[:1], [lines[0].replace("1536", "3")], [*lines, lines[1].replace('"k": "', '"k": "x')]):
        with pytest.raises(ValueError, match="vectors_file_invalid"):
            read_vectors(bad)


# ------------------------------------------------------------------ the importer, on the test database
async def test_import_twice_writes_nothing_and_embeds_nothing_the_second_time(db):
    embeddings = spy()
    dry = await run_import(db, bundle(records()), embeddings, options(apply=False))
    assert dry["applied"] is False and embeddings.calls == 0
    assert set((await counts(db)).values()) == {0}  # a dry run writes nothing, not even a run row

    first = await run_import(db, bundle(records()), embeddings, options())
    assert first["applied"] and first["lesson_writes"] == {"inserted": 3} and first["embedded"] == dry["texts_missing"]
    assert first["normalised"] == {"invisible": 1, "fence_info": 1, "directive": 1, "nhprep_url": 4}
    stored = await counts(db)
    assert stored | {"sync_runs": 0} == {
        "courses": 2,
        "trainers": 1,
        "course_trainers": 1,
        "lessons": 3,
        "lesson_items": 23,
        "search_chunks": 11,
        "sync_runs": 0,
    }
    calls = embeddings.calls

    second = await run_import(db, bundle(records()), embeddings, options())
    assert embeddings.calls == calls and second["embedded"] == 0 and second["texts_missing"] == 0
    assert second["course_writes"] == {"unchanged": 2} and second["lesson_writes"] == {"unchanged": 3}
    assert second["item_writes"] == {} and second["to_index"] == 0
    assert second["chunk_writes"] == {"inserted": 0, "updated": 0, "deleted": 0}
    assert await counts(db) == stored | {"sync_runs": 2}
    runs = await scalar(db, "SELECT string_agg(status || ':' || embedding_model, ',') FROM nhprep_sync_runs")
    assert runs == "succeeded:fake:nhprep-test"  # the second run embedded nothing: no model recorded
    assert await scalar(db, "SELECT count(*) FROM nhprep_sync_runs WHERE status = 'succeeded'") == 2
    embed = "SELECT video_embed_url FROM nhprep_lessons WHERE external_id = :id"
    assert await scalar(db, embed, id=L2) == "https://player.mediadelivery.net/embed/12345/" + str(
        uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
    )
    assert await scalar(db, embed, id=L1) is None  # NHprep's own MP4 host: no URL is ever stored


async def test_a_changed_card_rewrites_only_its_lessons_chunk_and_an_unavailable_kind_is_kept(db):
    await run_import(db, bundle(records()), spy(), options())
    rows = records()
    rows[2]["aids"]["artifacts"]["flashcards"]["items"][0]["back"] = "Changed synthetic answer."
    rows[1]["aids"]["artifacts"]["cheatsheet"] = {"availability": "error", "reason": "Synthetic.", "items": []}
    embeddings = spy()
    report = await run_import(db, bundle(rows), embeddings, options())
    assert report["lesson_writes"] == {"updated": 2, "unchanged": 1} and report["embedded"] == 1
    assert report["item_writes"] == {"updated": 1, "unchanged": 19}
    # L2's card chunk is replaced; L1's later chunks only renumber (the kept cheat-sheet chunk is not regenerated)
    assert report["chunk_writes"] == {"inserted": 1, "updated": 3, "deleted": 1}
    sheet = "SELECT count(*) FROM nhprep_lesson_items WHERE kind = 'cheat_sheet'"
    assert await scalar(db, sheet) == 2  # NHprep said "error", not "gone": what we have stays
    assert await scalar(db, "SELECT count(*) FROM nhprep_search_chunks WHERE kind = 'cheat_sheet'") == 1


async def test_a_second_import_waits_for_nobody_it_refuses_while_the_lock_is_held(db):
    async with db.engine.connect() as holder:
        assert await holder.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": NHPREP_LOCK_KEY})
        with pytest.raises(ImportBusy):
            await run_import(db, bundle(records()), spy(), options())
        await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": NHPREP_LOCK_KEY})
        await holder.commit()
    assert (await counts(db))["sync_runs"] == 0


async def test_a_lost_lock_stops_the_run_marks_it_failed_and_a_rerun_completes(db):
    class Killer(SpyEmbeddings):
        async def aembed_documents(self, texts):
            if self.calls == 0:  # the lock connection dies while the provider works
                async with db.engine.connect() as other:
                    await other.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' AND granted "
                            "AND ((classid::bigint << 32) | objid::bigint) = :k"
                        ),
                        {"k": NHPREP_LOCK_KEY},
                    )
                    await other.commit()
            return await super().aembed_documents(texts)

    with pytest.raises(LockLost):
        await run_import(
            db, bundle(records()), Killer(KeywordHashEmbeddings(1536), model="fake:nhprep-test"), options()
        )
    assert await scalar(db, "SELECT status || ':' || (report->>'error') FROM nhprep_sync_runs") == "failed:lock_lost"
    assert await scalar(db, "SELECT count(*) FROM nhprep_search_chunks") == 0  # no write after the lock went

    report = await run_import(db, bundle(records()), spy(), options())
    assert report["applied"] and (await counts(db))["search_chunks"] == 11
    statuses = "SELECT string_agg(status, ',' ORDER BY started_at) FROM nhprep_sync_runs"
    assert await scalar(db, statuses) == "failed,succeeded"


async def test_removals_need_a_complete_export_and_a_mass_removal_needs_allow_removals(db):
    await run_import(db, bundle(records()), spy(), options())
    without_l2 = [row for row in records() if row.get("external_id") != str(L2)]
    without_l2[0]["lessonCount"] = 2

    partial = await run_import(db, bundle(without_l2, complete=False), spy(), options())
    assert partial["removals"] == {"courses": 0, "lessons": 0}
    with pytest.raises(ImportRefused) as refused:  # 1 of 3 lessons is far over 5%
        await run_import(db, bundle(without_l2), spy(), options())
    assert refused.value.code == "mass_removal"
    removed = await run_import(db, bundle(without_l2), spy(), options(allow_removals=1))
    assert removed["removals"] == {"courses": 0, "lessons": 1}
    gone = "SELECT removed_at IS NOT NULL FROM nhprep_lessons WHERE external_id = :id"
    assert await scalar(db, gone, id=L2) is True
    assert (
        await scalar(
            db,
            "SELECT count(*) FROM nhprep_search_chunks c JOIN nhprep_lessons l ON l.id = c.lesson_id "
            "WHERE l.external_id = :id",
            id=L2,
        )
        == 0
    )

    back = await run_import(db, bundle(records()), spy(), options())  # it comes back: live again
    assert back["lesson_writes"]["updated"] == 1 and await scalar(db, gone, id=L2) is False

    no_firewall = [row for row in records() if "firewall" not in json.dumps(row)]
    with pytest.raises(ImportRefused, match="mass_removal"):  # a whole course always needs the flag
        await run_import(db, bundle(no_firewall), spy(), options())
    report = await run_import(db, bundle(no_firewall), spy(), options(allow_removals=2))
    assert report["removals"] == {"courses": 1, "lessons": 1}


async def test_a_lesson_moving_to_another_course_takes_its_chunks_along(db):
    await run_import(db, bundle(records()), spy(), options())
    rows = records()
    rows[2]["course"] = "firewall-essentials-b30"  # OSPF Areas moves
    rows[0]["lessonCount"], rows[4]["lessonCount"] = 2, 2
    report = await run_import(db, bundle(rows), spy(), options())
    assert report["removals"] == {"courses": 0, "lessons": 0}
    moved = (
        "SELECT count(*) FROM nhprep_search_chunks c JOIN nhprep_lessons l ON l.id = c.lesson_id "
        "JOIN nhprep_courses k ON k.id = c.course_id WHERE l.external_id = :id AND k.slug = :slug"
    )
    assert await scalar(db, moved, id=L2, slug="firewall-essentials-b30") == 3
    assert (
        await scalar(
            db,
            "SELECT count(*) FROM nhprep_search_chunks c JOIN nhprep_lessons l ON l.id = c.lesson_id "
            "WHERE c.course_id <> l.course_id",
        )
        == 0
    )


async def test_precomputed_vectors_are_the_only_embedder_and_a_missing_text_refuses_before_writing(db, tmp_path):
    emitted = io.StringIO()
    await run_import(db, bundle(records()), spy(), options(apply=False, emit_texts=emitted))
    texts = [json.loads(line) for line in emitted.getvalue().splitlines()]
    assert len(texts) == 11 and all(text_key(item["t"]) == item["k"] for item in texts)

    vectors = io.StringIO()
    write_vectors(vectors, "host-model", [(item["k"], [0.25] * 1536) for item in texts[1:]])
    partial = PrecomputedEmbeddings(*read_vectors(vectors.getvalue().splitlines()))
    with pytest.raises(ImportRefused) as refused:
        await run_import(db, bundle(records()), partial, options())
    assert refused.value.code == "vectors_missing" and set((await counts(db)).values()) == {0}

    vectors = io.StringIO()
    write_vectors(vectors, "host-model", [(item["k"], [0.25] * 1536) for item in texts])
    complete = PrecomputedEmbeddings(*read_vectors(vectors.getvalue().splitlines()))
    report = await run_import(db, bundle(records()), complete, options())
    assert report["embedded"] == 11
    assert (
        await scalar(db, "SELECT string_agg(DISTINCT embedding_model, ',') FROM nhprep_search_chunks") == "host-model"
    )


async def test_refusals_capacity_and_egress_basis_write_nothing(db, monkeypatch):
    with pytest.raises(ImportRefused) as refused:
        await run_import(db, bundle(records()), spy(), options(egress_basis=None))
    assert refused.value.code == "egress_basis_required"
    monkeypatch.setattr(importer, "NHPREP_RESERVE_BYTES", importer.DB_CEILING_BYTES)  # no headroom at all
    with pytest.raises(ImportRefused) as refused:
        await run_import(db, bundle(records()), spy(), options())
    assert refused.value.code == "capacity"
    with pytest.raises(ImportRefused, match="invalid_export"):
        await run_import(db, read_export(FIXTURE.read_bytes(), None, None), spy(), options())
    assert set((await counts(db)).values()) == {0}


async def test_purge_deletes_the_catalogue_and_keeps_the_audit(db):
    await run_import(db, bundle(records()), spy(), options())
    would = await purge(db, operator="local", reason="test", apply=False)
    assert would["nhprep_lessons"] == 3 and (await counts(db))["lessons"] == 3
    deleted = await purge(db, operator="local", reason="test rollback", apply=True)
    assert deleted["nhprep_search_chunks"] == 11
    assert await counts(db) | {"sync_runs": 0} == dict.fromkeys(await counts(db), 0)
    kinds = "SELECT string_agg(kind || ':' || status, ',' ORDER BY started_at) FROM nhprep_sync_runs"
    assert await scalar(db, kinds) == "import:succeeded,purge:succeeded"
