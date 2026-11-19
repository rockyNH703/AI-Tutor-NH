"""The headed learning suite's chat fixtures (tests/fixtures/learning/*.json), checked the way the server reads
them. The e2e stack sends each message to /api/v1/chat/stream; the model sees it after secret masking, and the
fake LLM (app/llm/fake.py) echoes it as "You said: <message>", piece by piece; the saved reply is built into
practice records (app/assessments/state.py). A fixture that masking would change, that the API would refuse as
too long, that streams too slowly, or whose practice block no longer builds (or no longer refuses with its own
code) fails here, before a headed run does."""

import json
from pathlib import Path

import pytest

from app.assessments import state as assessments
from app.llm.base import ChatMessage, GenerationOptions
from app.llm.fake import FakeProvider, default_reply
from app.llm.redact import mask_secrets

FIXTURES = Path(__file__).parent / "fixtures" / "learning"
MESSAGE_MAX = 16_000  # app/chat/schemas.py: ChatRequest.message
PIECES_MAX = 1_500  # streamed at LLM_FAKE_TOKEN_DELAY_S=0.01 (playwright.learning.config.ts): at most ~15 s a turn
KINDS = ("single", "multi", "fill", "numeric", "short", "long", "match", "order", "cli", "lines", "scenario", "diagram")
NAMES = {
    *(f"kind-{kind}" for kind in KINDS),
    *("v1-quiz", "v1-typed", "v1-mcq", "v1-fill-blank", "quiz-30", "quiz-31", "exam", "long-options", "katex-error"),
    *("bad-json", "bad-duplicate", "bad-dangling", "bad-empty", "bad-oversized", "bad-unclosed", "bad-renderers"),
    *("flashcards", "cheatsheet", "cheatsheet-exam", "video", "video-invalid", "video-live", "topology", "mixed"),
}
# The record each kind-* fixture's first question becomes (the author's type -> the stored kind).
STORED_KIND = {"single": "mcq", "fill": "fill_blank", "scenario": "mcq"}
SCHEMA_V1 = {"v1-quiz", "v1-typed", "v1-mcq", "v1-fill-blank", "quiz-30", "mixed"}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_the_fixture_set_is_exactly_the_expected_one():
    assert len(NAMES) == 36
    assert {path.stem for path in FIXTURES.glob("*.json")} == NAMES


@pytest.mark.parametrize("name", sorted(NAMES))
async def test_a_fixture_reaches_the_model_unchanged_and_streams_within_limits(name):
    fixture = load(name)
    message = fixture["message"]
    assert set(fixture) == {"message", "refusal"}
    assert mask_secrets(message) == (message, 0), "masking would change what the fake LLM echoes"
    assert 0 < len(message) <= MESSAGE_MAX
    stream = await FakeProvider().stream([ChatMessage("user", message)], GenerationOptions())
    pieces = [part.text async for part in stream.parts if part.text]
    assert "".join(pieces) == default_reply([ChatMessage("user", message)])
    assert len(pieces) <= PIECES_MAX


@pytest.mark.parametrize("name", sorted(NAMES))
def test_a_practice_fixture_builds_its_record_or_refuses_with_its_code(name):
    fixture = load(name)
    message = fixture["message"]
    reply = default_reply([ChatMessage("user", message)])
    records, refusal = assessments.build(reply, message)
    assert refusal == fixture["refusal"]
    if fixture["refusal"] is not None or not assessments.has_test(reply):
        assert records is None  # refused whole, or a renderer that is never recorded (flashcards, video, ...)
        return
    assert records is not None and len(records) == 1
    record = records[0]
    assert record["schemaVersion"] == (1 if name in SCHEMA_V1 else 2)
    if name.startswith("kind-"):
        kind = name.removeprefix("kind-")
        assert record["mode"] == "practice"
        assert record["questions"][0]["kind"] == STORED_KIND.get(kind, kind)
        assert (record["scenario"] is not None) == (kind == "scenario")
    if name in {"exam", "cheatsheet-exam"}:
        assert record["mode"] == "exam"
    if name == "quiz-30":
        assert record["total"] == assessments.MAX_QUESTIONS
