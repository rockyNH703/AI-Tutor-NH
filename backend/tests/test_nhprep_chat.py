"""NHprep's chat turn (plan rev 4 §8.4): inside ChatService._app_turn, after the question bank's parse. A typed
message from an account NHprep is for, that the cue parse reads as an NHprep request and the finder matches in time,
is answered by the app: one fixed line (no model, no search) and the card's ids in metadata.nhprep (pinned by
fixtures/nhprep/cards.json); anything else is the normal turn. Regenerating a card runs the finder again. Readers
NHprep isn't for never see the card. The parser and finder are fakes here (lane L3 owns the real ones)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.assessments import state as assessments
from app.db.models import Message
from app.nhprep.chat import COPY, NhprepChat, build_chat, card
from app.nhprep.types import FinderResult, Match, NhprepQuery
from app.qbank import chat_tests
from tests.conftest import make_settings, parse_sse, register, stream_chat
from tests.test_nhprep_api import CARDS, nhprep_app, seed, student

LESSON, ITEM = uuid.uuid4(), uuid.uuid4()
OTHERS = [uuid.uuid4() for _ in range(7)]
ASK = "show me the static routes lesson notes"
QUERY = NhprepQuery(text="static routes", views=("notes", "video", "notes"))
CONFIDENT = FinderResult("confident", Match(LESSON, 0.9, ITEM), (Match(OTHERS[0], 0.5),), {"legs": {"dense": 3}})


class Parser:
    def __init__(self, query: NhprepQuery | None = QUERY, fail: Exception | None = None):
        self.query, self.fail, self.calls = query, fail, []

    def __call__(self, message, earlier):
        self.calls.append((message, list(earlier)))
        if self.fail:
            raise self.fail
        return self.query if "lesson" in message else None


class Finder:
    def __init__(self, result: FinderResult = CONFIDENT, delay: float = 0, fail: Exception | None = None):
        self.result, self.delay, self.fail, self.calls = result, delay, fail, []

    async def __call__(self, query):
        self.calls.append(query)
        await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        return self.result


def install(app, parser=None, finder=None) -> tuple[Parser, Finder]:
    parser, finder = parser or Parser(), finder or Finder()
    app.state.chat.nhprep = NhprepChat(app.state.settings, parser, finder)
    return parser, finder


async def stored(app, message_id: str) -> Message:
    async with app.state.db.sessionmaker() as db:
        return await db.scalar(select(Message).where(Message.id == uuid.UUID(message_id)))


# ------------------------------------------------------------------ the card turn
async def test_a_confident_match_is_a_fixed_line_and_the_cards_ids_with_no_model_call():
    async with nhprep_app() as (app, client):
        _, finder = install(app)
        response, events = await stream_chat(client, await student(client, app), ASK)
        assert response.status_code == 200, response.text
        assert [name for name, _ in events] == ["meta", "token", "done"]
        assert events[1][1]["text"] == COPY["confident"]
        assert app.state.llm.calls == []  # no model; question_bank_tests is off here: NHprep doesn't need the bank
        message = events[-1][1]["message"]
        assert message["content"] == COPY["confident"] and message["status"] == "complete"
        assert message["metadata"]["nhprep"] == {
            "version": 1,
            "views": ["notes", "video"],
            "status": "confident",
            "primaryLessonId": str(LESSON),
            "alternativeLessonIds": [str(OTHERS[0])],
            "matchedItemId": str(ITEM),
        }
        assert "nhprepTrace" not in message["metadata"] and finder.calls == [QUERY]
        trace = (await stored(app, message["id"])).metadata_["nhprepTrace"]
        assert trace["status"] == "confident" and trace["legs"] == {"dense": 3} and isinstance(trace["findMs"], int)


async def test_an_ambiguous_match_draws_the_closest_with_up_to_five_others():
    alternatives = tuple(Match(lesson, 0.4) for lesson in [LESSON, *OTHERS, OTHERS[0]])  # self and a repeat
    async with nhprep_app() as (app, client):
        install(app, finder=Finder(FinderResult("ambiguous", Match(LESSON, 0.5), alternatives)))
        _, events = await stream_chat(client, await student(client, app), ASK)
        message = events[-1][1]["message"]
        assert message["content"] == COPY["ambiguous"]
        assert message["metadata"]["nhprep"]["alternativeLessonIds"] == [str(lesson) for lesson in OTHERS[:5]]
        assert message["metadata"]["nhprep"]["matchedItemId"] is None


@pytest.mark.parametrize(
    ("parser", "finder"),
    [
        (Parser(query=None), Finder()),  # no NHprep cue
        (Parser(fail=ValueError("parse bug")), Finder()),
        (Parser(), Finder(FinderResult("none"))),
        (Parser(), Finder(FinderResult("timeout"))),
        (Parser(), Finder(FinderResult("confident"))),  # no primary: a finder bug, not a card
        (Parser(), Finder(delay=5)),  # past NHPREP_FIND_TIMEOUT_S
        (Parser(), Finder(fail=RuntimeError("embeddings down"))),
    ],
    ids=["no_cue", "parse_error", "none", "timeout_status", "no_primary", "deadline", "finder_error"],
)
async def test_anything_but_a_match_in_time_is_the_normal_turn(parser, finder):
    async with nhprep_app(nhprep_find_timeout_s=0.2) as (app, client):
        install(app, parser, finder)
        started = asyncio.get_running_loop().time()
        _, events = await stream_chat(client, await student(client, app), ASK)
        assert asyncio.get_running_loop().time() - started < 3  # the deadline held
        message = events[-1][1]["message"]
        assert len(app.state.llm.calls) == 1 and "nhprep" not in message["metadata"]


@pytest.mark.parametrize("who", ["outsider", "mode_off", "qa_unlisted"])
async def test_nhprep_is_never_asked_for_an_account_it_is_not_for(who):
    async with nhprep_app(mode="qa" if who == "qa_unlisted" else "on", nhprep_qa_emails="qa@networkershome.com") as (
        app,
        client,
    ):
        parser, finder = install(app)
        headers = (await register(client, app))["headers"] if who == "outsider" else await student(client, app)
        if who == "mode_off":
            app.state.settings.nhprep_mode = "off"
        _, events = await stream_chat(client, headers, ASK)
        assert parser.calls == [] and finder.calls == [] and len(app.state.llm.calls) == 1
        assert "nhprep" not in events[-1][1]["message"]["metadata"]


@pytest.mark.parametrize(
    "turn",
    [
        {"voice": True},
        {"attached_kinds": ("pdf",)},
        {"command": object()},
    ],
    ids=["voice", "attachments", "memory_command"],
)
async def test_only_a_typed_message_without_files_can_be_an_nhprep_turn(turn):
    async with nhprep_app() as (app, client):
        parser, _ = install(app)
        user = SimpleNamespace(id=uuid.uuid4(), email="s@networkershome.com", email_verified=True)
        prepared = SimpleNamespace(
            **{"voice": False, "attached_kinds": (), "command": None, "user_message": object(), "nhprep": None, **turn},
            user=user,
            assistant_id=uuid.uuid4(),
        )
        assert await app.state.chat._app_turn(prepared, [], ASK) is None
        assert parser.calls == []


async def test_the_bank_parse_comes_first():
    async with nhprep_app(question_bank_tests=True, question_bank_turn="on") as (app, client):
        parser, _ = install(app)
        _, events = await stream_chat(client, await student(client, app), "quiz me on the static routes lesson")
        assert parser.calls == [] and "nhprep" not in events[-1][1]["message"]["metadata"]


async def test_the_parser_sees_the_earlier_messages_newest_first():
    async with nhprep_app() as (app, client):
        parser, _ = install(app)
        headers = await student(client, app)
        _, events = await stream_chat(client, headers, "what is a floating static route?")
        conversation = events[-1][1]["conversation"]["id"]
        await stream_chat(client, headers, "and administrative distance?", conversation)
        await stream_chat(client, headers, "the lesson on this please", conversation)
        assert parser.calls[-1] == (
            "the lesson on this please",
            ["and administrative distance?", "what is a floating static route?"],
        )


# ------------------------------------------------------------------ regenerate
async def test_regenerating_a_card_runs_the_finder_again_and_a_model_reply_stays_the_models():
    async with nhprep_app() as (app, client):
        parser, finder = install(app)
        headers = await student(client, app)
        _, events = await stream_chat(client, headers, ASK)
        conversation = events[-1][1]["conversation"]["id"]
        finder.result = FinderResult("confident", Match(OTHERS[1], 0.9))
        response = await client.post(f"/v1/conversations/{conversation}/regenerate/stream", headers=headers)
        done = parse_sse(response.text)[-1][1]["message"]
        assert len(finder.calls) == 2 and done["metadata"]["nhprep"]["primaryLessonId"] == str(OTHERS[1])
        assert app.state.llm.calls == []

        _, events = await stream_chat(client, headers, "explain floating statics", conversation)  # the model's
        calls = len(parser.calls)
        response = await client.post(f"/v1/conversations/{conversation}/regenerate/stream", headers=headers)
        assert len(parser.calls) == calls and "nhprep" not in parse_sse(response.text)[-1][1]["message"]["metadata"]


# ------------------------------------------------------------------ who sees the card
async def test_the_card_is_shown_only_to_a_reader_nhprep_is_for():
    async with nhprep_app() as (app, client):
        install(app)
        headers = await student(client, app)
        _, events = await stream_chat(client, headers, ASK)
        conversation = events[-1][1]["conversation"]["id"]
        url = f"/v1/conversations/{conversation}/messages"

        shown = (await client.get(url, headers=headers)).json()["items"][-1]
        assert shown["metadata"]["nhprep"]["primaryLessonId"] == str(LESSON) and "nhprepTrace" not in shown["metadata"]
        app.state.settings.nhprep_mode = "off"  # the kill switch: the line stays, the card goes
        off = (await client.get(url, headers=headers)).json()["items"][-1]
        assert off["content"] == COPY["confident"] and "nhprep" not in off["metadata"]
        app.state.settings.nhprep_mode = "on"
        app.state.settings.nhprep_allowed_email_domains = "example.org"  # no longer a NetworkersHome student
        hidden = (await client.get(url, headers=headers)).json()["items"][-1]
        assert hidden["content"] == "This reply isn't available." and hidden["metadata"] == {}


# ------------------------------------------------------------------ the fixed copy, the pinned shape, the logs
@pytest.mark.parametrize("copy", list(COPY.values()))
async def test_the_fixed_copy_is_plain_text_that_never_becomes_a_practice_test(copy):
    for mark in ("`", "~", "[", "]", "$", "::", '"chapter', "question-bank", "chapter", "student"):
        assert mark not in copy.lower(), mark
    assert not assessments.has_test(copy) and assessments.records_for({}, copy, "complete") == []
    assert await chat_tests.fill(None, copy, "seed", uuid.uuid4(), uuid.uuid4()) == (copy, None)


def test_the_pinned_cards_are_what_card_public_writes():
    for pinned in CARDS["cards"]:
        primary = Match(
            uuid.UUID(pinned["primaryLessonId"]), 0.9, uuid.UUID(m) if (m := pinned["matchedItemId"]) else None
        )
        result = FinderResult(
            pinned["status"], primary, tuple(Match(uuid.UUID(i), 0.5) for i in pinned["alternativeLessonIds"])
        )
        built = card(NhprepQuery(text="t", views=tuple(pinned["views"])), result, 12)
        assert built.public == pinned and built.copy == COPY[pinned["status"]]


async def test_no_nhprep_log_line_holds_the_students_words(caplog):
    async with nhprep_app() as (app, client):
        logging.getLogger().addHandler(caplog.handler)  # create_app's configure_logging replaced the root handlers
        caplog.set_level(logging.INFO)
        install(app)
        await stream_chat(client, await student(client, app), ASK)
    records = [record for record in caplog.records if record.getMessage().startswith("nhprep.")]
    assert {record.getMessage() for record in records} >= {"nhprep.intent", "nhprep.finder"}
    blob = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in records)
    assert "static routes" not in blob and ASK not in blob


def test_build_chat_is_nothing_while_nhprep_is_off():
    assert build_chat(None, make_settings(), None) is None  # type: ignore[arg-type]


async def test_build_chat_wires_lane_l3s_parser_and_finder():
    """Only the wiring: the real finder, on the sample catalog, answers a FinderResult (its ranking is L3's)."""
    from app.nhprep import intent

    async with nhprep_app() as (app, client):
        await seed(app)
        chat = build_chat(app.state.db, make_settings(nhprep_mode="on"), app.state.embeddings)
        assert chat is not None and chat.parse is intent.parse
        result = await chat.find(NhprepQuery(text="static routes", views=("video",)))
        assert isinstance(result, FinderResult)
