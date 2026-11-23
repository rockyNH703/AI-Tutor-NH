"""A scripted OpenRouter for the study scope's decision models (app/intents/decision.py): each model answers by the
current message's words, so the tests never reach a network."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

from app.intents.decision import DecisionClassifier
from app.llm.base import ChatMessage, GenerationOptions, LLMStream, ProviderInfo, StreamPart, Usage

PRIMARY = "deepseek/deepseek-v4.1-flash-1017"
SOL = "openai/gpt-6.2-sol"


def verdict(
    scope: str = "study", study: bool = True, intent: str = "understand", follow_up: bool = False, confidence=0.95
) -> str:
    return json.dumps(
        {"scope": scope, "study": study, "intent": intent, "follow_up": follow_up, "confidence": confidence}
    )


Script = Callable[[str, str], "str | BaseException"]


class ScriptedDecisions:
    """`script(model, current message)` gives the raw answer, or an exception to raise. Every request is kept."""

    name = "scripted-decision"

    def __init__(self, script: Script) -> None:
        self.script = script
        self.calls: list[tuple[str | None, str, GenerationOptions]] = []

    async def stream(self, messages: list[ChatMessage], options: GenerationOptions) -> LLMStream:
        text = messages[-1].content
        self.calls.append((options.model, text, options))
        answer = self.script(options.model or "", text.rsplit("Current message: ", 1)[-1])
        if isinstance(answer, BaseException):
            raise answer

        async def parts() -> AsyncIterator[StreamPart]:
            yield StreamPart(text=answer)
            yield StreamPart(usage=Usage(input_tokens=600, output_tokens=40), finish_reason="stop")

        return LLMStream(info=ProviderInfo(self.name, options.model or ""), parts=parts())

    def models(self) -> list[str | None]:
        return [model for model, _, _ in self.calls]


def by_words(rules: dict[str, str], default: str, escalation: dict[str, str] | None = None) -> Script:
    """The first rule whose key is in the current message (lower case) answers; the escalation model uses its own
    rules when given (else the same), and `default` when none matches."""

    def script(model: str, current: str) -> str:
        table = escalation if escalation is not None and model == SOL else rules
        low = current.lower()
        return next((answer for key, answer in table.items() if key in low), default)

    return script


def classifier(script: Script, **overrides) -> tuple[DecisionClassifier, ScriptedDecisions]:
    provider = ScriptedDecisions(script)
    values = {
        "primary_model": PRIMARY,
        "escalation_model": SOL,
        "primary_timeout_s": 2.0,
        "escalation_timeout_s": 2.0,
        "escalate_below": 0.85,
        "min_redirect": 0.5,
        "min_confidence": 0.5,
    } | overrides
    return DecisionClassifier(provider, **values), provider


# One script for the chat tests: what the fake decision models read in the messages those tests send.
CHAT_RULES = {
    "count this": verdict("unrelated", False, "other", confidence=0.9),
    "ipl": verdict("unrelated", False, "other", confidence=0.97),
    "movie": verdict("unrelated", False, "other", confidence=0.96),
    "buy": verdict("unrelated", False, "other", confidence=0.95),
    "football": verdict("mixed", True, "understand", confidence=0.9),
    "password reset": verdict("support", False, "other", confidence=0.95),
    "stressed": verdict("support", False, "other", confidence=0.95),
    "how was your day": verdict("chat", False, "other", confidence=0.95),
    "asdf": verdict("unclear", False, "other", confidence=0.9),
    "ospf bgp vlan": verdict("unclear", False, "other", confidence=0.9),
}
CHAT_DEFAULT = verdict("study", True, "understand", confidence=0.95)
