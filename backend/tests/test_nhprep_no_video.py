"""A lesson's video reference never leaves the server (owner, 2026-11-20; Revision 5 in netsec-handoffs/nhprep.md).

nhprep_lessons keeps video_provider and video_id for the server's signer. Nothing NetSec sends carries them, an embed or
a video link: no /v1/nhprep response model, no pinned wire shape (fixtures/nhprep/cards.json, which the web app's
stubs read), and no card or trace a chat reply stores. The one exception is POST .../playback's `src`
(NhprepPlaybackOut), the signed link the player loads. The `video` status (ready / no_video / not_configured) is
allowed: it names no video.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.nhprep import intent
from app.nhprep.chat import build_chat, card
from app.nhprep.types import FinderResult, Match
from tests.conftest import make_settings
from tests.test_nhprep_api import CARDS, nhprep_app, seed

FORBIDDEN_KEY = re.compile(r"video_?(id|provider|url|library)|embed_?(url|src)|^src$|^url$", re.IGNORECASE)
LINK = re.compile(r"videos\.nhprep|mediadelivery|\.mp4\b|[?&](sig|exp|token)=", re.IGNORECASE)
PLAYBACK_SCHEMA = "NhprepPlaybackOut"  # the only model allowed to carry `src`


def offenders(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            if FORBIDDEN_KEY.search(str(key)):
                found.append(f"{path}.{key}")
            found += offenders(inner, f"{path}.{key}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            found += offenders(inner, f"{path}[{index}]")
    elif isinstance(value, str) and LINK.search(value):
        found.append(f"{path}={value}")
    return found


def test_the_offender_check_catches_each_kind_of_video_reference():
    sample = {"video_id": 1, "videoProvider": 2, "embedUrl": 3, "src": 4, "y": "https://videos.nhprep.com/a.mp4"}
    assert len(offenders(sample)) == 5
    allowed = {"title": "QoS for voice and video traffic", "video": "no_video", "views": ["video", "chapters"]}
    assert offenders(allowed) == []


def _refs(value: Any) -> set[str]:
    if isinstance(value, dict):
        own = {value["$ref"].rsplit("/", 1)[-1]} if isinstance(value.get("$ref"), str) else set()
        return own.union(*(_refs(inner) for inner in value.values()))
    if isinstance(value, list):
        return set().union(*(_refs(inner) for inner in value))
    return set()


async def test_no_nhprep_response_schema_but_playback_carries_a_video_reference():
    async with nhprep_app() as (app, _):
        spec = app.openapi()
    paths = {path: ops for path, ops in spec["paths"].items() if path.startswith("/v1/nhprep")}
    schemas = spec["components"]["schemas"]
    wanted, seen = _refs(paths), set()
    while wanted - seen:  # every schema the routes reach, transitively
        name = (wanted - seen).pop()
        seen.add(name)
        wanted |= _refs(schemas[name])
    assert PLAYBACK_SCHEMA in seen and any(name.startswith("NhprepLesson") for name in seen)
    assert offenders({name: schemas[name] for name in sorted(seen - {PLAYBACK_SCHEMA})}) == []


def test_the_pinned_wire_shapes_carry_no_video_reference_but_the_playback_src():
    assert offenders({key: value for key, value in CARDS.items() if key != "playback"}) == []
    assert set(CARDS["playback"]["ready"]) == {"status", "src", "expiresIn"}


async def test_a_real_cards_public_metadata_and_trace_carry_no_video_reference():
    """The real cue parse and finder on the sample catalog: neither metadata.nhprep nor nhprepTrace names the video."""
    async with nhprep_app() as (app, _):
        lessons = await seed(app)
        chat = build_chat(app.state.db, make_settings(nhprep_mode="on"), app.state.embeddings)
        assert chat is not None
        query = intent.parse("is there a video on static routes and floating statics?")
        assert query is not None
        result = await chat.find(query)
    primary = result.primary or Match(next(iter(lessons.values())), 0.5)
    built = card(query, FinderResult("confident", primary, result.alternatives, result.trace), 7)
    assert built.public["views"] and offenders(built.public) == []
    assert offenders(built.trace) == [], built.trace
    assert uuid.UUID(built.public["primaryLessonId"])
