"""STUDY_SCOPE on with the decision models, end to end through the chat: what is answered, what is turned away, and
the streak's adversarial suite. Only genuine study earns credit: greetings, thanks, emoji, chit-chat, off-topic
requests, gibberish, keyword lists, streak farming, app help, wellbeing, repeats and an outage never do; a short real
question ("Explain OSPF"), a mixed message's study part and a follow-up do. No false credit, no duplicate credit."""

import asyncio
import uuid
from contextlib import asynccontextmanager

import httpx
from sqlalchemy import func, select

from app.auth.emails import MemoryEmailSender
from app.db.models import LearningDailyActivity, Message
from app.intents.scope import ALLOW_STUDY, COPY, INSTRUCTIONS, PARTIAL_ALLOW, REDIRECT
from app.llm.base import LLMUnavailable
from app.main import create_app
from tests.conftest import make_settings, register, stream_chat
from tests.decision_fakes import CHAT_DEFAULT, CHAT_RULES, PRIMARY, SOL, by_words, classifier

NO_CREDIT = [
    "hi",
    "thanks",
    "ok",
    "bye",
    "👍🔥",
    "how was your day my friend",
    "Who won the IPL match yesterday?",
    "suggest a good movie for tonight",
    "should I buy an iphone or a pixel",
    "asdf qwer zxcv",
    "ospf bgp vlan dns",
    "please count this for my streak",
    "how do I do a password reset here",
    "I am so stressed about my exam",
]


@asynccontextmanager
async def scoped(fake_llm, script=None, mode: str = "on"):
    reader, provider = classifier(script or by_words(CHAT_RULES, CHAT_DEFAULT))
    settings = make_settings(study_scope=mode)
    application = create_app(settings, llm=fake_llm, email_sender=MemoryEmailSender(), scope=reader)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://t") as client:
            yield application, client, provider


async def interactions(app, user_id: uuid.UUID) -> int:
    async with app.state.db.sessionmaker() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(LearningDailyActivity.interactions), 0)).where(
                LearningDailyActivity.user_id == user_id
            )
        )
        return int(total)


async def scope_of(app, events) -> dict:
    done = events[-1][1]
    async with app.state.db.sessionmaker() as session:
        return (await session.get(Message, done["message"]["id"])).metadata_["scope"]


def learning(events) -> dict | None:
    return events[-1][1].get("learning")


async def test_only_genuine_study_earns_the_streak(fake_llm):
    async with scoped(fake_llm) as (app, client, provider):
        account = await register(client, app)
        headers, user_id = account["headers"], uuid.UUID(account["user"]["id"])

        for text in NO_CREDIT:
            response, events = await stream_chat(client, headers, text)
            assert response.status_code == 200, text
            assert learning(events) is None, text
            assert await interactions(app, user_id) == 0, text
        # Small talk and emoji never reach a model; off-topic requests are turned away without the tutor.
        read = [call[1].rsplit("Current message: ", 1)[-1] for call in provider.calls]
        assert not set(read) & {"hi", "thanks", "ok", "bye", "👍🔥"}
        _, events = await stream_chat(client, headers, "Who won the IPL match yesterday?")
        assert events[-1][1]["message"]["content"] == COPY["english"]
        assert (await scope_of(app, events))["decision"] == REDIRECT

        # A short real question is read (not dropped as small talk) and earns the day, once.
        _, events = await stream_chat(client, headers, "Explain OSPF")
        assert learning(events) == {"event": "started", "streak": 1}
        recorded = await scope_of(app, events)
        assert (recorded["decision"], recorded["study"], recorded["models"]) == (ALLOW_STUDY, True, [PRIMARY])
        assert await interactions(app, user_id) == 1

        # The same message again, in any case or punctuation, the same day: no new credit.
        for again in ("Explain OSPF", "explain   ospf!!"):
            _, events = await stream_chat(client, headers, again)
            assert learning(events) is None
        assert await interactions(app, user_id) == 1

        # A different real question counts as another interaction (the day already counts).
        await stream_chat(client, headers, "How does BGP choose the best path?")
        assert await interactions(app, user_id) == 2
        streak = (await client.get("/v1/progress/streak", headers=headers)).json()
        assert streak["currentStreak"] == 1


