"""The app facts (PLATFORM_GUIDE, app/chat/platform.py) reach the model on every chat path, right after the scope
policy, only with the switch on, and never carry the account's name or address."""

from __future__ import annotations

import httpx

from app.auth.emails import MemoryEmailSender
from app.main import create_app
from tests.conftest import make_settings, register, stream_chat

FACTS = "About the NetSec AI app"


async def _system_prompts(fake_llm, **flags) -> tuple[list[str], str]:
    application = create_app(make_settings(**flags), llm=fake_llm, email_sender=MemoryEmailSender())
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://t") as client:
            account = await register(client, application)
            _, events = await stream_chat(client, account["headers"], "Where do I change my reply language?")
            first = fake_llm.calls[-1][0].content
            conversation_id = events[0][1]["conversationId"]
            regenerate = await client.post(
                f"/v1/conversations/{conversation_id}/regenerate/stream", headers=account["headers"]
            )
            assert regenerate.status_code == 200
            return [first, fake_llm.calls[-1][0].content], account["email"]


async def test_with_the_switch_on_every_chat_path_carries_the_app_facts_and_no_identity(fake_llm):
    prompts, email = await _system_prompts(fake_llm, platform_guide=True)
    for prompt in prompts:
        assert FACTS in prompt
        assert email not in prompt and email.split("@")[0] not in prompt


async def test_with_the_switch_off_the_app_facts_never_reach_the_model(fake_llm):
    prompts, _ = await _system_prompts(fake_llm)
    assert all(FACTS not in prompt for prompt in prompts)
