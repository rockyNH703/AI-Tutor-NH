from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, MemoryCommandReceipt, Message, User, UserSettings
from app.errors import AppError, Conflict
from app.memory.foundation import lock_memory_user
from app.memory.preferences import MemoryPreferences
from app.memory.service import MemoryService
from app.settings import Settings

# Only the explicit forms are commands, on one line and without a question mark. "Remember how BGP picks a
# path?", "Forget about NAT, explain PAT" or a pasted config that starts with "remember" go to the assistant.
_REMEMBER = re.compile(r"remember\s+that\s+(.+?)\s*[.!]?", re.IGNORECASE)
_FORGET = re.compile(r"forget\s+that\s+(.+?)\s*[.!]?", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedMemoryCommand:
    kind: str
    content: str


def parse_memory_command(text: str) -> ParsedMemoryCommand | None:
    candidate = text.strip()
    if "\n" in candidate or "\r" in candidate or "?" in candidate:
        return None
    if match := _REMEMBER.fullmatch(candidate):
        return ParsedMemoryCommand("remember", match.group(1).strip())
    if match := _FORGET.fullmatch(candidate):
        return ParsedMemoryCommand("forget", match.group(1).strip())
    return None


def command_refusal(kind: str, error: Exception) -> str:
    """The reply to a command the memory layer refused: its fixed public reason, never the user's own text."""
    if isinstance(error, IntegrityError):
        if kind == "remember":
            return "That is already in your memory."
        return "I couldn’t forget that right now. Please check Settings → Memory."
    reason = error.message if isinstance(error, AppError) else AppError.message
    return f"I can’t save that: {reason}" if kind == "remember" else f"I can’t do that: {reason}"


def request_hash(command: ParsedMemoryCommand) -> str:
    return hashlib.sha256(f"{command.kind}\0{command.content}".encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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
    started_at = receipt.created_at
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
            await lock_memory_user(session, user_id)
            settings_row = await session.get(UserSettings, user_id, with_for_update=True)
            current = MemoryPreferences.from_settings(settings_row)
            receipt = await session.get(MemoryCommandReceipt, receipt_id, with_for_update=True)
            assistant_message = await session.get(Message, assistant_message_id, with_for_update=True)
            source_eligible = (
                await session.scalar(
                    select(Message.id)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Message.id == source_message_id,
                        Message.role == "user",
                        Message.status == "complete",
                        Conversation.user_id == user_id,
                    )
                )
            ) is not None
            if receipt is None or assistant_message is None:
                if assistant_message is not None and assistant_message.status == "streaming":
                    assistant_message.status = "cancelled"
                    await session.commit()
                return ""
            cleared_before = current.cleared_before
            cleared_after_start = cleared_before is not None and _utc(cleared_before) >= _utc(started_at)
            suppressed = await memory.suppressed(user_id, content_hash)
            receipt_eligible = (
                receipt.user_id == user_id
                and receipt.request_hash == digest
                and receipt.message_id == assistant_message_id
                and receipt.status == "started"
            )
            if (
                await cancelled()
                or assistant_message.status != "streaming"
                or not receipt_eligible
                or not source_eligible
                or cleared_after_start
                or suppressed
            ):
                receipt.status = "failed"
                receipt.completed_at = datetime.now(UTC)
                if assistant_message.status == "streaming":
                    assistant_message.status = "cancelled"
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
