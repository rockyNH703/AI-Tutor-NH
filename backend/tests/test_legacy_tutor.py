"""The assistant as it behaves before any per-user settings exist, frozen.

Per-user settings add instructions to the system prompt. A user who has set nothing and not
practised yet must get exactly what every user got before settings existed: the same system
prompt, byte for byte, and the same messages sent to the model. These fixtures were recorded from
the code before any settings existed; if one of these tests fails, the default assistant changed.
2026-09-30: the owner asked for study formatting (cards, chemistry, diagrams, charts, citations), so
the prompt fixtures were re-recorded with that prompt (app/chat/assistant.py). 2026-10-22: re-recorded
for one line added on purpose. 2026-10-29: re-recorded for NetSec AI's engineering prompt (the owner's
text, app/chat/assistant.py SYSTEM_PROMPT); the messages around it are unchanged.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from app.chat.assistant import SYSTEM_PROMPT, AssistantConfig, resolve_assistant_config
from app.progress import activity
from app.rag.graph import RetrievalOptions
from tests.conftest import register, stream_chat

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY_PROMPT = (FIXTURES / "legacy_system_prompt.txt").read_bytes().decode("utf-8")
LEGACY_MESSAGES = json.loads((FIXTURES / "legacy_llm_messages.json").read_bytes().decode("utf-8"))


def test_the_system_prompt_is_the_frozen_legacy_prompt():
    assert SYSTEM_PROMPT.encode("utf-8") == LEGACY_PROMPT.encode("utf-8")


def test_the_default_assistant_configuration_is_unchanged():
    assert resolve_assistant_config(None, None) == AssistantConfig(
        system_prompt=LEGACY_PROMPT, retrieval=RetrievalOptions(), model=None, temperature=None
    )


async def test_a_conversation_sends_the_model_exactly_the_legacy_messages(client, app, fake_llm, monkeypatch):
    # 11:30 India time: the practice day has 12h 30m left (the record's countdown is then fixed).
    monkeypatch.setattr(activity, "utcnow", lambda: datetime(2026, 9, 26, 6, 0, tzinfo=UTC))
    account = await register(client, app)
    _, first = await stream_chat(client, account["headers"], "What is photosynthesis?")
    conversation_id = first[0][1]["conversationId"]
    await stream_chat(client, account["headers"], "Explain the second step.", conversation_id)

    sent = [[{"role": m.role, "content": m.content} for m in call] for call in fake_llm.calls]
    assert sent[0] == LEGACY_MESSAGES[0]
    # The second turn: the same messages. Its prompt is the legacy prompt, then whatever the user's
    # practice so far adds as its own "## " section (exact text: tests/test_tutor_instructions.py).
    assert sent[1][1:] == LEGACY_MESSAGES[1][1:]
    second = sent[1][0]["content"]
    assert second == LEGACY_PROMPT or second.startswith(f"{LEGACY_PROMPT}\n\n## ")
