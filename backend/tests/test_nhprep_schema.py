"""Migration 0027's NHprep tables (G3): the generated embed URL cannot be written and is never a signed URL, the video
and chapter checks hold, and no NHprep table holds user data (so account deletion needs nothing new)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

COURSE = uuid.UUID("c0000000-0000-4000-8000-000000000001")
HASH = "a" * 64
LESSON = (
    "INSERT INTO nhprep_lessons (id, external_id, course_id, position, title, slug, content_hash, video_provider, "
    "video_id, video_library_id) VALUES (:id, gen_random_uuid(), :course, 1, 'T', 't', :hash, :provider, :video, :lib)"
)


@pytest.fixture
async def conn(database):
    engine = create_async_engine(database)
    async with engine.connect() as connection:
        await connection.execute(
            text(
                "INSERT INTO nhprep_courses (id, external_id, slug, title, content_hash) VALUES (:c, 'c', 'c', 'C', :h)"
            ),
            {"c": COURSE, "h": HASH},
        )
        await connection.commit()
        yield connection
    await engine.dispose()


async def _lesson(conn, provider=None, video=None, library=None) -> uuid.UUID:
    lesson_id = uuid.uuid4()
    params = {"id": lesson_id, "course": COURSE, "hash": HASH, "provider": provider, "video": video, "lib": library}
    await conn.execute(text(LESSON), params)
    await conn.commit()
    return lesson_id


async def _refused(conn, sql: str, params: dict) -> bool:
    try:
        await conn.execute(text(sql), params)
    except DBAPIError:
        await conn.rollback()
        return True
    await conn.rollback()
    return False


async def test_the_embed_url_is_generated_canonical_and_cannot_be_written(conn):
    video = uuid.uuid4()
    bunny = await _lesson(conn, "bunny", video, 42)
    own = await _lesson(conn, "nhprep-mp4", uuid.uuid4())
    none = await _lesson(conn)
    url = "SELECT video_embed_url FROM nhprep_lessons WHERE id = :id"
    assert (await conn.scalar(text(url), {"id": bunny})) == f"https://player.mediadelivery.net/embed/42/{video}"
    assert (await conn.scalar(text(url), {"id": own})) is None  # NHprep's MP4 host: nothing NetSec can play yet
    assert (await conn.scalar(text(url), {"id": none})) is None
    signed = "https://player.mediadelivery.net/embed/42/x?token=" + "abc" + "&expires=1"
    assert await _refused(
        conn, "UPDATE nhprep_lessons SET video_embed_url = :u WHERE id = :id", {"u": signed, "id": bunny}
    )
    insert = LESSON.replace("video_library_id)", "video_library_id, video_embed_url)").replace(":lib)", ":lib, :u)")
    params = {"id": uuid.uuid4(), "course": COURSE, "hash": HASH, "provider": None, "video": None, "lib": None}
    assert await _refused(conn, insert, params | {"u": signed})


@pytest.mark.parametrize(
    ("provider", "video", "library"),
    [
        ("bunny", uuid.uuid4(), None),  # Bunny needs its library
        ("nhprep-mp4", uuid.uuid4(), 42),  # only Bunny has one
        ("nhprep-mp4", None, None),  # a provider needs a video id
        (None, uuid.uuid4(), None),  # and a video id needs a provider
        (None, None, 42),
        ("youtube", uuid.uuid4(), None),
        ("bunny", uuid.uuid4(), 0),
    ],
)
async def test_video_references_are_all_or_nothing(conn, provider, video, library):
    params = {"id": uuid.uuid4(), "course": COURSE, "hash": HASH, "provider": provider, "video": video, "lib": library}
    assert await _refused(conn, LESSON, params)


async def test_only_chapters_have_a_start_and_header_chunks_have_no_item(conn):
    lesson = await _lesson(conn)
    item = (
        "INSERT INTO nhprep_lesson_items (lesson_id, kind, item_key, position, start_seconds, content_hash) "
        "VALUES (:l, :kind, :key, 0, :start, :h)"
    )
    assert await _refused(conn, item, {"l": lesson, "kind": "note", "key": "n1", "start": 5, "h": HASH})
    assert await _refused(conn, item, {"l": lesson, "kind": "chapter", "key": "c1", "start": 86_401, "h": HASH})
    assert await _refused(conn, item, {"l": lesson, "kind": "chapter", "key": "bad key", "start": 5, "h": HASH})
    assert not await _refused(conn, item, {"l": lesson, "kind": "chapter", "key": "c1", "start": 5, "h": HASH})


async def test_no_nhprep_table_holds_user_data(database):
    """Account deletion (tests/test_account_deletion.py) discovers every table that references users.id; NHprep's
    catalogue is shared reference data and must stay out of that set (no per-user rows to delete)."""
    engine = create_async_engine(database)
    async with engine.connect() as connection:
        referencing = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE c.contype = 'f' AND c.confrelid = 'users'::regclass AND t.relname LIKE 'nhprep%'"
            )
        )
        tables = await connection.scalar(text("SELECT count(*) FROM pg_tables WHERE tablename LIKE 'nhprep%'"))
    await engine.dispose()
    assert referencing == 0 and tables == 7
