from __future__ import annotations

import time
import uuid
from dataclasses import replace

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.students import is_nh_student
from app.assessments import state as assessments
from app.auth.dependencies import Principal, get_principal
from app.chat.schemas import (
    AssessmentResponseOut,
    AssessmentResponseRequest,
    CancelOut,
    ChatRequest,
    ChatResponse,
    FeedbackOut,
    FeedbackRequest,
)
from app.chat.service import ChatService, PreparedTurn, collect
from app.chat.streams import sse_body
from app.conversations.repository import ConversationRepository
from app.conversations.router import conversation_out, message_out, nhprep_derived
from app.conversations.schemas import ConversationOut, MessageOut
from app.db.models import MessageFeedback
from app.db.session import get_session
from app.errors import Conflict, NotFound, RateLimited, ValidationFailed, error_body
from app.logging import get_logger
from app.progress.learning import StreakEvent, now, record_practice
from app.qbank import bank_tests, chat_tests

router = APIRouter(prefix="/v1", tags=["chat"])
log = get_logger("app.assessments")

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
ERROR_STATUS = {"llm_unavailable": 502, "llm_timeout": 504, "llm_bad_request": 422, "service_unavailable": 503}


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat


RECEIVED_HEADER = "x-netsec-received-at"  # epoch milliseconds, set by the web app's BFF once it has the body
RECEIVED_SKEW_S = 30.0


def received_at(request: Request) -> float:
    """When the question reached the app: the BFF's stamp when it is plausible (the API listens on loopback only,
    behind the BFF), else now. It only times the turn; nothing is decided by it."""
    now = time.time()
    raw = request.headers.get(RECEIVED_HEADER, "")
    if raw.isdigit() and len(raw) <= 16:
        stamped = int(raw) / 1000
        if now - RECEIVED_SKEW_S <= stamped <= now + 1:
            return stamped
    return now


async def _stream(
    chat: ChatService, turn: PreparedTurn, request: Request, db: AsyncSession, arrived: float
) -> StreamingResponse:
    # The request's session would otherwise stay checked out (idle in a transaction opened by prepare's
    # reads) for the whole reply: with 5+5 pooled connections, a few long replies starve everyone (K-07a).
    await db.close()
    generation = chat.start(replace(turn, received_at=arrived))
    keepalive = request.app.state.settings.stream_keepalive_s
    return StreamingResponse(
        sse_body(turn.meta_event(), generation, keepalive), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.post("/chat/stream", summary="Send a message and stream the reply (Server-Sent Events)")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    chat: ChatService = Depends(get_chat_service),
):
    arrived = received_at(request)
    turn = await chat.prepare_message(
        db,
        principal.user,
        body.conversation_id,
        body.message,
        body.attachment_ids,
        client_request_id=body.client_request_id,
        voice=body.mode == "voice",
        speech_format=body.speech_format,
    )
    return await _stream(chat, turn, request, db, arrived)


@router.post("/chat", response_model=ChatResponse, summary="Send a message and wait for the whole reply")
async def chat_complete(
    body: ChatRequest,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    chat: ChatService = Depends(get_chat_service),
):
    turn = await chat.prepare_message(
        db,
        principal.user,
        body.conversation_id,
        body.message,
        body.attachment_ids,
        client_request_id=body.client_request_id,
        voice=body.mode == "voice",
        speech_format=body.speech_format,
    )
    await db.close()  # not held while the whole reply is generated (K-07a)
    generation = chat.start(turn)
    final, _ = await collect(generation)
    if final.type == "error":
        code = final.data.get("code", "internal_error")
        return JSONResponse(
            status_code=ERROR_STATUS.get(code, 500),
            content=error_body(code, final.data.get("message", ""), messageId=final.data.get("messageId")),
        )
    return ChatResponse(
        conversation=ConversationOut.model_validate(final.data["conversation"]),
        user_message=message_out(turn.user_message, turn.user_attachments) if turn.user_message else None,
        message=MessageOut.model_validate(final.data["message"]),
    )


@router.post(
    "/conversations/{conversation_id}/regenerate/stream",
    summary="Replace the last reply with a new one (Server-Sent Events)",
)
async def regenerate_stream(
    conversation_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    chat: ChatService = Depends(get_chat_service),
):
    arrived = received_at(request)
    turn = await chat.prepare_regenerate(db, principal.user, conversation_id)
    return await _stream(chat, turn, request, db, arrived)


@router.post("/messages/{message_id}/cancel", response_model=CancelOut, summary="Stop a reply being generated")
async def cancel_message(
    message_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
    chat: ChatService = Depends(get_chat_service),
):
    return CancelOut(status=await chat.cancel(db, principal.user, message_id))


async def _owned_assistant_message(db: AsyncSession, user_id: uuid.UUID, message_id: uuid.UUID):
    message = await ConversationRepository(db).get_message(user_id, message_id)
    if message is None or message.role != "assistant":
        raise NotFound("Message not found.")
    return message


