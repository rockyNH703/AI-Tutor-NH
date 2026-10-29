from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from app.db.models import MEMORY_KINDS
from app.errors import ValidationFailed
from app.llm.base import ChatMessage, GenerationOptions, LLMProvider
from app.memory.foundation import MEMORY_PROMPT_VERSION, MEMORY_SCHEMA_VERSION
from app.memory.policy import normalize_content, validate_memory_policy
from app.settings import Settings

MAX_PROPOSALS = 3

_SYSTEM_PROMPT = f"""You extract optional personal memories from one user message to NetSec AI, an assistant for
network, security and infrastructure engineers.
Use only the supplied user text. Do not infer from an assistant, retrieved context, files, or prior memory.
Keep only lasting facts the user explicitly stated about themselves that will help future answers: the
environment and platforms they work with (vendors, operating systems, cloud providers, tools), their projects
and responsibilities, their certification goals, and how they like answers (depth, format, vendor syntax).
Use "learning_context" for certification goals and how they like answers, "study_context" for their
environment, platforms and projects. Write each as one short sentence about the user, in general terms.
Return exactly one JSON object and no markdown:
{{"proposals":[{{"operation":"add","kind":"learning_context|study_context","content":"...","confidence":0.0,"evidenceReason":"..."}}]}}
Return at most {MAX_PROPOSALS} proposals. Return an empty proposals array when nothing durable and useful was
explicitly stated.
Never extract credentials or secrets (passwords, keys, tokens, pre-shared keys, SNMP communities,
certificates), configuration or log excerpts, IP addresses, hostnames, serial numbers or other identifiers of a
system, network or organisation, one-off incident details, their role or experience level (their profile holds
those), mastery, scores, or streaks.
Schema version: {MEMORY_SCHEMA_VERSION}. Prompt version: {MEMORY_PROMPT_VERSION}.
"""


class ExtractionOutputError(ValueError):
    """The model returned output that does not exactly match the extraction contract."""


class ExtractionOutputTooLarge(ExtractionOutputError):
    pass


@dataclass(frozen=True)
class ExtractedProposal:
    operation: str
    kind: str
    content: str
    confidence: float
    evidence_reason: str


@dataclass(frozen=True)
class ExtractionResult:
    proposals: tuple[ExtractedProposal, ...]
    provider: str
    model: str


def bounded_user_text(text: str, limit: int) -> str:
    return text[:limit]


async def collect_bounded_completion(
    provider: LLMProvider,
    messages: list[ChatMessage],
    options: GenerationOptions,
    *,
    max_chars: int,
    timeout_s: float,
) -> tuple[str, str, str]:
    """Collect the streaming interface without allowing unbounded time or output."""

    async with asyncio.timeout(timeout_s):
        stream = await provider.stream(messages, options)
        pieces: list[str] = []
        size = 0
        try:
            async for part in stream.parts:
                if not part.text:
                    continue
                size += len(part.text)
                if size > max_chars:
                    raise ExtractionOutputTooLarge("Extraction output exceeded its character limit.")
                pieces.append(part.text)
        finally:
            close = getattr(stream.parts, "aclose", None)
            if close is not None:
                await close()
    return "".join(pieces), stream.info.name, stream.info.model


def parse_extraction_output(raw: str, *, max_item_chars: int) -> tuple[ExtractedProposal, ...]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ExtractionOutputError("Extraction output was not valid JSON.") from error
    if not isinstance(value, dict) or set(value) != {"proposals"}:
        raise ExtractionOutputError("Extraction output must contain only proposals.")
    proposals = value["proposals"]
    if not isinstance(proposals, list) or len(proposals) > MAX_PROPOSALS:
        raise ExtractionOutputError(f"Extraction output must contain at most {MAX_PROPOSALS} proposals.")
    parsed: list[ExtractedProposal] = []
    expected = {"operation", "kind", "content", "confidence", "evidenceReason"}
    for item in proposals:
        if not isinstance(item, dict) or set(item) != expected:
            raise ExtractionOutputError("A proposal did not match the extraction schema.")
        if item["operation"] != "add" or item["kind"] not in MEMORY_KINDS:
            raise ExtractionOutputError("A proposal used an unsupported operation or kind.")
        if isinstance(item["confidence"], bool) or not isinstance(item["confidence"], (int, float)):
            raise ExtractionOutputError("A proposal confidence was not numeric.")
        confidence = float(item["confidence"])
        if not 0 <= confidence <= 1:
            raise ExtractionOutputError("A proposal confidence was outside zero to one.")
        reason = item["evidenceReason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            raise ExtractionOutputError("A proposal evidence reason was invalid.")
        if not isinstance(item["content"], str):
            raise ExtractionOutputError("A proposal content value was not text.")
        try:
            content = normalize_content(item["content"], max_chars=max_item_chars)
            validate_memory_policy(content)
        except ValidationFailed as error:
            raise ExtractionOutputError("A proposal violated memory policy.") from error
        parsed.append(
            ExtractedProposal(
                operation="add",
                kind=item["kind"],
                content=content,
                confidence=confidence,
                evidence_reason=reason.strip(),
            )
        )
    return tuple(parsed)


async def extract_user_memories(provider: LLMProvider, settings: Settings, user_text: str) -> ExtractionResult:
    source = bounded_user_text(user_text, settings.memory_worker_max_input_chars)
    messages = [ChatMessage("system", _SYSTEM_PROMPT), ChatMessage("user", source)]
    raw, provider_name, model = await collect_bounded_completion(
        provider,
        messages,
        GenerationOptions(
            model=settings.memory_worker_model or None,
            temperature=0.0,
            max_output_tokens=settings.memory_worker_max_output_tokens,
        ),
        max_chars=settings.memory_worker_max_output_chars,
        timeout_s=settings.memory_worker_timeout_s,
    )
    return ExtractionResult(
        parse_extraction_output(raw, max_item_chars=settings.memory_max_item_chars), provider_name, model
    )
