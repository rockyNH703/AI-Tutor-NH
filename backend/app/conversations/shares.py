"""Shared links to chats (owner request 2026-11-26).

A link is a read-only SNAPSHOT of the chat as it was when it was shared, not a live view: what the owner sees in the
share dialog is exactly what a recipient gets, and later messages are never exposed by an old link. Sharing again
("Update link") takes a new snapshot behind a new token and ends the old link.

- The token (32 URL-safe characters, 192 random bits) is shown to the owner once; only its SHA-256 is stored.
- Only the owner's ordinary chats can be shared: a program chat (NH programs, invoice-gated material) never.
- The snapshot holds the title and, per message, its role, its text and when it was written, nothing else: no ids,
  no metadata (sources, citations, cards, tests), no file names (only how many files a message carried), no system
  or hidden messages. A reply that showed NetworkersHome material (NHprep cards, a question-bank test) is replaced by
  one neutral line: that material is for enrolled students only.
- Revoking, deleting the chat or deleting the account ends the link at once (the row is revoked or cascaded away).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.repository import ConversationRepository
from app.db.models import Conversation, ConversationShare, Message, MessageAttachment
from app.errors import NotFound, ValidationFailed

TOKEN_BYTES = 24
TOKEN_PATTERN = r"^[A-Za-z0-9_-]{32}$"  # noqa: S105 - the shape of a share token, not a secret
# A shared chat holds at most this many messages (the most recent ones): a link is a conversation, not an archive.
MAX_SHARED_MESSAGES = 400
WITHHELD_REPLY = "This reply showed NetworkersHome course material, which isn't included in shared chats."
SHARED_STATUSES = ("complete", "cancelled")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _withheld(metadata: dict[str, Any] | None) -> bool:
    """A reply built from NetworkersHome material (NHprep cards, a question-bank test): never in a public link."""
    metadata = metadata or {}
    bank = metadata.get("bank") if isinstance(metadata.get("bank"), dict) else None
    return "nhprep" in metadata or bank is not None


async def _owned_ordinary_chat(db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, *, lock: bool = False):
    conversation = await ConversationRepository(db).get(user_id, conversation_id, lock=lock)
    if conversation is None:
        raise NotFound("Conversation not found.")
    if conversation.program_id is not None:
        raise ValidationFailed("Program chats can't be shared.", code="not_shareable")
    return conversation


async def _snapshot(db: AsyncSession, conversation: Conversation) -> dict[str, Any]:
    rows = list(
        await db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.role.in_(("user", "assistant")),
                Message.status.in_(SHARED_STATUSES),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(MAX_SHARED_MESSAGES)
        )
    )
    rows.reverse()
    counts = dict(
        (
            await db.execute(
                select(MessageAttachment.message_id, func.count())
                .where(MessageAttachment.message_id.in_([m.id for m in rows if m.role == "user"]))
                .group_by(MessageAttachment.message_id)
            )
        ).all()
    )
    messages = []
    for message in rows:
        if not message.content.strip():
            continue
        content = WITHHELD_REPLY if message.role == "assistant" and _withheld(message.metadata_) else message.content
        messages.append(
            {
                "role": message.role,
                "content": content,
                "created_at": message.created_at.isoformat(),
                "attachments": int(counts.get(message.id, 0)),
            }
        )
    return {"title": conversation.title, "messages": messages}


async def live_share(db: AsyncSession, conversation_id: uuid.UUID) -> ConversationShare | None:
    return await db.scalar(
        select(ConversationShare).where(
            ConversationShare.conversation_id == conversation_id, ConversationShare.revoked_at.is_(None)
        )
    )


async def share_status(db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID) -> ConversationShare | None:
    await _owned_ordinary_chat(db, user_id, conversation_id)
    return await live_share(db, conversation_id)


async def _revoke_live(db: AsyncSession, conversation_id: uuid.UUID) -> bool:
    result = await db.execute(
        update(ConversationShare)
        .where(ConversationShare.conversation_id == conversation_id, ConversationShare.revoked_at.is_(None))
        .values(revoked_at=func.clock_timestamp())
    )
    return result.rowcount > 0


async def create_share(
    db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> tuple[str, ConversationShare]:
    """A new link with a fresh snapshot; any live link to the chat ends in the same transaction. Returns the token."""
    conversation = await _owned_ordinary_chat(db, user_id, conversation_id, lock=True)  # one share change at a time
    snapshot = await _snapshot(db, conversation)
    if not snapshot["messages"]:
        raise ValidationFailed("There's nothing in this chat to share yet.", code="nothing_to_share")
    await _revoke_live(db, conversation_id)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    share = ConversationShare(
        conversation_id=conversation_id,
        user_id=user_id,
        token_hash=token_hash(token),
        snapshot=snapshot,
        message_count=len(snapshot["messages"]),
    )
    db.add(share)
    await db.flush()
    await db.refresh(share)
    return token, share


async def revoke_share(db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    """Ends the chat's live link, if it has one (revoking twice is not an error)."""
    await _owned_ordinary_chat(db, user_id, conversation_id, lock=True)
    await _revoke_live(db, conversation_id)


async def shared_chat(db: AsyncSession, token: str) -> tuple[dict[str, Any], datetime] | None:
    """A live link's snapshot and when it was shared; None for an unknown, revoked or deleted one (all alike)."""
    share = await db.scalar(
        select(ConversationShare).where(
            ConversationShare.token_hash == token_hash(token), ConversationShare.revoked_at.is_(None)
        )
    )
    if share is None:
        return None
    return share.snapshot, share.created_at