@router.put("/messages/{message_id}/feedback", response_model=FeedbackOut, tags=["feedback"])
async def put_feedback(
    message_id: uuid.UUID,
    body: FeedbackRequest,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    await _owned_assistant_message(db, principal.user.id, message_id)
    rating = 1 if body.rating == "up" else -1
    comment = (body.comment or "").strip() or None
    statement = insert(MessageFeedback).values(
        message_id=message_id, user_id=principal.user.id, rating=rating, comment=comment
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=["message_id", "user_id"],
            set_={"rating": rating, "comment": comment, "updated_at": statement.excluded.created_at},
        )
    )
    await db.commit()
    return FeedbackOut(message_id=message_id, rating=body.rating, comment=comment)


@router.get("/messages/{message_id}/feedback", response_model=FeedbackOut, tags=["feedback"])
async def get_feedback(
    message_id: uuid.UUID, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
):
    await _owned_assistant_message(db, principal.user.id, message_id)
    row = await db.scalar(
        select(MessageFeedback).where(
            MessageFeedback.message_id == message_id, MessageFeedback.user_id == principal.user.id
        )
    )
    if row is None:
        raise NotFound("No feedback for this message.")
    return FeedbackOut(message_id=message_id, rating="up" if row.rating > 0 else "down", comment=row.comment)


REFUSED = {
    "closed": "This can no longer be answered.",
    "changed": "This has changed. Reload to see it.",
    "corrupt": "This can't be answered. Ask for a new one.",
    "unsupported": "This can't be answered. Ask for a new one.",
    "answered": "This question already has an answer.",
    "out_of_order": "Answer the waiting question first.",
    # schemaVersion 2 (app/assessments/modes.py)
    "not_exam": "This test checks each answer as you give it; nothing is saved for later.",
    "exam": "In a mock exam, answers are saved and graded together when you submit.",
    "submitted": "This mock exam has been submitted.",
    "stale": "Your answers changed in another tab. Reload to see them.",
}
# A v2 test's 422: what was wrong with the value sent for the named question (never the value itself).
INVALID = {
    "question": "No such question in this test.",
    "value": "This answer doesn't fit the question.",
    "number": "Use digits, with an optional leading minus and a full stop for decimals (for example -12.5).",
    "duplicate": "Each choice can be picked only once.",
    "too_long": "This answer is too long.",
    "drafts_full": "Your saved answers are too long to keep. Shorten one and save again.",
}


async def _bank_step(
    db: AsyncSession, user_id: uuid.UUID, record: dict, extra: dict
) -> tuple[dict, StreakEvent | None]:
    """A question-bank test's new response in the practice ledger (app/qbank/chat_tests.py), in the
    transaction and under the lock that records it on the message: the answer (a skip records
    nothing), then the next question of an adaptive test chosen from it, or the set closed once the
    test is complete; then the learning day the answer may make (record_practice)."""
    source = record["source"]
    set_id = uuid.UUID(source["setId"])
    response = record["responses"][-1]
    position = response["question"] + 1  # the ledger counts served questions from 1
    answered = None
    if response["status"] == "answered" and source.get("keys"):  # a certification-bank test (0026)
        question = record["questions"][response["question"]]
        chosen = response.get("answer") if question["kind"] == "multi" else [response["value"]]
        letters = [bank_tests.LETTERS[index] for index in chosen]
        answered = await bank_tests.answer_keys(db, user_id, set_id, position, letters)
        if answered is not None and answered[1] != bool(response["earned"]):
            log.warning("assessment key drift", extra={**extra, "set_id": str(set_id)})
    elif response["status"] == "answered":
        letter = assessments.LETTERS[response["value"]]
        answered = await chat_tests.answer(db, user_id, set_id, position, letter)
        if answered is not None and answered[1] != bool(response["earned"]):
            # Only a question edited since it was served is graded differently now; the student keeps
            # the grade of the key they saw, and mastery sets the answer aside (its served_hash).
            log.warning("assessment key drift", extra={**extra, "set_id": str(set_id)})
    planned = record["total"]
    if record.get("adaptive") and assessments.state_of(record) != "completed" and position == len(record["questions"]):
        row = await chat_tests.next_question(db, user_id, set_id, position)
        record = assessments.extend(record, chat_tests.quiz_question(row) if row is not None else None)
        if row is not None:
            record["source"] = {**source, "questionIds": [*source["questionIds"], row.question_id]}
    if assessments.state_of(record) == "completed":
        await chat_tests.close(db, user_id, set_id, ended_early=record["total"] < planned)
    learning = await record_practice(db, user_id, now(), answered[0]) if answered is not None else None
    return record, learning


