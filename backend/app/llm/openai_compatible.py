"""Any OpenAI-compatible chat-completions API (Ollima, OpenRouter, OpenAI, vLLM, ...)."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import AsyncIterator

import httpx
import openai
from openai import AsyncOpenAI

from app.llm.base import (
    ChatMessage,
    GenerationOptions,
    LLMAuthError,
    LLMBadRequest,
    LLMError,
    LLMRateLimited,
    LLMStream,
    LLMTimeout,
    LLMUnavailable,
    ProviderInfo,
    StreamPart,
    Usage,
    mark_request_sent,
)

_SECRETISH = re.compile(r"[A-Za-z0-9_\-*.]{24,}")


def redact_secrets(text: str) -> str:
    """Some providers echo (partial) API keys in errors; logs must never carry them."""
    return _SECRETISH.sub("[redacted]", text)


def map_error(error: BaseException) -> LLMError:
    """Translate SDK/transport exceptions into typed errors. Never leaks vendor text to users."""
    detail = redact_secrets(f"{type(error).__name__}: {str(error)[:300]}")
    if isinstance(error, LLMError):
        return error
    if isinstance(error, (openai.APITimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return LLMTimeout(detail)
    if isinstance(error, openai.RateLimitError):
        return LLMRateLimited(detail)
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return LLMAuthError(detail)
    if isinstance(error, openai.NotFoundError):
        return LLMUnavailable(detail)  # model not served by this provider: another may have it
    if isinstance(error, openai.APIStatusError):
        if error.status_code == 402:  # out of credit
            return LLMUnavailable(detail)
        if error.status_code in (400, 413, 422):
            return LLMBadRequest(detail)
        return LLMUnavailable(detail)
    if isinstance(error, (openai.APIConnectionError, httpx.HTTPError, ConnectionError)):
        return LLMUnavailable(detail)
    return LLMUnavailable(detail)


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        connect_timeout_s: float = 10.0,
        first_token_timeout_s: float = 60.0,
        total_timeout_s: float = 180.0,
        stream_usage: bool = True,
        default_headers: dict[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.first_token_timeout_s = first_token_timeout_s
        self.total_timeout_s = total_timeout_s
        self.stream_usage = stream_usage
        self._client = AsyncOpenAI(
            api_key=api_key or "missing",
            base_url=base_url,
            max_retries=0,  # retries/fallback are decided by FallbackProvider, not hidden in the SDK
            timeout=httpx.Timeout(total_timeout_s, connect=connect_timeout_s),
            default_headers=default_headers,
            http_client=http_client,
        )
        self._configured = bool(api_key)

    async def stream(self, messages: list[ChatMessage], options: GenerationOptions) -> LLMStream:
        if not self._configured:
            raise LLMAuthError(f"{self.name}: no API key configured")
        model = options.model or self.model
        kwargs = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": options.temperature,
            "max_tokens": options.max_output_tokens,
            "stream": True,
        }
        if self.stream_usage:
            kwargs["stream_options"] = {"include_usage": True}
        started = time.monotonic()
        response = None
        try:
            # One deadline for the whole wait (K-18b): a provider dripping role-only chunks used to restart the
            # clock with each one. Read up to the first chunk with text here, so "failed before the first token"
            # is raised from stream() itself and a fallback can still take over.
            async with asyncio.timeout(self.first_token_timeout_s):
                mark_request_sent()  # the end of the turn's pre-LLM interval (app/llm/base.py)
                response = await self._client.chat.completions.create(**kwargs)
                iterator = response.__aiter__()
                first = None
                while first is None:
                    try:
                        chunk = await iterator.__anext__()
                    except StopAsyncIteration:
                        # A reply with no text at all is not an answer (MISS-chat-1): the fallback, if any, answers.
                        raise LLMUnavailable(f"{self.name}: the reply ended before any text") from None
                    part = _to_part(chunk)
                    first = part if part is not None and part.text else None
        except BaseException as error:  # noqa: BLE001
            if response is not None:  # stop the provider generating (and billing) a reply nobody will read
                with contextlib.suppress(Exception):
                    await response.close()
            if isinstance(error, asyncio.CancelledError | LLMError):
                raise
            raise map_error(error) from error

        return LLMStream(
            info=ProviderInfo(self.name, model),
            parts=self._parts(first, iterator, started, response),
        )

    async def _parts(self, first: StreamPart, iterator, started: float, response) -> AsyncIterator[StreamPart]:
        deadline = started + self.total_timeout_s
        try:
            yield first
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMTimeout(f"{self.name}: total generation time exceeded")
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    return
                part = _to_part(chunk)
                if part is not None:
                    yield part
        except LLMError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            raise map_error(error) from error
        finally:
            # Closing the HTTP response is what stops the provider generating (and
            # billing) after a stop, a timeout or a mid-stream failure.
            with contextlib.suppress(Exception):
                await response.close()


def _to_part(chunk) -> StreamPart | None:
    text = ""
    finish = None
    if chunk.choices:
        choice = chunk.choices[0]
        text = (choice.delta.content or "") if choice.delta else ""
        finish = choice.finish_reason
    usage = None
    if getattr(chunk, "usage", None):
        usage = Usage(input_tokens=chunk.usage.prompt_tokens, output_tokens=chunk.usage.completion_tokens)
    if not text and usage is None and finish is None:
        return None
    return StreamPart(text=text, usage=usage, finish_reason=finish)
