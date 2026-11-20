"""The chat core.

prepare (request transaction)                      generate (independent task)
───────────────────────────────                    ────────────────────────────────────
load / create conversation (owned)                 load the conversation from PostgreSQL
sweep stale `streaming` rows, 409 if one is live   read what it asks for (INTENT_ROUTER, app/intents)
per-user quota (usage_events)                      run the LangGraph turn graph (retrieve ->
insert user message + assistant placeholder          assemble -> generate), push token events
  (+ its attached files, owned and ready)          stop on cancel (event or DB flag)
commit                                             persist content + status + retrieval summary

The browser only ever sends a conversation id, the new text and the ids of files it attaches.
History, identity, attachments and message persistence are owned here, in PostgreSQL. Retrieval and prompt assembly
belong to the graph (app/rag/graph.py); this layer never touches embeddings or pgvector.
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.students import is_nh_student
from app.assessments import state as assessments
from app.auth.service import AuthService
from app.chat import bank_turn
from app.chat.assistant import AssistantConfig, resolve_assistant_config
from app.chat.streams import ActiveGenerations, ChatEvent, Generation
from app.chat.titles import topic_title
from app.conversations.repository import DEFAULT_TITLE, ConversationRepository
from app.conversations.router import HIDDEN_REPLY, conversation_out, message_out, nhprep_derived
from app.db.models import Conversation, MemoryCommandReceipt, Message, MessageAttachment, UsageEvent, User
from app.db.session import Database
from app.errors import Conflict, NotFound, RateLimited, ServiceUnavailable, ValidationFailed
from app.files import service as files
from app.intents.classifier import HINT_ONLY, IntentClassifier, Routed, explicit_hint_only
from app.intents.scope import ALLOW_STUDY, INSTRUCTIONS, NOT_LEARNING, REDIRECT, Scope, redirect_copy, settled
from app.intents.scope import stored as stored_scope
from app.intents.taxonomy import BANK_INTENTS, INTENTS
from app.jobs.memory import enqueue_memory_extraction
from app.learning.context import StudentContext, certification_code, read_student_context
from app.llm.base import LLM_REQUEST_AT, ChatMessage, GenerationOptions, LLMError
from app.logging import get_logger, request_id_var
from app.memory.commands import (
    apply_memory_command,
    command_refusal,
    parse_memory_command,
    replay_receipt,
    request_hash,
)
from app.memory.recall import MemoryRecall
from app.nhprep.chat import Card, NhprepChat, nhprep_allows
from app.progress.activity import record_question
from app.progress.learning import is_meaningful, now, record_learning, tutor_progress
from app.qbank import bank_tests, chat_tests
from app.settings import Settings
from app.voice import first_speech
from app.voice.expression import expression_for
from app.voice.policy import speaks_their_language
from app.voice.provider import VoiceProvider

if TYPE_CHECKING:
    from app.voice.router import SpeechBudget

log = get_logger("app.chat")

# A reply this slow is logged as a warning (operators see it without reading every "generation finished").
SLOW_TTFT_MS = 20_000
SLOW_TOTAL_MS = 120_000
# The longest the reply's end waits for the server's first words to be spoken (VOICE_SERVER_SPEECH).
FIRST_SPEECH_WAIT_S = 6.0

PUBLIC_ERRORS = {
    "llm_unavailable": ("The AI service is temporarily unavailable. Please try again.", True),
    "llm_rate_limited": ("The AI service is busy right now. Please try again in a moment.", True),
    "llm_timeout": ("The AI service took too long to respond. Please try again.", True),
    "llm_bad_request": (
        "The AI model could not process this request. Try a shorter message or a new conversation.",
        False,
    ),
    "service_unavailable": ("The service is temporarily unavailable. Please try again.", True),
    "internal_error": ("Something went wrong while generating the reply.", True),
}


def _pre_llm(received_at: float | None, sent_at: float | None) -> dict | None:
    """The pre-LLM interval in milliseconds, when both ends are known (no model call: no interval)."""
    if received_at is None or sent_at is None:
        return None
    return {"preLlmMs": max(0, int((sent_at - received_at) * 1000))}


def public_citations(summary: dict | None) -> list[dict] | None:
    """Citation records safe for the citation view; never expose source URIs or ranking scores."""
    if not summary:
        return None
    citations = []
    for number, source in enumerate(summary.get("sources") or [], start=1):
        citation = {
            "ref": str(number),
            "kind": "source",
            "title": str(source.get("title") or source.get("source") or f"Source {number}"),
        }
        for key in (
            "documentId",
            "revisionId",
            "stableChunkKey",
            "sectionPath",
            "physicalPage",
            "printedPageLabel",
            "excerpt",
        ):
            if source.get(key) is not None:
                citation[key] = source[key]
        citations.append(citation)
    citations += [
        {"ref": str(file["ref"]), "kind": "file", "title": str(file["filename"]), "fileId": str(file["fileId"])}
        for file in summary.get("fileSources") or []
    ]
    return citations or None


def public_error(code: str) -> dict:
    # Rate limits and outages are the same thing to a user: try again shortly.
    public_code = "llm_unavailable" if code == "llm_rate_limited" else code
    message, retryable = PUBLIC_ERRORS.get(code, PUBLIC_ERRORS["internal_error"])
    return {
        "code": public_code if public_code in PUBLIC_ERRORS else "internal_error",
        "message": message,
        "retryable": retryable,
    }


@dataclass
class PreparedTurn:
    user: User
    conversation: Conversation
    user_message: Message | None  # None for a regeneration
    assistant_id: uuid.UUID
    config: AssistantConfig
    replaces_message_id: uuid.UUID | None = None
    # Files this turn may read: the conversation's most recently attached ready files.
    files: list[dict] = field(default_factory=list)
    user_attachments: list[MessageAttachment] = field(default_factory=list)
    # When the question was accepted (the app's clock): the learning day it counts for.
    asked_at: datetime | None = None
    # What `config` was built from, so an intent the router applies can build it again (_route).
    student: StudentContext | None = None
    tests: str = ""
    bank: str | None = None
    attached_kinds: tuple[str, ...] = ()  # the kinds of the files the new message attaches (pdf, image, ...)
    # messages.metadata.intent (internal) of a regenerated reply, read when it was prepared; a new
    # message is read when its reply is generated (_route).
    intent: dict | None = None
    # messages.metadata.scope (internal, STUDY_SCOPE): the same, for the study scope (_scope).
    scope: dict | None = None
    # Spoken in voice mode (app/voice) by a user it is for: the assistant is told, and the face's expression is sent.
    voice: bool = False
    client_request_id: uuid.UUID | None = None
    command: object | None = None
    command_replay: Message | None = None
    command_receipt_id: uuid.UUID | None = None
    # Voice turns: the server speaks the reply's first words itself (VOICE_SERVER_SPEECH), in the browser's format.
    server_speech: bool = False
    speech_format: str = "mp3"
    # When the question reached the app (wall clock, seconds): the BFF's stamp, else the API's (app/chat/router.py).
    received_at: float | None = None
    usage_id: uuid.UUID | None = None  # the turn's usage_events row (closed even if its chat is deleted)
    # The certification bank's own reply to a quiz request (app/chat/bank_turn.py): no model, no search.
    served: bank_turn.Served | None = None
    # A regeneration of an NHprep card (app/nhprep/chat.py): the replaced reply's metadata.nhprep, so the finder runs
    # again; None for every other turn (a regenerated model reply stays the model's, decided once).
    nhprep: dict | None = None

    def meta_event(self) -> ChatEvent:
        return ChatEvent(
            "meta",
            {
                "conversationId": str(self.conversation.id),
                "conversation": conversation_out(self.conversation).model_dump(mode="json", by_alias=True),
                "userMessage": message_out(self.user_message, self.user_attachments).model_dump(
                    mode="json", by_alias=True
                )
                if self.user_message
                else None,
                "assistantMessageId": str(self.assistant_id),
                "replacesMessageId": str(self.replaces_message_id) if self.replaces_message_id else None,
            },
        )


# Provider finish reasons that mean the reply stopped before it was finished (the web app says so).
CUT_OFF = ("length", "content_filter")


class ChatService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        rag_graph,
        generations: ActiveGenerations,
        intents: IntentClassifier | None = None,
        memory: MemoryRecall | None = None,
        voice: VoiceProvider | None = None,
        voice_budget: SpeechBudget | None = None,
        nhprep: NhprepChat | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.rag_graph = rag_graph  # compiled LangGraph graph (app.rag.graph.build_rag_graph)
        self.generations = generations
        self.intents = intents  # None: no classifier (INTENT_ROUTER off, or no key)
        self.memory = memory
        self.voice = voice  # voice mode's speech (app/voice): the first words of a voice reply (_speak_first)
        self.voice_budget = voice_budget
        self.nhprep = nhprep  # NHprep's lesson cards (app/nhprep/chat.py); None: NHPREP_MODE off

    # ------------------------------------------------------------------ prepare
    async def _sweep_stale(
        self, session: AsyncSession, conversation_id: uuid.UUID | None = None, *, everything: bool = False
    ) -> None:
        """Mark `streaming` rows that can no longer be alive as interrupted, and end their usage rows."""
        query = update(Message).where(Message.status == "streaming")
        if not everything:
            cutoff = func.clock_timestamp() - timedelta(seconds=self.settings.stream_stale_after_s)
            query = query.where(Message.created_at < cutoff)
        if conversation_id is not None:
            query = query.where(Message.conversation_id == conversation_id)
        swept = list(
            await session.scalars(
                query.values(
                    status="error",
                    metadata_=Message.metadata_.op("||")(func.jsonb_build_object("errorCode", "interrupted")),
                ).returning(Message.id)
            )
        )
        if swept:
            await session.execute(
                update(UsageEvent)
                .where(UsageEvent.message_id.in_(swept), UsageEvent.outcome == "streaming")
                .values(outcome="error")
            )

    async def sweep_all_stale(self, *, at_startup: bool = False) -> None:
        """At startup every `streaming` row is dead: the API is one process (netsecai-api.service), so a
        chat is not refused for STREAM_STALE_AFTER_S after a crash or restart."""
        async with self.db.sessionmaker() as session:
            await self._sweep_stale(session, everything=at_startup)
            if at_startup:  # a usage row whose reply was deleted with its chat (message_id SET NULL) is not live
                await session.execute(
                    update(UsageEvent)
                    .where(UsageEvent.outcome == "streaming", UsageEvent.message_id.is_(None))
                    .values(outcome="cancelled")
                )
            await session.commit()

    async def _check_turn_allowed(self, session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        if not self.settings.chat_enabled:  # K-11a: the operator's switch
            raise ServiceUnavailable("Chat is switched off for now. Please try again later.")
        if len(self.generations) >= self.settings.chat_live_generations_max:  # K-07b: this process's share
            raise ServiceUnavailable("The assistant is very busy right now. Please try again in a moment.")
        # Conversation rows serialize one chat; this user-scoped lock also serializes quota
        # check + reservation across different conversations for the same account.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"chat-generation:{user_id}"},
        )
        await self._sweep_stale(session, conversation_id)
        live = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id, Message.status == "streaming")
        )
        if live:
            raise Conflict("A reply is already being generated in this conversation.")
        alive_since = func.clock_timestamp() - timedelta(seconds=self.settings.stream_stale_after_s)
        mine = await session.scalar(  # K-07b: replies this account has generating in all its chats
            select(func.count())
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == user_id, Message.status == "streaming", Message.created_at > alive_since)
        )
        if mine >= self.settings.chat_live_generations_per_user:
            raise RateLimited("Too many replies are being written at once. Wait for one to finish.")
        now = func.clock_timestamp()
        counts = (
            await session.execute(
                select(
                    func.count().filter(UsageEvent.created_at > now - timedelta(minutes=1)),
                    func.count().filter(UsageEvent.created_at > now - timedelta(days=1)),
                ).where(UsageEvent.user_id == user_id, UsageEvent.created_at > now - timedelta(days=1))
            )
        ).one()
        if (
            counts[0] >= self.settings.chat_generations_per_minute
            or counts[1] >= self.settings.chat_generations_per_day
        ):
            raise RateLimited("You have reached the message limit. Please try again later.")
        if self.settings.chat_generations_global_per_day:  # K-11a: every account together
            total = await session.scalar(
                select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at > now - timedelta(days=1))
            )
            if total >= self.settings.chat_generations_global_per_day:
                raise RateLimited("The assistant has reached today's limit. Please try again later.")

    async def _add_placeholder(
        self, session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> tuple[Message, UsageEvent]:
        assistant = Message(conversation_id=conversation_id, role="assistant", content="", status="streaming")
        session.add(assistant)
        await session.flush()
        # Recorded now, not at the end, so concurrent requests count toward the quota. Its own id travels with the
        # turn: if the chat is deleted mid-reply the row loses its message (SET NULL) but is still closed.
        usage = UsageEvent(
            user_id=user_id, conversation_id=conversation_id, message_id=assistant.id, outcome="streaming"
        )
        session.add(usage)
        await session.flush()
        return assistant, usage

    async def _student(self, session: AsyncSession, user: User, asked_at: datetime | None = None) -> StudentContext:
        """What the assistant is told about this user: their settings and how they are doing, as
        before the question being answered (`asked_at` for a regenerated answer)."""
        progress = await tutor_progress(
            session, user.id, user.timezone, self.settings.default_timezone, now(), asked_at
        )
        return await read_student_context(session, user.id, progress)

    async def _memories(self, user_id: uuid.UUID, query: str):
        return () if self.memory is None else await self.memory.recall(user_id, query)

    @staticmethod
    def _memory_context(memories) -> str:
        lines = []
        for item in memories:
            content = " ".join(item.content.replace('"', "'").split())
            lines.append(f'- [{item.kind}] "{content}"')
        return "\n".join(lines)

    def _config(
        self,
        user: User,
        conversation: Conversation,
        student: StudentContext | None,
        tests: str,
        bank: str | None,
        intent: str | None = None,
        *,
        search: bool = True,
        scope: str | None = None,
        voice: bool = False,
    ) -> AssistantConfig:
        """The turn's prompt and retrieval (app/chat/assistant.py), the same way for a new message, a
        regenerated reply, an intent the router applies (_route) and a study scope decision (_scope)."""
        return resolve_assistant_config(
            user,
            conversation,
            student,
            interactive=self.settings.tutor_interactive_blocks,
            tests=tests,
            teaching=self.settings.tutor_teaching_policy,
            bank=bank,
            intent=intent,
            search=search,
            scoped=self.settings.study_scope == "on",
            scope=scope,
            voice=voice,
        )

    async def prepare_message(
        self,
        session: AsyncSession,
        user: User,
        conversation_id: uuid.UUID | None,
        text: str,
        attachment_ids: list[uuid.UUID] | None = None,
        *,
        client_request_id: uuid.UUID | None = None,
        voice: bool = False,
        speech_format: str | None = None,
    ) -> PreparedTurn:
        if len(text) > self.settings.chat_max_message_chars:
            raise ValidationFailed("The message is too long.")
        repo = ConversationRepository(session)
        memory_on = self.settings.memory_enabled  # off: every /v1/memories route is off too
        command = parse_memory_command(text) if memory_on and not attachment_ids and not voice else None
        request_id = client_request_id or (uuid.uuid4() if command is not None else None)
        replay = None
        if command is not None and request_id is not None:
            found = await replay_receipt(session, user.id, str(request_id), request_hash(command))
            if found is not None and found[0].status == "started":
                raise Conflict("That memory command is still being processed.")
            replay = found[1] if found is not None and found[0].status == "completed" else None
        if replay is not None:
            conversation = await repo.get(user.id, replay.conversation_id, lock=True)
            if conversation is None:
                raise NotFound("Conversation not found.")
        elif conversation_id is None:
            # Named after its first answered question when the reply completes (_name_conversation).
            conversation = await repo.create(user.id, DEFAULT_TITLE)
        else:
            conversation = await repo.get(user.id, conversation_id, lock=True)
            if conversation is None:
                raise NotFound("Conversation not found.")
        if replay is not None:
            return PreparedTurn(
                user,
                conversation,
                None,
                replay.id,
                AssistantConfig(system_prompt="", retrieval=None),
                client_request_id=request_id,
                command=command,
                command_replay=replay,
            )
        await self._check_turn_allowed(session, user.id, conversation.id)
        attached = await files.resolve_attachments(session, user.id, list(attachment_ids or []))

        # Read before this question is counted: the same as the Settings preview showed.
        student = await self._student(session, user)
        # Voice mode is English only (app/voice): for anyone else a spoken message is a typed one.
        voice = voice and self.settings.voice_rollout_allows(user.email) and speaks_their_language(student)
        server_speech = (
            voice
            and self.voice is not None
            and self.settings.voice_feature_allows(self.settings.voice_server_speech, user.email)
        )
        tests = await self._tests(session, conversation.id)
        bank = await self._bank(session, text, student, conversation.id)
        # Small talk that opens a chat ("hi") searches nothing; "why?" after a question still does.
        search = is_meaningful(text)
        if not search and conversation_id is not None:
            questions = select(Message.id).where(Message.conversation_id == conversation.id, Message.role == "user")
            search = await session.scalar(questions.limit(1)) is not None
        asked_at = now()
        user_message = Message(conversation_id=conversation.id, role="user", content=text, status="complete")
        session.add(user_message)
        await session.flush()
        await files.add_attachments(session, user_message.id, attached)
        await record_question(session, user.id, user.timezone, self.settings.default_timezone)
        assistant, usage = await self._add_placeholder(session, user.id, conversation.id)
        command_receipt_id = None
        if command is not None and request_id is not None:
            receipt = MemoryCommandReceipt(
                user_id=user.id,
                conversation_id=conversation.id,
                message_id=assistant.id,
                client_request_id=str(request_id),
                command_kind=command.kind,
                request_hash=request_hash(command),
                status="started",
            )
            session.add(receipt)
            try:
                await session.flush()
            except IntegrityError as error:
                await session.rollback()
                raise Conflict("That memory command is already being processed.") from error
            command_receipt_id = receipt.id
        await repo.touch(conversation.id)
        await session.commit()
        await session.refresh(conversation)
        await session.refresh(user_message)
        attachments = (await repo.attachments([user_message.id])).get(user_message.id, [])
        return PreparedTurn(
            user,
            conversation,
            user_message,
            assistant.id,
            self._config(user, conversation, student, tests, bank, search=search, voice=voice),
            files=await files.conversation_files(session, conversation.id, user.id),
            user_attachments=attachments,
            asked_at=asked_at,
            student=student,
            tests=tests,
            bank=bank,
            attached_kinds=tuple(file.kind for file in attached),
            client_request_id=request_id,
            command=command,
            command_replay=replay,
            command_receipt_id=command_receipt_id,
            voice=voice,
            server_speech=server_speech,
            speech_format=speech_format or "mp3",
            usage_id=usage.id,
        )

    async def prepare_regenerate(self, session: AsyncSession, user: User, conversation_id: uuid.UUID) -> PreparedTurn:
        repo = ConversationRepository(session)
        conversation = await repo.get(user.id, conversation_id, lock=True)
        if conversation is None:
            raise NotFound("Conversation not found.")
        await self._check_turn_allowed(session, user.id, conversation.id)
        last = await session.scalar(
            select(Message)
            .where(Message.conversation_id == conversation.id, Message.status.in_(("complete", "cancelled", "error")))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        if last is None or last.role != "assistant":
            raise Conflict("There is no reply to regenerate.")
        acknowledgement = await session.scalar(
            select(MemoryCommandReceipt.id).where(MemoryCommandReceipt.message_id == last.id).limit(1)
        )
        if acknowledgement is not None:  # MISS-chat-6: the memory layer's answer, not the model's
            raise Conflict("The reply to a memory command cannot be regenerated.")
        # K-47d: the reply stays as it is until its replacement is complete (_finalize supersedes it then), so a
        # regeneration that fails or is stopped leaves the answer the user already had.
        # The question this answer is for: left out of today's count, as when it was first sent.
        asked_at = await session.scalar(
            select(Message.created_at)
            .where(
                Message.conversation_id == conversation.id,
                Message.role == "user",
                Message.created_at <= last.created_at,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        student = await self._student(session, user, asked_at)
        tests = await self._tests(session, conversation.id, exclude=last.id)
        asked = list(
            await session.scalars(  # the question this answer is for, and the one before it
                select(Message.content)
                .where(Message.conversation_id == conversation.id, Message.role == "user")
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(2)
            )
        )
        question = asked[0] if asked else ""
        intent = self._reused_intent(last, question)
        applied = intent["id"] if intent else None
        scope = self._reused_scope(last)
        bank = await self._bank(session, question, student, conversation.id, asked=applied in BANK_INTENTS or None)
        if intent is not None:
            intent["bankMenu"] = _bank_menu(bank, question, applied)
        assistant, usage = await self._add_placeholder(session, user.id, conversation.id)
        await repo.touch(conversation.id)
        await session.commit()
        await session.refresh(conversation)
        nhprep = (last.metadata_ or {}).get("nhprep")
        return PreparedTurn(
            user,
            conversation,
            None,
            assistant.id,
            self._config(
                user,
                conversation,
                student,
                tests,
                bank,
                applied,
                search=is_meaningful(question) or len(asked) > 1,
                scope=_decision(scope),
            ),
            replaces_message_id=last.id,
            files=await files.conversation_files(session, conversation.id, user.id),
            student=student,
            tests=tests,
            bank=bank,
            intent=intent,
            scope=scope,
            usage_id=usage.id,
            nhprep=nhprep if isinstance(nhprep, dict) else None,
        )

    def _reused_intent(self, last: Message, question: str) -> dict | None:
        """A regenerated reply's intent (INTENT_ROUTER): hint_only when its question explicitly asks only
        for a hint (in every mode, as for a new message), else the intent the replaced reply was given. Never
        a new classification."""
        if explicit_hint_only(question):
            return HINT_ONLY.record(True, "explicit")
        if self.settings.intent_router == "off":
            return None
        applied = self.settings.intent_router == "on"
        stored = (last.metadata_ or {}).get("intent") or {}
        intent = stored.get("id")
        if not isinstance(intent, str) or intent not in INTENTS:
            return None
        return Routed(intent, intent, INTENTS[intent].group).record(applied, "regenerate")

    def _reused_scope(self, last: Message) -> dict | None:
        """A regenerated reply's study scope (STUDY_SCOPE): the decision the replaced reply was given, so
        the same message is decided the same way. Never a new reading."""
        if self.settings.study_scope == "off":
            return None
        found = stored_scope(last.metadata_)
        return found.record(self.settings.study_scope == "on") if found else None

    # ----------------------------------------------------------------- generate
    def start(self, turn: PreparedTurn) -> Generation:
        generation = Generation(message_id=turn.assistant_id, queue=asyncio.Queue())
        self.generations.add(generation)
        generation.task = asyncio.create_task(self._generate(turn, generation))
        return generation

    async def _conversation(
        self, session: AsyncSession, conversation_id: uuid.UUID, *, hide_nhprep: bool = False
    ) -> list[ChatMessage]:
        """The last CHAT_HISTORY_MAX_MESSAGES usable messages, oldest first.

        No size trimming here: the graph's context budget (app/rag/context.py) decides
        what fits, so there is exactly one owner of the prompt size.
        """
        rows = list(
            await session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.role.in_(("user", "assistant")),
                    Message.status.in_(("complete", "cancelled", "error")),
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(self.settings.chat_history_max_messages)
            )
        )
        return _history(list(reversed(rows)), hide_nhprep=hide_nhprep)

    async def _cancel_requested(self, message_id: uuid.UUID) -> bool:
        """Stopped elsewhere, or gone with its deleted chat (MISS-chat-3). Bounded (MISS-chat-2): a database that
        does not answer must not hang the reply; that poll counts as "not cancelled" and the next one asks again."""
        try:
            async with asyncio.timeout(max(1.0, self.settings.stream_cancel_poll_s * 5)):
                async with self.db.sessionmaker() as session:
                    status = await session.scalar(select(Message.status).where(Message.id == message_id))
            return status is None or status == "cancelled"
        except Exception:  # noqa: BLE001 - a failed or slow poll must not stop a healthy stream
            return False

    async def _command_cancelled(self, generation: Generation) -> bool:
        return generation.cancel.is_set() or await self._cancel_requested(generation.message_id)

    async def _wait(self, awaitable, generation: Generation):
        """Await `awaitable`, but return (False, None) if the generation is cancelled first.

        The in-process event reacts at once. The database flag (set by a cancel request
        that reached another instance) is re-checked at most every `stream_cancel_poll_s`.
        """
        now = time.monotonic()
        if now - generation.last_db_check >= self.settings.stream_cancel_poll_s:
            generation.last_db_check = now
            if await self._cancel_requested(generation.message_id):
                generation.cancel.set()
        if generation.cancel.is_set():
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            return False, None
        task = asyncio.ensure_future(awaitable)
        while True:
            waiter = asyncio.ensure_future(generation.cancel.wait())
            done, _ = await asyncio.wait(
                {task, waiter}, timeout=self.settings.stream_cancel_poll_s, return_when=asyncio.FIRST_COMPLETED
            )
            waiter.cancel()
            if task in done:
                return True, task.result()
            if generation.cancel.is_set() or await self._cancel_requested(generation.message_id):
                generation.cancel.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return False, None

    async def _generate(self, turn: PreparedTurn, generation: Generation) -> None:
        try:
            if turn.command_replay is not None:
                await generation.queue.put(ChatEvent("token", {"text": turn.command_replay.content}))
                async with self.db.sessionmaker() as session:
                    message = await session.get(Message, turn.command_replay.id)
                    conversation = await session.get(Conversation, turn.conversation.id)
                await generation.queue.put(
                    ChatEvent(
                        "done",
                        {
                            "message": message_out(message).model_dump(mode="json", by_alias=True),
                            "conversation": conversation_out(conversation).model_dump(mode="json", by_alias=True),
                            "replayed": True,
                        },
                    )
                )
            elif (
                turn.command is not None
                and turn.user_message is not None
                and turn.client_request_id is not None
                and turn.command_receipt_id is not None
            ):
                if generation.cancel.is_set():
                    async with self.db.sessionmaker() as session:
                        assistant = await session.get(Message, turn.assistant_id)
                        receipt = await session.get(MemoryCommandReceipt, turn.command_receipt_id)
                        assistant.status = "cancelled"
                        receipt.status = "failed"
                        receipt.completed_at = now()
                        await self._close_command_usage(session, turn.assistant_id, "cancelled")
                    return
                try:
                    async with self.db.sessionmaker() as session:
                        conversation = await session.get(Conversation, turn.conversation.id)
                        source = await session.get(Message, turn.user_message.id)
                        assistant = await session.get(Message, turn.assistant_id)
                        user = await session.get(User, turn.user.id)
                        text = await apply_memory_command(
                            session,
                            self.settings,
                            self.memory.embeddings if self.memory is not None else self.rag_graph.embeddings,
                            user,
                            source,
                            assistant,
                            turn.command,
                            turn.command_receipt_id,
                            cancelled=lambda: self._command_cancelled(generation),
                        )
                        await self._close_command_usage(session, turn.assistant_id, "complete" if text else "cancelled")
                        if not text:
                            return
                        message = await session.get(Message, turn.assistant_id)
                        conversation = await session.get(Conversation, turn.conversation.id)
                except (ValidationFailed, Conflict, IntegrityError) as error:
                    # The memory policy, a length limit, a duplicate or a race refused it: the turn still ends, as a
                    # complete reply giving the memory layer's fixed reason (never the user's text). An outage
                    # (ServiceUnavailable, the database) is not a refusal: it ends with an error event below.
                    log.info(
                        "memory command refused",
                        extra={"error_type": type(error).__name__, "message_id": str(turn.assistant_id)},
                    )
                    text = command_refusal(turn.command.kind, error)
                    try:
                        ended = await self._end_command(turn, text)
                    except Exception as end_error:  # noqa: BLE001 - the database failed too: still end the stream
                        self._log_unended_command(turn, end_error)
                        await self._command_error(generation, turn)
                        return
                    if ended is None:
                        return
                    message, conversation = ended
                except Exception as error:  # noqa: BLE001 - embeddings or the database failed: end with an error event
                    log.warning(
                        "memory command failed",
                        extra={"error_type": type(error).__name__, "message_id": str(turn.assistant_id)},
                    )
                    try:
                        await self._end_command(turn, None)
                    except Exception as end_error:  # noqa: BLE001 - the stale sweep ends the row later
                        self._log_unended_command(turn, end_error)
                    await self._command_error(generation, turn)
                    return
                await generation.queue.put(ChatEvent("token", {"text": text}))
                await generation.queue.put(
                    ChatEvent(
                        "done",
                        {
                            "message": message_out(message).model_dump(mode="json", by_alias=True),
                            "conversation": conversation_out(conversation).model_dump(mode="json", by_alias=True),
                        },
                    )
                )
            else:
                await self._run(turn, generation)
        finally:
            await generation.queue.put(None)  # end of stream, always
            self.generations.remove(turn.assistant_id)

    @staticmethod
    async def _command_error(generation: Generation, turn: PreparedTurn) -> None:
        event = ChatEvent("error", {**public_error("service_unavailable"), "messageId": str(turn.assistant_id)})
        event.data["requestId"] = request_id_var.get()
        await generation.queue.put(event)

    @staticmethod
    def _log_unended_command(turn: PreparedTurn, error: Exception) -> None:
        log.warning(
            "memory command could not be ended",
            extra={"error_type": type(error).__name__, "message_id": str(turn.assistant_id)},
        )

    @staticmethod
    async def _close_command_usage(session: AsyncSession, message_id: uuid.UUID, outcome: str) -> None:
        """A memory command never calls the model: its usage row (opened by prepare) ends with the turn."""
        await session.execute(
            update(UsageEvent)
            .where(UsageEvent.message_id == message_id, UsageEvent.outcome == "streaming")
            .values(outcome=outcome)
        )
        await session.commit()

    async def _end_command(self, turn: PreparedTurn, refusal: str | None) -> tuple[Message, Conversation] | None:
        """Finish a memory command the memory layer refused (`refusal`, a complete reply) or that failed (None,
        an error). None when the conversation was deleted meanwhile."""
        async with self.db.sessionmaker() as session:
            message = await session.get(Message, turn.assistant_id, with_for_update=True)
            if message is None:
                return None
            receipt = await session.get(MemoryCommandReceipt, turn.command_receipt_id, with_for_update=True)
            if refusal is not None:
                message.content = refusal
                message.status = "complete"
                message.metadata_ = {"memoryCommand": {"kind": turn.command.kind, "refused": True}}
            else:
                message.status = "error"
                message.metadata_ = {**(message.metadata_ or {}), "errorCode": "service_unavailable"}
            if receipt is not None:
                receipt.status = "completed" if refusal is not None else "failed"
                receipt.completed_at = now()
            await self._close_command_usage(session, turn.assistant_id, "complete" if refusal is not None else "error")
            conversation = await session.get(Conversation, turn.conversation.id)
            return message, conversation

    def _classifies(self, turn: PreparedTurn) -> bool:
        """Whether _route will wait for the classifier on this turn (INTENT_ROUTER=on, a new message that
        is not an explicit hint-only request)."""
        return (
            self.settings.intent_router == "on"
            and self.intents is not None
            and turn.user_message is not None
            and not explicit_hint_only(turn.user_message.content)
        )

    def _parallel(self, turn: PreparedTurn) -> bool:
        """Voice turns (VOICE_PARALLEL_ROUTING): the intent router and the search start beside the study scope."""
        return (
            turn.voice
            and self.settings.study_scope == "on"
            and self.settings.voice_feature_allows(self.settings.voice_parallel_routing, turn.user.email)
            and self._classifies(turn)
        )

    async def _plan_first_words(
        self, turn: PreparedTurn, generation: Generation, text: str
    ) -> tuple[bool, asyncio.Task | None]:
        """Voice turns (server_speech): once the reply's first words are known (app/voice/first_speech.py), say
        which and start speaking them, or say none will be. Returns whether it is still undecided, and the speech."""
        found = first_speech.first_speakable(text)
        message_id = str(turn.assistant_id)
        if found is None:
            if not first_speech.settled(text):
                return True, None
            await generation.queue.put(ChatEvent("speech", {"messageId": message_id, "state": "skipped"}))
            return False, None
        chars, words = found
        planned = {"messageId": message_id, "state": "planned", "chars": chars, "text": words}
        await generation.queue.put(ChatEvent("speech", planned))
        return False, asyncio.ensure_future(self._speak_first(turn, generation, chars, words))

    async def _speak_first(self, turn: PreparedTurn, generation: Generation, chars: int, words: str) -> None:
        """The reply's first words as audio, sent with the reply (or `failed`: the web app speaks them itself).
        Counted like the web app's own speech requests: the reply's budget and the user's limits."""
        event = {"messageId": str(turn.assistant_id), "chars": chars, "text": words}
        started = time.monotonic()
        try:
            if self.voice is None or (self.voice_budget and not self.voice_budget.spend(turn.assistant_id, len(words))):
                raise RateLimited("No speech left for this reply.")
            async with self.db.sessionmaker() as session:
                # Only the per-user speech limits are used: no passwords or email here.
                await AuthService(session, self.settings, None, None).throttle_voice(turn.user, "tts")  # type: ignore[arg-type]
            audio, media = await self.voice.synthesize(words, turn.speech_format)
            event |= {"state": "ready", "type": media, "audio": base64.b64encode(audio).decode("ascii")}
            log.info(
                "voice first words",
                extra={
                    "chars": len(words),
                    "format": turn.speech_format,
                    "bytes": len(audio),
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "message_id": str(turn.assistant_id),
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - never fails the reply: the web app speaks these words
            log.info(
                "voice first words failed",
                extra={"error_type": type(error).__name__, "message_id": str(turn.assistant_id)},
            )
            event["state"] = "failed"
        await generation.queue.put(ChatEvent("speech", event))

    async def _route(
        self,
        turn: PreparedTurn,
        conversation: list[ChatMessage],
        generation: Generation,
        reading: asyncio.Task | None = None,
    ) -> tuple[AssistantConfig, dict | None, asyncio.Task | None]:
        """What a new message asks for (INTENT_ROUTER, app/intents): read, then applied to the turn's
        prompt and retrieval (on), or only recorded (shadow: the reading runs beside the reply, which
        never waits for it). A message that explicitly asks only for a hint is hint_only without asking
        the classifier. Returns the turn's config, its messages.metadata.intent, and the shadow reading
        still running. A regenerated reply was routed when it was prepared (_reused_intent). `reading`: the
        classifier's reading, already started (_parallel)."""
        mode = self.settings.intent_router
        if turn.user_message is None:
            return turn.config, turn.intent, None
        question = turn.user_message.content
        if explicit_hint_only(question):  # a fixed rule on the user's own words: in every mode, even off
            routed, source = HINT_ONLY, "explicit"
        elif mode == "off" or self.intents is None:
            return turn.config, None, None
        elif mode == "shadow":
            return turn.config, None, asyncio.ensure_future(self.intents.classify(conversation, turn.attached_kinds))
        else:
            reading = reading if reading is not None else self.intents.classify(conversation, turn.attached_kinds)
            ok, routed = await self._wait(reading, generation)
            if not ok:  # stopped while reading: the graph's first step stops at once too
                return turn.config, None, None
            source = "classifier"
        applied = routed.intent is not None and (mode == "on" or source == "explicit")
        config, bank = await self._apply(turn, routed.intent) if applied else (turn.config, turn.bank)
        record = routed.record(applied, source)
        return config, {**record, "bankMenu": _bank_menu(bank, question, record["id"])}, None

    async def _scope(
        self, turn: PreparedTurn, conversation: list[ChatMessage], generation: Generation
    ) -> tuple[dict | None, asyncio.Task | None]:
        """Is the new message something NetSec AI helps with (STUDY_SCOPE, app/intents/scope.py)? The fixed
        rules first (a hint-only request, small talk, files without a real question), then the classifier:
        applied (on), or only recorded (shadow: read beside the reply, which never waits for it). Returns
        messages.metadata.scope and the shadow reading still running. A regenerated reply's was read when
        it was prepared (_reused_scope)."""
        mode = self.settings.study_scope
        if mode == "off" or turn.user_message is None:
            return turn.scope, None
        question = turn.user_message.content
        if explicit_hint_only(question):
            found = Scope(ALLOW_STUDY, reason="hint_only", source="rule")
        else:
            found = settled(is_meaningful(question), turn.attached_kinds)
        if found is None:
            if self.intents is None:
                return None, None
            if mode == "shadow":
                return None, asyncio.ensure_future(self.intents.scope(conversation, turn.attached_kinds))
            ok, found = await self._wait(self.intents.scope(conversation, turn.attached_kinds), generation)
            if not ok:  # stopped while reading: the turn stops at once
                return None, None
            if found.decision == REDIRECT and turn.tests:  # the chat has a test or an exercise: most likely about it
                found = Scope(ALLOW_STUDY, found.best, found.confidence, "practice", found.source, found.latency_ms)
        return found.record(mode == "on"), None

    async def _expression(
        self, turn: PreparedTurn, generation: Generation, intent: dict | None, decision: str | None
    ) -> None:
        """Voice mode: the face's expression for this reply (app/voice/expression.py), before its first words.
        Only the expression word is sent; a typed turn sends nothing."""
        if turn.voice:
            expression = expression_for(intent, decision, self.settings.intent_min_group_confidence)
            await generation.queue.put(
                ChatEvent("expression", {"messageId": str(turn.assistant_id), "expression": expression})
            )

    async def _apply(self, turn: PreparedTurn, intent: str) -> tuple[AssistantConfig, str | None]:
        """The turn's config with an intent, and the question bank's domains it gives the assistant: a test
        intent gets them even when the message's words did not ask for a test (chat_tests.menu)."""
        bank = turn.bank
        if INTENTS[intent].bank and bank == "":
            try:
                async with self.db.sessionmaker() as session:
                    bank = await self._bank(
                        session, turn.user_message.content, turn.student, turn.conversation.id, asked=True
                    )
            except Exception as error:  # noqa: BLE001 - the chapters as prepared; the turn goes on
                log.warning(
                    "intent bank menu failed",
                    extra={"error_type": type(error).__name__, "message_id": str(turn.assistant_id)},
                )
        # An intent can turn the knowledge search off, never on (small talk that opened the chat).
        search = turn.config.retrieval is not None
        config = self._config(
            turn.user,
            turn.conversation,
            turn.student,
            turn.tests,
            bank,
            intent,
            search=search,
            scope=_decision(turn.scope),
            voice=turn.voice,
        )
        return config, bank

    async def _run(self, turn: PreparedTurn, generation: Generation) -> None:
        started = time.monotonic()
        request_sent: dict = {"at": None}  # the provider marks its first request (app/llm/base.py)
        LLM_REQUEST_AT.set(request_sent)
        pieces: list[str] = []
        ttft_ms: int | None = None
        usage = None
        finish_reason = None
        status = "complete"
        error_code: str | None = None
        provider = model = None
        retrieval_summary: dict | None = None
        intent: dict | None = None
        shadow: asyncio.Task | None = None
        scope: dict | None = None
        scope_shadow: asyncio.Task | None = None
        reading: asyncio.Task | None = None  # the intent router's reading, started beside the scope (_parallel)
        ahead: asyncio.Task | None = None  # the knowledge search, started ahead of the graph
        speaking: asyncio.Task | None = None  # the server's first words (server_speech)
        card: Card | None = None  # NHprep's lesson card (_app_turn)
        planning = turn.server_speech
        options = GenerationOptions(
            model=turn.config.model,
            temperature=turn.config.temperature
            if turn.config.temperature is not None
            else self.settings.llm_temperature,
            max_output_tokens=self.settings.llm_max_output_tokens,
        )
        query = turn.user_message.content if turn.user_message is not None else ""
        try:
            async with self.db.sessionmaker() as session:
                conversation = await self._conversation(
                    session, turn.conversation.id, hide_nhprep=not is_nh_student(turn.user, self.settings)
                )
            if turn.replaces_message_id is not None:  # the replies being regenerated stay until it completes (K-47d)
                while conversation and conversation[-1].role == "assistant":
                    conversation.pop()  # but the model answers the question afresh, without them
            if not query:
                query = next((message.content for message in reversed(conversation) if message.role == "user"), "")
            # A quiz request the bank answers itself, or an NHprep lesson card, is decided first: no recall, classifier
            # or search runs.
            found = await self._app_turn(turn, conversation, query)
            turn.served = found if isinstance(found, bank_turn.Served) else None
            card = found if isinstance(found, Card) else None
            memories = await self._memories(turn.user.id, query) if found is None else []
            # Voice turns (VOICE_PARALLEL_ROUTING): the router's reading and the knowledge search start now, beside the
            # study scope, not after it: the same requests on the same words, so the same decisions (OllaLink answers
            # them one after another anyway). A redirect cancels both; an intent that turns the search off drops it.
            prepared = turn.config.retrieval
            if found is None and self._parallel(turn):
                reading = asyncio.ensure_future(self.intents.classify(conversation, turn.attached_kinds))
                ahead = asyncio.ensure_future(
                    self.rag_graph.prefetch(
                        {
                            "user_id": turn.user.id,
                            "conversation": conversation,
                            "memory_context": self._memory_context(memories),
                            "retrieval": prepared,
                            "files": turn.files,
                        }
                    )
                )
            if found is None:
                scope, scope_shadow = await self._scope(turn, conversation, generation)
                turn.scope = scope
            decision = _decision(scope)
            if turn.served is not None:  # the bank's own reply: stored questions, no model and no search
                ttft_ms = int((time.monotonic() - started) * 1000)
                pieces.append(turn.served.text)
                await generation.queue.put(ChatEvent("token", {"text": turn.served.text}))
            elif card is not None:  # NHprep's card: a fixed line, the lesson's ids in metadata, no model and no search
                ttft_ms = int((time.monotonic() - started) * 1000)
                pieces.append(card.copy)
                await generation.queue.put(ChatEvent("token", {"text": card.copy}))
            elif decision == REDIRECT:  # not learning: the app's own short reply, without the model or a search
                language = dict(turn.student.preferences).get("language") if turn.student else None
                text = redirect_copy(turn.user_message.content if turn.user_message is not None else "", language)
                ttft_ms = int((time.monotonic() - started) * 1000)
                pieces.append(text)
                await self._expression(turn, generation, None, decision)
                await generation.queue.put(ChatEvent("token", {"text": text}))
            else:
                if turn.user_message is not None and decision in INSTRUCTIONS:  # its line, in the prompt
                    turn.config = self._config(
                        turn.user,
                        turn.conversation,
                        turn.student,
                        turn.tests,
                        turn.bank,
                        search=turn.config.retrieval is not None,
                        scope=decision,
                        voice=turn.voice,
                    )
                # While the router reads the message (INTENT_ROUTER=on: two Ollalink requests, about 1.1 s from
                # the server), the knowledge search already runs: an intent can only turn it off, so it is the
                # search the graph would make. The router then adds only what it takes beyond the search.
                if ahead is None and self._classifies(turn):
                    prepared = turn.config.retrieval
                    ahead = asyncio.ensure_future(
                        self.rag_graph.prefetch(
                            {
                                "user_id": turn.user.id,
                                "conversation": conversation,
                                "memory_context": self._memory_context(memories),
                                "retrieval": prepared,
                                "files": turn.files,
                            }
                        )
                    )
                config, intent, shadow = await self._route(turn, conversation, generation, reading)
                await self._expression(turn, generation, intent, decision)
                state = {
                    "user_id": turn.user.id,
                    "conversation": conversation,
                    "system_prompt": config.system_prompt,
                    "memory_context": self._memory_context(memories),
                    "retrieval": config.retrieval,
                    "generation": options,
                    "files": turn.files,
                }
                if ahead is not None:
                    found = None
                    if config.retrieval == prepared:  # else the intent turned the search off: the graph as before
                        try:
                            _, found = await self._wait(ahead, generation)
                        except Exception:  # noqa: BLE001 - never fails a turn: the graph searches itself
                            found = None
                    if found is None:  # stopped (the graph's first step stops too), or not used
                        ahead.cancel()
                    else:
                        state.update(found)
                events = self.rag_graph.astream(state, stream_mode="custom").__aiter__()
                try:
                    while True:
                        ok, event = await self._wait(_next(events), generation)
                        if not ok:
                            status = "cancelled"  # cancelling the pending step cancelled the running node
                            break
                        if event is None:
                            break
                        kind = event.get("event")
                        if kind == "token":
                            if ttft_ms is None:
                                ttft_ms = int((time.monotonic() - started) * 1000)
                            pieces.append(event["text"])
                            await generation.queue.put(ChatEvent("token", {"text": event["text"]}))
                            if planning:
                                planning, speaking = await self._plan_first_words(turn, generation, "".join(pieces))
                        elif kind == "llm_started":
                            provider, model = event["provider"], event["model"]
                        elif kind == "llm_finished":
                            usage, finish_reason = event.get("usage"), event.get("finish_reason")
                        elif kind == "retrieval":
                            retrieval_summary = event["summary"]
                            # Before the first token, so a client can show [1]/[F1] as chips while it streams.
                            citations = public_citations(retrieval_summary)
                            if citations:
                                await generation.queue.put(
                                    ChatEvent(
                                        "citations", {"messageId": str(turn.assistant_id), "citations": citations}
                                    )
                                )
                finally:
                    await events.aclose()
        except LLMError as error:
            status, error_code = "error", error.code
            # The class and code only, never the error's text: a provider's message can echo the input.
            log.warning("llm generation failed", extra={"error_code": error.code, "error_type": type(error).__name__})
        except Exception:  # noqa: BLE001
            status, error_code = "error", "internal_error"
            log.exception("generation crashed")
        if status == "complete" and not "".join(pieces).strip():  # MISS-chat-1: no text is no answer
            status, error_code = "error", "llm_unavailable"
            log.warning(
                "llm returned an empty reply", extra={"provider": provider, "message_id": str(turn.assistant_id)}
            )
        for task in (reading, ahead):  # a redirect, a stop or a failure left them running
            if task is not None and not task.done():
                task.cancel()
        if planning and status != "cancelled":  # the reply ended before its first words were settled
            await generation.queue.put(ChatEvent("speech", {"messageId": str(turn.assistant_id), "state": "skipped"}))
        if speaking is not None:  # its event goes out before the reply's last
            if status == "cancelled":
                speaking.cancel()
            finished, _ = await asyncio.wait({speaking}, timeout=FIRST_SPEECH_WAIT_S)
            if not finished:
                speaking.cancel()
                await generation.queue.put(
                    ChatEvent("speech", {"messageId": str(turn.assistant_id), "state": "failed"})
                )
        if shadow is not None:  # recorded, never applied
            if status == "cancelled":
                shadow.cancel()
            (routed,) = await asyncio.gather(shadow, return_exceptions=True)
            if isinstance(routed, Routed):
                question = turn.user_message.content
                intent = {**routed.record(False, "classifier"), "bankMenu": _bank_menu(turn.bank, question, None)}
        if scope_shadow is not None:  # recorded, never applied
            if status == "cancelled":
                scope_shadow.cancel()
            (found,) = await asyncio.gather(scope_shadow, return_exceptions=True)
            if isinstance(found, Scope):
                scope = found.record(False)

        latency_ms = int((time.monotonic() - started) * 1000)
        content = "".join(pieces)
        metadata = {
            "provider": provider,
            "model": model,
            "latencyMs": latency_ms,
            "ttftMs": ttft_ms,
            "inputTokens": usage.input_tokens if usage else None,
            "outputTokens": usage.output_tokens if usage else None,
            "finishReason": finish_reason,
            "errorCode": error_code,
            # Internal (not in the public allowlist): status, reason, sources in [n] order, timings.
            "retrieval": retrieval_summary,
            # Public: what the reply's [1] and [F1] markers point at (None when nothing is numbered).
            "citations": public_citations(retrieval_summary),
            # Internal: what the intent router read in the message and whether this turn used it.
            "intent": intent,
            # Internal: the study scope's decision (app/intents/scope.py) and whether this turn used it.
            "scope": scope,
            # Internal: question received -> first model request sent (the 1-second budget; scripts/pre_llm_report.py).
            "timing": _pre_llm(turn.received_at, request_sent["at"]),
            # The certification bank's reply (app/chat/bank_turn.py): its topics, questions and timings.
            "bank": turn.served.bank if turn.served is not None else None,
            # Public (NHprep's students only): the lesson card this reply shows, ids only (app/nhprep/chat.py).
            "nhprep": card.public if card is not None else None,
            # Internal: why NHprep answered (status, counts, per-leg ms); never text.
            "nhprepTrace": card.trace if card is not None else None,
        }
        metadata = {k: v for k, v in metadata.items() if v is not None}
        log.info(
            "generation finished",
            extra={
                "status": status,
                "provider": provider,
                "model": model,
                "latency_ms": latency_ms,
                "ttft_ms": ttft_ms,
                "pre_llm_ms": (metadata.get("timing") or {}).get("preLlmMs"),
                "output_tokens": metadata.get("outputTokens"),
                "retrieval_status": (retrieval_summary or {}).get("status"),
                "intent": (intent or {}).get("id"),
                "scope": (scope or {}).get("decision"),
                "message_id": str(turn.assistant_id),
            },
        )
        if (ttft_ms or 0) > SLOW_TTFT_MS or latency_ms > SLOW_TOTAL_MS:
            log.warning(
                "generation slow",
                extra={
                    "status": status,
                    "provider": provider,
                    "latency_ms": latency_ms,
                    "ttft_ms": ttft_ms,
                    "message_id": str(turn.assistant_id),
                },
            )
        if finish_reason in CUT_OFF:  # shown to the user (finishReason); counted here
            log.warning(
                "reply cut off",
                extra={"finish_reason": finish_reason, "provider": provider, "message_id": str(turn.assistant_id)},
            )
        try:
            message, conversation, learning = await self._finalize(
                turn, content, status, metadata, latency_ms, ttft_ms, usage
            )
            if message is None:  # MISS-chat-3: the chat was deleted while its reply streamed; nothing to show
                log.info("reply discarded: its chat was deleted", extra={"message_id": str(turn.assistant_id)})
                return
            if message.status == "error":
                event = ChatEvent(
                    "error", {**public_error(error_code or "internal_error"), "messageId": str(message.id)}
                )
            else:
                done = {
                    "message": message_out(message, nhprep=nhprep_allows(turn.user, self.settings)).model_dump(
                        mode="json", by_alias=True
                    ),
                    "conversation": conversation_out(conversation).model_dump(mode="json", by_alias=True),
                }
                if learning is not None:  # this answer made today a learning day (committed)
                    done["learning"] = {"event": learning.kind, "streak": learning.streak}
                event = ChatEvent("done", done)
        except Exception as error:  # noqa: BLE001 - the database is down; tell the client, keep the row for the sweep
            log.error(
                "could not persist generation",
                extra={"error_type": type(error).__name__, "message_id": str(turn.assistant_id)},
            )
            event = ChatEvent("error", {**public_error("service_unavailable"), "messageId": str(turn.assistant_id)})
        event.data["requestId"] = request_id_var.get()
        await generation.queue.put(event)

    async def _bank(
        self,
        session: AsyncSession,
        question: str | None,
        student: StudentContext,
        conversation_id: uuid.UUID,
        asked: bool | None = None,
    ) -> str | None:
        """What the assistant is told about the question bank: None when tests are the model's own
        (QUESTION_BANK_TESTS off); the domains (chapters) it may set a test on when the question asks for one
        (app/qbank/chat_tests.py; `asked`: the intent router read it as a test request); "" otherwise."""
        if not self.settings.question_bank_tests:
            return None
        earlier = ""
        if student.grade is None and (asked or chat_tests.wants_test(question or "")):  # the class may be named earlier
            said = await session.scalars(
                select(Message.content)
                .where(Message.conversation_id == conversation_id, Message.role == "user")
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(10)
            )
            earlier = "\n".join(reversed(list(said)))
        return await chat_tests.menu(session, question or "", student.grade, student.medium, earlier, asked=asked)

    async def _app_turn(
        self, turn: PreparedTurn, conversation: list[ChatMessage], query: str
    ) -> bank_turn.Served | Card | None:
        """The certification bank's reply to a message that clearly asks for a quiz (QUESTION_BANK_TURN), else
        NHprep's lesson card for one that asks for an NHprep lesson or its study aids (NHPREP_MODE), or None for
        every other turn. Text turns only; a failure leaves the turn to the model."""
        if turn.voice or turn.attached_kinds or turn.command is not None:
            return None
        request = None
        if self.settings.question_bank_tests and self.settings.question_bank_turn_allows(turn.user.email or ""):
            request = bank_turn.wants(query)
        users = [message.content for message in reversed(conversation) if message.role == "user"]
        earlier = users[1:4] if users and users[0] == query else users[:3]
        if request is None:
            # NHprep: a new message, or the regeneration of an NHprep card (its finder runs again).
            if self.nhprep is None or (turn.user_message is None and turn.nhprep is None):
                return None
            return await self.nhprep.answer(turn.user, query, earlier, turn.assistant_id)
        goal = certification_code(turn.student.certification_goal) if turn.student else None
        try:
            async with self.db.sessionmaker() as session:
                served = await bank_turn.answer(
                    session,
                    request,
                    user_id=turn.user.id,
                    conversation_id=turn.conversation.id,
                    reply_id=turn.assistant_id,
                    earlier=earlier,
                    goal=goal,
                    entitled=is_nh_student(turn.user, self.settings),
                )
        except Exception:  # noqa: BLE001 - the bank never fails a turn: the model answers instead
            log.exception("bank turn failed", extra={"message_id": str(turn.assistant_id)})
            return None
        if served is not None:
            log.info(
                "bank turn",
                extra={
                    "message_id": str(turn.assistant_id),
                    "status": served.bank.get("status"),
                    "questions": len(served.question_ids),
                    "find_ms": (served.bank.get("timing") or {}).get("findMs"),
                    "pick_ms": (served.bank.get("timing") or {}).get("pickMs"),
                },
            )
        return served

    async def _with_bank(self, session: AsyncSession, turn: PreparedTurn, metadata: dict) -> dict:
        """The bank test's practice set, opened in the reply's own transaction, and its record (keys inside,
        shown to a client only per question once final)."""
        served = turn.served
        set_id = await bank_tests.open_set(session, turn.user.id, turn.assistant_id, served.batch)
        record = {
            **served.record,
            "source": {
                "kind": "question_bank",
                "netsec": True,
                "keys": True,
                "setId": str(set_id),
                "topicIds": served.bank.get("topicIds") or [],
                "questionIds": list(served.question_ids),
            },
        }
        log.info(
            "assessment created",
            extra={"message_id": str(turn.assistant_id), "questions": record["total"], "source": "question_bank"},
        )
        return {**metadata, "assessments": [record], "bank": {**served.bank, "setId": str(set_id)}}

    async def _with_test(
        self,
        session: AsyncSession,
        turn: PreparedTurn,
        content: str,
        metadata: dict,
        bank: chat_tests.BankTest | None = None,
    ) -> dict:
        """The reply's practice test, checked whole (app/assessments/state.py) and saved by the same
        UPDATE as the reply: a test exists exactly when its reply does, and never in part. A test the
        question bank set is built from its own block, with where its questions came from and its
        practice set (an adaptive one shows its first question and grows a question per answer)."""
        extra = {"message_id": str(turn.assistant_id)}
        if bank is not None:
            records, problem = assessments.build(f"```quiz\n{bank.body}\n```", None)
            if not records:
                log.warning("assessment rejected", extra={**extra, "code": problem, "source": "question_bank"})
                await chat_tests.close(session, turn.user.id, bank.set_id)  # nothing can be answered
                return {**metadata, "assessmentError": {"code": problem}}
            records[0] |= {"total": bank.total, "adaptive": bank.adaptive}  # the hash covers the questions only
            records[0]["source"] = {
                "kind": "question_bank",
                "chapterIds": list(bank.chapter_ids),
                "topicIds": list(bank.topic_ids),
                "questionIds": list(bank.question_ids),
                "setId": str(bank.set_id),
            }
            log.info(
                "assessment created",
                extra={
                    **extra,
                    "questions": records[0]["total"],
                    "source": "question_bank",
                    "chapter_ids": list(bank.chapter_ids),
                    "adaptive": bank.adaptive,
                },
            )
            return {**metadata, "assessments": records}
        if not assessments.has_test(content):
            return metadata
        question = (
            turn.user_message.content
            if turn.user_message is not None
            else await session.scalar(  # a regeneration answers the latest question
                select(Message.content)
                .where(Message.conversation_id == turn.conversation.id, Message.role == "user")
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
        )
        records, problem = assessments.build(content, question)
        if records:
            log.info("assessment created", extra={**extra, "questions": records[0]["total"]})
            return {**metadata, "assessments": records}
        if problem:
            log.warning("assessment rejected", extra={**extra, "code": problem})
            return {**metadata, "assessmentError": {"code": problem}}
        return metadata  # the fence named a test only inside another block: no test

    async def _tests(self, session: AsyncSession, conversation_id: uuid.UUID, exclude: uuid.UUID | None = None) -> str:
        """The chat's practice tests for the assistant, newest first. Complete replies only: a
        regenerated (superseded) reply is gone, and its test with it."""
        query = (
            select(Message.metadata_["assessments"])
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
                Message.status == "complete",
                Message.metadata_.has_key("assessments"),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(5)
        )
        if exclude is not None:
            query = query.where(Message.id != exclude)
        found = await session.scalars(query)
        return assessments.tutor_summary([record for records in found for record in reversed(records or [])])

    async def _finalize(self, turn, content, status, metadata, latency_ms, ttft_ms, usage):
        async with self.db.sessionmaker() as session:
            bank = None
            served = turn.served
            if status == "complete" and served is not None and served.batch is not None:  # the bank's own test
                tested = await self._side_effect(
                    session, "bank_test", turn, lambda: self._with_bank(session, turn, metadata)
                )
                metadata = (
                    tested if tested is not None else {**metadata, "assessmentError": {"code": "bank_unavailable"}}
                )
            elif status == "complete" and served is None:
                if self.settings.question_bank_tests:  # the test's questions come from the bank
                    filled = await self._side_effect(
                        session,
                        "practice_test",
                        turn,
                        lambda: chat_tests.fill(
                            session,
                            content,
                            str(turn.assistant_id),
                            turn.user.id,
                            turn.conversation.id,
                            adaptive=self.settings.adaptive_chat_tests,
                        ),
                    )
                    if filled is not None:
                        content, bank = filled
                tested = await self._side_effect(
                    session, "practice_record", turn, lambda: self._with_test(session, turn, content, metadata, bank)
                )
                metadata = tested if tested is not None else metadata
            # A cancel written by the cancel endpoint wins over "complete".
            final_status = await session.scalar(
                update(Message)
                .where(Message.id == turn.assistant_id)
                .values(
                    content=content,
                    status=case((Message.status == "cancelled", "cancelled"), else_=status),
                    metadata_=metadata,
                )
                .returning(Message.status)
            )
            usage_row = (
                UsageEvent.id == turn.usage_id
                if turn.usage_id is not None
                else UsageEvent.message_id == turn.assistant_id
            )
            if final_status is None:  # MISS-chat-3: the reply's chat was deleted while it streamed
                await session.execute(update(UsageEvent).where(usage_row).values(outcome="cancelled"))
                await session.commit()
                return None, None, None
            if final_status == "complete" and turn.replaces_message_id is not None:
                await self._supersede_replaced(session, turn)  # K-47d: only now that the new reply is complete
            if bank is not None and final_status != "complete":  # the cancel won: its test can't be answered
                await chat_tests.close(session, turn.user.id, bank.set_id)
            if (metadata.get("bank") or {}).get("setId") and final_status != "complete":
                await bank_tests.close(session, turn.user.id, uuid.UUID(metadata["bank"]["setId"]))
            # A fully answered, meaningful question makes its day a learning day (app/progress/learning.py);
            # a message the study scope turned away (STUDY_SCOPE on) never does, and never names the chat.
            learning = None
            turned_away = _decision(metadata.get("scope")) in NOT_LEARNING
            if (
                final_status == "complete"
                and turn.user_message is not None
                and is_meaningful(turn.user_message.content)
                and not turned_away
                and served is None  # asking for a quiz is not learning; its answers are (record_practice)
            ):
                learning = await self._side_effect(
                    session, "learning_day", turn, lambda: record_learning(session, turn.user.id, now(), turn.asked_at)
                )
            if final_status == "complete" and turn.conversation.title == DEFAULT_TITLE and not turned_away:
                await self._name_conversation(session, turn, content)
            await session.execute(
                update(UsageEvent)
                .where(usage_row)
                .values(
                    provider=metadata.get("provider"),
                    model=metadata.get("model"),
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    latency_ms=latency_ms,
                    ttft_ms=ttft_ms,
                    outcome=final_status or status,  # a cancel that won is recorded as cancelled
                )
            )
            if final_status == "complete" and turn.user_message is not None and served is None:
                await self._side_effect(
                    session,
                    "memory_job",
                    turn,
                    lambda: enqueue_memory_extraction(
                        session,
                        self.settings,
                        user_id=turn.user.id,
                        conversation_id=turn.conversation.id,
                        source_message=turn.user_message,
                        assistant_message_id=turn.assistant_id,
                    ),
                )
            await ConversationRepository(session).touch(turn.conversation.id)
            await session.commit()
            message = await session.get(Message, turn.assistant_id)
            conversation = await session.get(Conversation, turn.conversation.id)
            return message, conversation, learning

    @staticmethod
    async def _supersede_replaced(session: AsyncSession, turn: PreparedTurn) -> None:
        """The earlier replies to this reply's question become superseded, and their tests can't be answered."""
        asked_at = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.conversation_id == turn.conversation.id, Message.role == "user"
            )
        )
        query = select(Message.id).where(
            Message.conversation_id == turn.conversation.id,
            Message.role == "assistant",
            Message.id != turn.assistant_id,
            Message.status.in_(("complete", "cancelled", "error")),
        )
        if asked_at is not None:
            query = query.where(Message.created_at > asked_at)
        replaced = list(await session.scalars(query))
        if not replaced:
            return
        # The reply rows first, then their sets: the order an answer locks them in.
        await session.execute(update(Message).where(Message.id.in_(replaced)).values(status="superseded"))
        for message_id in replaced:
            await chat_tests.close_for_message(session, turn.user.id, message_id)

    @staticmethod
    async def _side_effect(session: AsyncSession, name: str, turn: PreparedTurn, operation):
        """One side effect of a saved reply, in its own SAVEPOINT (K-15a): if it fails, it is logged (ids only)
        and undone, and the reply the user has already read is still saved. Returns None on failure."""
        try:
            async with session.begin_nested():
                return await operation()
        except Exception as error:  # noqa: BLE001 - the reply outlives a failed practice test, streak or memory job
            log.error(
                "reply side effect failed",
                extra={"side_effect": name, "error_type": type(error).__name__, "message_id": str(turn.assistant_id)},
            )
            return None

    async def _name_conversation(self, session: AsyncSession, turn: PreparedTurn, reply: str) -> None:
        """Name a "New chat" after its first answered, meaningful question (app/chat/titles.py).

        Compare-and-set on the default title, scoped to the owner: a rename made while the reply
        streamed always wins, and a named conversation is never renamed. A title never fails a turn.
        """
        extra = {"conversation_id": str(turn.conversation.id)}
        try:
            question = (
                turn.user_message.content
                if turn.user_message is not None
                else await session.scalar(  # a regeneration answers the latest question
                    select(Message.content)
                    .where(Message.conversation_id == turn.conversation.id, Message.role == "user")
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(1)
                )
            )
            if not question or not is_meaningful(question):
                log.info("conversation title skipped", extra={**extra, "reason": "small_talk"})
                return
            named = topic_title(question, reply)
            if named is None:
                log.info("conversation title skipped", extra={**extra, "reason": "no_candidate"})
                return
            title, source = named
            async with session.begin_nested():  # a failure here must not undo the reply being saved
                result = await session.execute(
                    update(Conversation)
                    .where(
                        Conversation.id == turn.conversation.id,
                        Conversation.user_id == turn.user.id,
                        Conversation.title == DEFAULT_TITLE,
                    )
                    .values(title=title)
                )
            if result.rowcount:
                log.info("conversation titled", extra={**extra, "source": source, "words": len(title.split())})
        except Exception:  # noqa: BLE001 - the reply is already complete; keep "New chat"
            log.exception("conversation title failed", extra=extra)

    # ------------------------------------------------------------------- cancel
    async def cancel(self, session: AsyncSession, user: User, message_id: uuid.UUID) -> str:
        message = await ConversationRepository(session).get_message(user.id, message_id)
        if message is None or message.role != "assistant":
            raise NotFound("Message not found.")
        if message.status == "streaming":
            await session.execute(
                update(Message)
                .where(Message.id == message_id, Message.status == "streaming")
                .values(status="cancelled")
            )
            await session.commit()
            self.generations.cancel(message_id)
            return "cancelled"
        return message.status


STOPPED_MARK = "\n\n[This reply was stopped before it was finished.]"
NO_REPLY = "[No reply was given to this message.]"


def _history(rows: list[Message], *, hide_nhprep: bool = False) -> list[ChatMessage]:
    """What the model is shown of the earlier turns, oldest first (MISS-chat-5): a stopped reply says it was
    stopped, a failed one leaves no two questions in a row, and of several replies to one question (a
    regeneration) the latest complete one stays. A reply's own fill-in-the-blanks answers stay out of the
    model's view (assessments.for_model). `hide_nhprep`: the reader is not a NetworkersHome student, so an
    NHprep-derived reply is the same neutral line they are shown (conversations/router.py)."""
    history: list[ChatMessage] = []
    last_complete = False  # whether the trailing assistant entry came from a complete reply
    for row in rows:
        if row.role == "user":
            if not row.content:
                continue
            if history and history[-1].role == "user":
                history.append(ChatMessage("assistant", NO_REPLY))
            history.append(ChatMessage("user", row.content))
            continue
        if row.status == "error" or not row.content:
            continue
        content = assessments.for_model(row.content) + (STOPPED_MARK if row.status == "cancelled" else "")
        if hide_nhprep and nhprep_derived(row.metadata_):
            content = HIDDEN_REPLY
        if history and history[-1].role == "assistant":
            if row.status == "complete" or not last_complete:
                history[-1] = ChatMessage("assistant", content)
                last_complete = row.status == "complete"
            continue
        history.append(ChatMessage("assistant", content))
        last_complete = row.status == "complete"
    return history


def _decision(scope: dict | None) -> str | None:
    """The study scope decision a turn applies (messages.metadata.scope); None when off, shadow or failed."""
    return scope["decision"] if scope and scope.get("applied") else None


def _bank_menu(bank: str | None, question: str, intent: str | None) -> str:
    """How the assistant got the question bank's domains on a turn (messages.metadata.intent): the message's
    words asked for a test (regex), an applied test intent asked for them (forced), or it got none."""
    if not bank:
        return "none"
    return "forced" if intent in BANK_INTENTS and not chat_tests.wants_test(question) else "regex"


async def _next(iterator):
    try:
        return await iterator.__anext__()
    except StopAsyncIteration:
        return None


async def collect(generation: Generation) -> tuple[ChatEvent, list[str]]:
    """Consume a generation to the end (non-streaming endpoint)."""
    tokens: list[str] = []
    final = ChatEvent("error", public_error("internal_error"))
    while True:
        event = await generation.queue.get()
        if event is None:
            return final, tokens
        if event.type == "token":
            tokens.append(event.data["text"])
        elif event.type in ("done", "error"):
            final = event