@router.post(
    "/messages/{message_id}/assessment-responses",
    response_model=AssessmentResponseOut,
    # Exactly the fields public() gives, so a response equals the reloaded metadata: a schemaVersion 1 record
    # keeps its v1 shape, and a v2 record has every field.
    response_model_exclude_unset=True,
    tags=["assessments"],
    summary="Answer, skip, save, clear or submit a practice test or mock exam in a reply",
)
async def respond_to_assessment(
    message_id: uuid.UUID,
    body: AssessmentResponseRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_session),
):
    """One action on a reply's test, graded and recorded here; values are never logged.

    `answer` and `skip` (practice): final, in order. On schemaVersion 2 a numeric or CLI answer gets
    three tries; a wrong try is kept in `pending` and reveals nothing. `save`, `clear` and `submit`
    are for a schemaVersion 2 mock exam only (409 on any other test, schemaVersion 1 included, and
    `answer` is 409 on an exam): `save` keeps a question's draft (checked like an answer, graded
    only on submit), `clear` empties it, and `submit` (question 0, with the record's `revision`; a
    stale one is 409) grades every draft, a missing one as skipped. After submit any other action
    is 409.

    A request that would not change the test (an exact replay, the same draft, a second submit)
    returns it unchanged (a safe retry); any other response to an answered question, or to a
    question after the waiting one, is 409. Requests are limited per user (429). The message row is
    locked while the action is recorded, so two tabs or a double tap record one.

    A question-bank test also records the answer in the user's practice ledger and, while it is
    adaptive, adds its next question chosen from that answer, all in the same transaction: a retry
    after a lost reply (with the hash it was shown) gets the test as it is now. `learning` says what
    this answer did to the streak when it made its India day a learning day (5 answers a day), as the
    chat's `done` does; null otherwise."""
    # Mock-exam saves arrive in bursts (a debounced save per edit): bounded per user before the row lock.
    if not request.app.state.assessment_limiter.allow(str(principal.user.id)):
        raise RateLimited()
    message = await ConversationRepository(db).get_message(principal.user.id, message_id, lock=True)
    if message is None or message.role != "assistant":
        raise NotFound("Message not found.")
    if nhprep_derived(message.metadata_) and not is_nh_student(principal.user, request.app.state.settings):
        raise NotFound("Message not found.")  # hidden from a reader who isn't a NetworkersHome student
    # Stored, or (a reply saved before its kind of block was recorded) built from its text exactly as
    # the client was shown it; this first answer stores it.
    stored = bool((message.metadata_ or {}).get("assessments"))
    records = list(assessments.records_for(message.metadata_, message.content, message.status))
    if body.position >= len(records):
        raise NotFound("This reply has no such test.")
    # Ids, positions, actions and reason codes only: never a value or an answer.
    extra = {"message_id": str(message_id), "position": body.position, "question": body.question}
    extra["action"] = body.action
    before = len(records[body.position].get("responses") or [])
    try:
        record, changed = assessments.respond(
            records[body.position],
            status=message.status,
            client_hash=body.hash,
            question=body.question,
            action=body.action,
            value=body.value,
            at=now().isoformat(),
            revision=body.revision,
        )
    except assessments.Refused as refused:
        log.warning(
            "assessment hash mismatch" if refused.reason == "corrupt" else "assessment conflict",
            extra={**extra, "reason": refused.reason},
        )
        raise Conflict(REFUSED[refused.reason], code=f"assessment_{refused.reason}") from None
    except assessments.Invalid as invalid:
        reason = str(invalid)
        log.info("assessment invalid", extra={**extra, "reason": reason})
        if records[body.position].get("schemaVersion") == 1:
            raise ValidationFailed("No such question, option or blank.") from None
        message_text = f"Question {body.question + 1}: {INVALID.get(reason, INVALID['value'])}"
        raise ValidationFailed(
            message_text, fields=[{"field": "value", "reason": reason, "question": body.question}]
        ) from None
    learning, added = None, False
    if changed:
        # A save, a clear or a wrong try changes the record without a new response: only a new response is
        # practice evidence, and only it is logged as one.
        added = len(record["responses"]) > before
        if added and (record.get("source") or {}).get("setId"):  # a question-bank test (since 0017)
            record, learning = await _bank_step(db, principal.user.id, record, extra)
        records[body.position] = record
        message.metadata_ = {**message.metadata_, "assessments": records}
        await db.commit()
        if not stored:
            log.info("assessment created", extra={**extra, "questions": record["total"], "from": "earlier reply"})
    shown = assessments.public(record, message.id, message.status)
    if added:
        latest = record["responses"][-1]
        log.info("assessment response", extra={**extra, "status": latest["status"], "earned": latest["earned"]})
        if shown["state"] == "completed":
            log.info("assessment completed", extra={**extra, "earned": shown["earned"], "possible": shown["possible"]})
    event = {"event": learning.kind, "streak": learning.streak} if learning is not None else None
    return {**shown, "learning": event}


@router.delete("/messages/{message_id}/feedback", status_code=status.HTTP_204_NO_CONTENT, tags=["feedback"])
async def delete_feedback(
    message_id: uuid.UUID, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_session)
):
    await _owned_assistant_message(db, principal.user.id, message_id)
    await db.execute(
        delete(MessageFeedback).where(
            MessageFeedback.message_id == message_id, MessageFeedback.user_id == principal.user.id
        )
    )
    await db.commit()
    return Response(status_code=204)


__all__ = ["router", "conversation_out"]
