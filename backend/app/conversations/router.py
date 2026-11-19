from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.students import is_nh_student
from app.assessments import state as assessments
from app.auth.dependencies import Principal, get_principal
from app.conversations.repository import ConversationRepository
from app.conversations.schemas import (
    ConversationOut,
    ConversationPage,
    CreateConversationRequest,
    MessageAttachmentOut,
    MessageOut,
    MessagePage,
    RenameConversationRequest,
)
from app.db.models import Conversation, Message
from app.db.session import get_session
from app.errors import NotFound
from app.logging import get_logger

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
log = get_logger("app.assessments")


def conversation_out(c: Conversation) -> ConversationOut:
    return ConversationOut(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)


HIDDEN_REPLY = "This reply isn't available."


def nhprep_derived(metadata: dict | None) -> bool:
    """Whether a reply shows NHprep content: NHprep's own cards, or a bank test with NHprep questions."""
    metadata = metadata or {}
    bank = metadata.get("bank") if isinstance(metadata.get("bank"), dict) else {}
    return "nhprep" in metadata or bank.get("source") == "NHprep"


def message_out(
    m: Message, attachments=(), withdrawn: frozenset[str] = frozenset(), *, entitled: bool = True
) -> MessageOut:
    """A message as its reader may see it. `entitled`: the reader is a NetworkersHome student
    (app/access/students.py); for anyone else an NHprep-derived reply is one neutral line, with no test or cards."""
    if not entitled and m.role == "assistant" and nhprep_derived(m.metadata_):
        return MessageOut(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=HIDDEN_REPLY,
            status=m.status,
            created_at=m.created_at,
            metadata={},
            attachments=[],
        )
    metadata = public_metadata(m.metadata_, m.id, m.status, m.content if m.role == "assistant" else "")
    if withdrawn and metadata.get("citations"):
        metadata["citations"] = [_redacted(citation, withdrawn) for citation in metadata["citations"]]
    return MessageOut(
        id=m.id,
        conversation_id=m.conversation_id,
        role=m.role,
        content=m.content,
        status=m.status,
        created_at=m.created_at,
        metadata=metadata,
        attachments=[MessageAttachmentOut(file_id=a.file_id, filename=a.filename) for a in attachments],
    )


def _cited_documents(rows) -> set[str]:
    return {
        str(citation["documentId"])
        for m in rows
        for citation in ((m.metadata_ or {}).get("citations") or [])
        if isinstance(citation, dict) and citation.get("documentId")
    }


def _redacted(citation: dict, withdrawn: frozenset[str]) -> dict:
    """A citation of a book since deleted or whose rights were revoked keeps its reference, not its text."""
    if not isinstance(citation, dict) or str(citation.get("documentId") or "").lower() not in withdrawn:
        return citation
    return {**{key: value for key, value in citation.items() if key != "excerpt"}, "withdrawn": True}


def public_metadata(
    metadata: dict | None, message_id: uuid.UUID | None = None, status: str | None = None, content: str = ""
) -> dict:
    # Only fields a client may rely on; internal diagnostics stay server-side.
    allowed = ("model", "errorCode", "finishReason", "citations", "assessmentError")
    public = {k: v for k, v in (metadata or {}).items() if k in allowed}
    # A practice test or exercise as the student may see it now (app/assessments/state.py): the
    # answers of questions not yet answered stay server-side. A reply saved before its kind of block
    # was recorded gets its record from its text (answerable; stored on the first answer).
    records = assessments.records_for(metadata, content, status or "") if message_id is not None else []
    if records:
        public["assessments"] = [test for record in records if (test := _shown(record, message_id, status))]
    bank = (metadata or {}).get("bank")
    if isinstance(bank, dict):  # the certification bank's reply: what a client shows beside its test, never keys
        public["bank"] = {
            "status": bank.get("status"),
            "title": bank.get("title"),
            "topics": [topic.get("title") for topic in bank.get("topics") or [] if isinstance(topic, dict)],
            "choices": bank.get("choices"),
            "source": bank.get("source"),
            "next": bool(bank.get("setId")) and bank.get("more", True) is not False,
        }
    return public


def _shown(record: dict, message_id: uuid.UUID, status: str | None) -> dict | None:
    """One record as the client may see it; a record this code can't read is left out (logged), never a 500."""
    try:
        return assessments.public(record, message_id, status or "")
    except Exception:  # noqa: BLE001 - a reply stays readable whatever its stored record holds
        log.warning("assessment unreadable", extra={"message_id": str(message_id)})
        return None


async def owned_conversation(repo: ConversationRepository, user_id: uuid.UUID, conversation_id: uuid.UUID):
    conversation = await repo.get(user_id, conversation_id)
    if conversation is None:
        raise NotFound("Conversation not found.")
    return conversation


@router.get("", response_model=ConversationPage)
async def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=200),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    rows, next_cursor = await ConversationRepository(db).list(principal.user.id, limit, cursor)
    return ConversationPage(items=[conversation_out(c) for c in rows], next_cursor=next_cursor)


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversationRequest,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    conversation = await ConversationRepository(db).create(principal.user.id, body.title)
    await db.commit()
    return conversation_out(conversation)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
):
    return conversation_out(await owned_conversation(ConversationRepository(db), principal.user.id, conversation_id))


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: RenameConversationRequest,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    repo = ConversationRepository(db)
    conversation = await owned_conversation(repo, principal.user.id, conversation_id)
    await repo.rename(conversation, body.title)
    await db.commit()
    await db.refresh(conversation)
    return conversation_out(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
):
    if not await ConversationRepository(db).delete(principal.user.id, conversation_id):
        raise NotFound("Conversation not found.")
    await db.commit()
    return Response(status_code=204)


@router.get("/{conversation_id}/messages", response_model=MessagePage)
async def list_messages(
    conversation_id: uuid.UUID,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    before: str | None = Query(None, max_length=200),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    repo = ConversationRepository(db)
    await owned_conversation(repo, principal.user.id, conversation_id)
    rows, older = await repo.messages_page(conversation_id, limit, before)
    attachments = await repo.attachments([m.id for m in rows if m.role == "user"])
    withdrawn = await repo.withdrawn_documents(_cited_documents(rows))
    entitled = is_nh_student(principal.user, request.app.state.settings)
    items = [message_out(m, attachments.get(m.id, ()), withdrawn, entitled=entitled) for m in rows]
    return MessagePage(items=items, next_cursor=older)
