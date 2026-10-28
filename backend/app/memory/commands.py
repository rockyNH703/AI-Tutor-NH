from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MemoryCommandReceipt, Message, User
from app.errors import Conflict
from app.memory.service import MemoryService
from app.settings import Settings

_REMEMBER = re.compile(r"^\s*remember(?:\s+that)?\s+(.+?)\s*[.!]?\s*$", re.IGNORECASE | re.DOTALL)
_FORGET = re.compile(r"^\s*forget(?:\s+that|\s+about)?\s+(.+?)\s*[.!]?\s*$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ParsedMemoryCommand:
    kind: str
    content: str


def parse_memory_command(text: str) -> ParsedMemoryCommand | None:
    if match := _REMEMBER.fullmatch(text):
        return ParsedMemoryCommand("remember", match.group(1).strip())
    if match := _FORGET.fullmatch(text):
        return ParsedMemoryCommand("forget", match.group(1).strip())
    return None


def request_hash(command: ParsedMemoryCommand) -> str:
    return hashlib.sha256(f"{command.kind}\0{command.content}".encode()).hexdigest()


async def replay_receipt(
    session: AsyncSession, user_id: uuid.UUID, client_request_id: str, expected_hash: str
) -> tuple[MemoryCommandReceipt, Message] | None:
    receipt = await session.scalar(
        select(MemoryCommandReceipt).where(
            MemoryCommandReceipt.user_id == user_id,
            MemoryCommandReceipt.client_request_id == client_request_id,
        )
    )
    if receipt is None:
        return None
    if receipt.request_hash != expected_hash:
        raise Conflict("That request id was already used for another memory command.")
    message = await session.get(Message, receipt.message_id)
    return (receipt, message) if message is not None else None


async def apply_memory_command(
    session: AsyncSession,
    settings: Settings,
    embeddings,
    user: User,
    source_message: Message,
    assistant_message: Message,
    command: ParsedMemoryCommand,
    receipt_id: uuid.UUID,
    *,
    cancelled,
) -> str:
    memory = MemoryService(session, settings, embeddings)
    digest = request_hash(command)
    user_id = user.id
    source_message_id = source_message.id
    assistant_message_id = assistant_message.id
    receipt = await session.get(MemoryCommandReceipt, receipt_id)
    if receipt is None or receipt.user_id != user_id or receipt.request_hash != digest:
        raise Conflict("That memory command is no longer available.")
    if receipt.status == "completed":
        existing = await session.get(Message, receipt.message_id)
        if existing is None:
            raise Conflict("That memory command result is no longer available.")
        return existing.content
    if await cancelled():
        assistant_message.status = "cancelled"
        receipt.status = "failed"
        receipt.completed_at = datetime.now(UTC)
        await session.commit()
        return ""

    if command.kind == "remember":
        normalized, content_hash = memory._normalize(command.content)
        if await memory.suppressed(user.id, content_hash):
            acknowledgement = "I won’t restore that deleted memory unless you add it again in Settings → Memory."
        else:
            embedding = await memory._embed(normalized)
            if await cancelled():
                assistant_message.status = "cancelled"
                receipt.status = "failed"
                receipt.completed_at = datetime.now(UTC)
                await session.commit()
                return ""
            await session.rollback()
            receipt = await session.get(MemoryCommandReceipt, receipt_id, with_for_update=True)
            assistant_message = await session.get(Message, assistant_message_id, with_for_update=True)
            if receipt is None or assistant_message is None:
                raise Conflict("That memory command is no longer available.")
            if await cancelled() or assistant_message.status != "streaming":
                receipt.status = "failed"
                receipt.completed_at = datetime.now(UTC)
                await session.commit()
                return ""
            item = await memory.repo.create(
                user_id=user_id,
                kind="study_context",
                content=normalized,
                content_hash=content_hash,
                source_message_id=source_message_id,
                expires_at=None,
                embedding=embedding,
                embedding_model=memory.model,
                embedding_dimensions=settings.embedding_dimensions,
                embedding_version=settings.memory_embedding_version,
                origin="explicit_chat",
            )
            acknowledgement = "I’ll remember that."
            receipt.result_memory_id = item.id
    else:
        matches = await memory.find_exact_active(user.id, command.content)
        if await cancelled():
            assistant_message.status = "cancelled"
            receipt.status = "failed"
            receipt.completed_at = datetime.now(UTC)
            await session.commit()
            return ""
        if len(matches) == 1:
            hashes = await memory.repo.chain_content_hashes(user.id, matches[0].id)
            await memory.repo.add_deletion_markers(
                user.id,
                [
                    (
                        memory._content_hmac(user.id, content_hash),
                        settings.memory_deletion_policy_version,
                        "delete",
                    )
                    for content_hash in hashes
                ],
            )
            await memory.repo.delete_chain(user.id, matches[0].id)
            acknowledgement = "I forgot that memory."
        elif matches:
            acknowledgement = "I found more than one matching memory. Please remove the right one in Settings → Memory."
        else:
            acknowledgement = "I couldn’t find an exact remembered item. Please check Settings → Memory."

    if await cancelled():
        await session.rollback()
        async with session.begin():
            assistant = await session.get(Message, assistant_message.id, with_for_update=True)
            receipt = await session.get(MemoryCommandReceipt, receipt_id, with_for_update=True)
            if assistant is not None and assistant.status == "streaming":
                assistant.status = "cancelled"
            if receipt is not None:
                receipt.status = "failed"
                receipt.completed_at = datetime.now(UTC)
        return ""
    assistant_message.content = acknowledgement
    assistant_message.status = "complete"
    assistant_message.metadata_ = {"memoryCommand": {"kind": command.kind}}
    receipt.status = "completed"
    receipt.completed_at = datetime.now(UTC)
    await session.commit()
    return acknowledgement