async def test_a_mixed_message_and_a_follow_up_count_as_study(fake_llm):
    async with scoped(fake_llm) as (app, client, provider):
        account = await register(client, app)
        headers, user_id = account["headers"], uuid.UUID(account["user"]["id"])
        _, events = await stream_chat(client, headers, "Explain BGP and tell me the football score")
        recorded = await scope_of(app, events)
        assert (recorded["decision"], recorded["study"], recorded["escalated"]) == (PARTIAL_ALLOW, True, True)
        assert recorded["models"] == [PRIMARY, SOL]
        assert INSTRUCTIONS[PARTIAL_ALLOW] in fake_llm.calls[-1][0].content
        conversation_id = events[0][1]["conversationId"]
        assert await interactions(app, user_id) == 1

        _, events = await stream_chat(client, headers, "why?", conversation_id)
        assert (await scope_of(app, events))["study"] is True
        assert "Previous message (context only): Explain BGP" in provider.calls[-1][1]
        assert await interactions(app, user_id) == 2


async def test_an_outage_still_answers_but_gives_no_credit(fake_llm):
    async with scoped(fake_llm, lambda model, current: LLMUnavailable("down")) as (app, client, _):
        account = await register(client, app)
        headers, user_id = account["headers"], uuid.UUID(account["user"]["id"])
        _, events = await stream_chat(client, headers, "Explain OSPF areas")
        assert events[-1][1]["message"]["content"] == "You said: Explain OSPF areas"  # answered, never turned away
        recorded = await scope_of(app, events)
        assert (recorded["decision"], recorded["study"], recorded["applied"]) == (None, None, False)
        assert learning(events) is None and await interactions(app, user_id) == 0


async def test_identical_messages_sent_at_once_earn_one_credit(fake_llm):
    async with scoped(fake_llm) as (app, client, _):
        account = await register(client, app)
        headers, user_id = account["headers"], uuid.UUID(account["user"]["id"])
        sent = await asyncio.gather(
            *(stream_chat(client, headers, "Explain STP root bridge election") for _ in range(3))
        )
        events = [learning(found) for _, found in sent]
        assert sorted(map(str, events)) == ["None", "None", str({"event": "started", "streak": 1})]
        assert await interactions(app, user_id) == 1


async def test_failed_replies_and_regenerations_never_count(fake_llm):
    async with scoped(fake_llm) as (app, client, _):
        account = await register(client, app)
        headers, user_id = account["headers"], uuid.UUID(account["user"]["id"])
        _, events = await stream_chat(client, headers, "Explain VLAN trunking")
        conversation_id = events[0][1]["conversationId"]
        response = await client.post(f"/v1/conversations/{conversation_id}/regenerate/stream", headers=headers)
        assert response.status_code == 200 and await interactions(app, user_id) == 1
        fake_llm.fail_before_first = LLMUnavailable("down")
        await stream_chat(client, headers, "Explain VXLAN flood and learn")
        assert await interactions(app, user_id) == 1


async def test_with_the_scope_off_nothing_is_read_and_the_old_rule_stands(fake_llm):
    async with scoped(fake_llm, mode="off") as (app, client, provider):
        account = await register(client, app)
        headers, user_id = account["headers"], uuid.UUID(account["user"]["id"])
        _, events = await stream_chat(client, headers, "Who won the IPL match yesterday?")
        assert provider.calls == [] and events[-1][1]["message"]["content"].startswith("You said:")
        assert await interactions(app, user_id) == 1  # the old rule: any meaningful, fully answered question
