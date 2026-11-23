"""Configuration, read from the environment (and `.env` in development).

Every setting has a safe development default except secrets. `validate_for_env`
refuses to start a production process with development secrets.
"""

from __future__ import annotations

import os
import zoneinfo
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.models import MEMORY_MAX_CONTENT_CHARS

# Placeholder for development only; validate_for_env() refuses it in production.
DEV_SECRET_KEY = "dev-only-secret-change-me"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- process ----
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "Assistant"
    host: str = "127.0.0.1"
    port: int = 4000
    log_level: str = "INFO"
    # Keys HMACs of one-time email codes. Must be set to a long random value in production.
    secret_key: SecretStr = SecretStr(DEV_SECRET_KEY)
    # Shared with the web app's Better Auth (web.env): it authenticates its calls to
    # /internal/auth/* (app/auth/internal.py). Empty = those routes do not exist (404).
    auth_internal_token: SecretStr = SecretStr("")

    # ---- database (PostgreSQL + pgvector: the single source of truth) ----
    database_url: str = "postgresql+asyncpg://aichat:aichat@127.0.0.1:5432/aichat"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_connect_timeout_s: float = 5.0

    # ---- HTTP ----
    # Comma-separated browser origins. Empty = no CORS (the API is called by the web
    # app's server-side proxy, not by browsers directly).
    allowed_origins: str = ""
    # Only enable when the API is reachable exclusively through a trusted proxy.
    trust_forwarded_for: bool = False
    max_body_bytes: int = 64 * 1024

    # ---- auth ----
    email_code_ttl_minutes: int = 15
    email_code_max_attempts: int = 5
    code_requests_per_email: int = 5  # per auth_window_minutes
    auth_window_minutes: int = 15
    email_change_requests_per_user: int = 3  # per auth_window_minutes
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 64 * 1024
    argon2_parallelism: int = 2

    # ---- profile photo ----
    # The upload is the original file (JPEG/PNG/WebP); the stored photo is a 256 px WebP.
    # The web app's proxy caps uploads at the default (docs/openapi.json `x-max-bytes`), so
    # raising this alone changes nothing for browsers.
    avatar_max_bytes: int = Field(default=5 * 1024 * 1024, ge=64 * 1024, le=20 * 1024 * 1024)
    avatar_uploads_per_user: int = 10  # per auth_window_minutes

    # ---- progress ----
    # Decides which day "today" is for a user who has not chosen a time zone (users.timezone).
    default_timezone: str = "Asia/Kolkata"

    # ---- jobs worker (python -m app.jobs, started every minute by aichat-jobs.timer) ----
    # Read on every run, so a change to api.env applies from the next minute, no restart needed.
    jobs_enabled: bool = False
    # Rollout of emails to students: off (nothing is planned or sent) -> dry_run (planned and
    # recorded as `dry_run`, nothing sent) -> allowlist (sent only to NOTIFICATIONS_ALLOWLIST)
    # -> on. Support forwards to SUPPORT_EMAIL do not depend on this.
    notifications_mode: Literal["off", "dry_run", "allowlist", "on"] = "off"
    notifications_allowlist: str = ""  # comma-separated user ids or email addresses
    notifications_kinds: str = ""  # comma-separated kinds to allow; empty = every kind
    notifications_max_per_run: int = Field(default=50, ge=1, le=1000)
    # The public web address, for links in emails (unsubscribe). Required once emails are sent.
    public_app_url: str = ""
    # Where support requests are forwarded. Empty: requests are only stored.
    support_email: str = ""

    # ---- email ----
    email_backend: Literal["smtp", "ollastack", "memory"] = "smtp"
    email_from: str = "Assistant <no-reply@localhost>"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = False
    smtp_ssl: bool = False
    # Ollastack Agent Mail (EMAIL_BACKEND=ollastack): the mailbox sets the sender; EMAIL_FROM is unused.
    ollastack_base_url: str = "https://login.ollastack.com"
    ollastack_token: SecretStr = SecretStr("")
    ollastack_mailbox_id: str = ""

    # ---- LLM ----
    # "openai_compatible" uses the primary (and optional fallback) provider below.
    # "fake" is a deterministic local provider for tests and offline development.
    llm_provider: Literal["openai_compatible", "fake"] = "openai_compatible"
    llm_primary_name: str = "ollima"
    llm_primary_base_url: str = "https://api.ollima.com/v1"
    llm_primary_api_key: SecretStr = SecretStr("")
    llm_primary_model: str = "gemini-3.1-flash-lite"
    llm_primary_stream_usage: bool = True
    llm_fallback_name: str = "openrouter"
    llm_fallback_base_url: str = "https://openrouter.ai/api/v1"
    llm_fallback_api_key: SecretStr = SecretStr("")
    llm_fallback_model: str = ""  # empty = no fallback provider
    llm_fallback_stream_usage: bool = True
    # Bounds (K-28d): a value outside them is a configuration mistake that would break every reply.
    llm_temperature: float = Field(default=0.7, ge=0, le=2)
    llm_max_output_tokens: int = Field(default=4096, ge=1, le=65_536)
    llm_connect_timeout_s: float = Field(default=10.0, gt=0, le=120)
    llm_first_token_timeout_s: float = Field(default=60.0, gt=0, le=600)  # at most llm_total_timeout_s
    llm_total_timeout_s: float = Field(default=180.0, gt=0, le=3600)
    llm_fake_token_delay_s: float = 0.0
    # Total characters sent to the model per turn (system prompt + retrieved context +
    # history + new message). Characters approximate tokens (~4 chars/token for English;
    # Devanagari costs ~2.6x more tokens per character). See app/rag/context.py.
    llm_max_input_chars: int = 48_000

    # ---- chat ----
    chat_max_message_chars: int = 16_000
    chat_history_max_messages: int = 50
    # 0 refuses every chat (an operator's switch); negative is a mistake.
    chat_generations_per_minute: int = Field(default=20, ge=0, le=10_000)
    chat_generations_per_day: int = Field(default=500, ge=0, le=1_000_000)
    # K-11a: chat off without a deploy (CHAT_ENABLED=false, then restart), and a daily cap for every account
    # together (0 = no cap): the operator's bound on what a leaked account or a burst can spend.
    chat_enabled: bool = True
    chat_generations_global_per_day: int = Field(default=0, ge=0, le=10_000_000)
    # K-07b: replies one account may have generating at once (across its chats), and in this API process.
    chat_live_generations_per_user: int = Field(default=3, ge=1, le=100)
    chat_live_generations_max: int = Field(default=64, ge=1, le=10_000)
    stream_keepalive_s: float = 15.0
    stream_stale_after_s: int = 300
    stream_cancel_poll_s: float = 1.0

    # ---- intent router (app/intents): Ollalink's classification model reads what each new message
    # asks for (one of 46 intents). off: nothing is sent; shadow: read and recorded beside the reply,
    # never applied; on: applied. Rollout off -> shadow -> on (docs/deployment.md). The key is a secret.
    intent_router: Literal["off", "shadow", "on"] = "off"
    ollalink_base_url: str = "https://ai.ollalink.com"
    ollalink_api_key: SecretStr = SecretStr("")
    # The whole reading (both requests); never retried. Then the turn goes on without an intent.
    intent_router_timeout_s: float = Field(default=1.5, gt=0, le=5)
    # An intent is applied only when P(group) and P(group) x P(intent | group) reach these; below the
    # group threshold the second request is not made. scripts/eval_intents.py chooses both (dev split).
    intent_min_confidence: float = Field(default=0.5, ge=0, le=1)
    intent_min_group_confidence: float = Field(default=0.5, ge=0, le=1)
    # ---- study scope (app/intents/scope.py): is the latest message something PadhAI helps with? One more
    # Ollalink request, with the router's key, client and time limit. off: nothing is sent; shadow: read
    # and recorded, never applied; on: unrelated requests are redirected and the tutor keeps to learning.
    study_scope: Literal["off", "shadow", "on"] = "off"
    # P(unrelated) needed to redirect, and P(support | unclear | mixed) needed to apply one: chosen with
    # scripts/eval_scope.py on the dev split (no learning request there above 0.31 for unrelated).
    scope_min_redirect: float = Field(default=0.5, ge=0, le=1)
    scope_min_confidence: float = Field(default=0.5, ge=0, le=1)
    # Who reads the scope: `decision` (app/intents/decision.py: OpenRouter's DeepSeek V4.1 Flash, escalating doubtful
    # readings to GPT-6.2 Sol; masked; the only reader allowed in production) or `ollalink` (the legacy /v1/classify,
    # kept but never evaluated for NetSec). The thresholds are chosen on backend/evals/scope (eval_decision_scope.py).
    study_scope_classifier: Literal["decision", "ollalink"] = "decision"
    decision_base_url: str = "https://openrouter.ai/api/v1"
    # Empty: LLM_FALLBACK_API_KEY when DECISION_BASE_URL is the fallback's URL (the same OpenRouter account).
    decision_api_key: SecretStr = SecretStr("")
    decision_primary_model: str = "deepseek/deepseek-v4.1-flash-1017"
    decision_escalation_model: str = "openai/gpt-6.2-sol"
    decision_primary_timeout_s: float = Field(default=3.0, gt=0, le=20)
    decision_escalation_timeout_s: float = Field(default=6.0, gt=0, le=30)
    # The primary's reading goes to the escalation model when its confidence is below this (or its label could turn
    # the message away, or it failed, or the message tries to steer the classifier).
    decision_escalate_below: float = Field(default=0.85, ge=0, le=1)
    # Under a second (owner, 2026-11-23): the reply waits at most this long for the primary's reading (the knowledge
    # search runs beside it); the escalation model reads in the background, for the streak and the record only.
    decision_wait_s: float = Field(default=0.9, gt=0, le=5)
    # The primary alone turns a message away from this confidence (0 of 457 in-scope eval cases redirected at 0.9).
    decision_redirect_alone: float = Field(default=0.9, ge=0, le=1)
    # While users use the app, one tiny request keeps the primary's connection warm after this many quiet seconds.
    decision_warm_after_s: float = Field(default=45.0, ge=0, le=3600)
    # While students use the app, one small warm-up to Ollalink when nothing was sent for this long, so a
    # message after a quiet spell is not slow (IntentClassifier.nudge). 0: never.
    ollalink_warm_after_s: float = Field(default=45.0, ge=0, le=3600)
    # ---- voice mode (app/voice): the student speaks, OllaLink's speech-to-text hears it, the tutor answers as
    # for a typed message, and OllaLink's text-to-speech reads the answer aloud, sentence by sentence. English
    # only (OllaLink's speech output has no Hindi), for students whose tutor language is English or unset.
    # Rollout off -> qa (only the accounts in VOICE_QA_EMAILS, comma-separated) -> on; every voice route checks
    # it itself. The key is OLLALINK_API_KEY.
    voice_mode: Literal["off", "qa", "on"] = "off"
    voice_qa_emails: str = ""
    voice_provider: Literal["ollalink", "fake"] = "ollalink"
    ollalink_stt_url: str = "https://gpu-transcribe.ollalink.com"
    ollalink_tts_url: str = "https://gpu-tts.ollalink.com"
    voice_tts_voice: str = "hadley"  # English; GET {OLLALINK_TTS_URL}/voices lists them (hadley is its default)
    # Per student, counted in auth_attempts (they hold across processes and restarts).
    voice_transcriptions_per_minute: int = Field(default=20, ge=1, le=600)
    voice_transcriptions_per_day: int = Field(default=400, ge=1, le=100_000)
    voice_speech_per_minute: int = Field(default=120, ge=1, le=6000)
    voice_speech_per_day: int = Field(default=3000, ge=1, le=1_000_000)
    # Calls to OllaLink at once, counted in THIS process (the API runs as one: deploy/systemd/aichat-api.service);
    # with more processes each would allow these. Per student, and in all.
    voice_transcriptions_in_flight_per_user: int = Field(default=1, ge=1, le=8)
    voice_speech_in_flight_per_user: int = Field(default=2, ge=1, le=8)
    voice_transcriptions_in_flight: int = Field(default=8, ge=1, le=256)
    voice_speech_in_flight: int = Field(default=16, ge=1, le=256)
    # Speech for one reply at most (characters, summed over its sentences), also counted in this process.
    voice_speech_chars_per_message: int = Field(default=8_000, ge=600, le=100_000)
    # A short pre-recorded word ("Okay.") the moment the student stops, before the answer (owner's decision,
    # 2026-10-26; the web app's own audio, nothing is sent anywhere). Rolled out like VOICE_MODE, to the same
    # VOICE_QA_EMAILS for `qa`.
    voice_ack: Literal["off", "qa", "on"] = "off"
    # Voice turns, rolled out the same way (latency, measured 2026-10-26). The study scope, the intent router and
    # the knowledge search start together, not scope first: the same requests on the same words, so the same
    # decisions (OllaLink answers them one at a time, so together they take ~1.4 s instead of ~1.9 s).
    voice_parallel_routing: Literal["off", "qa", "on"] = "off"
    # The reply's first words spoken by the server from its tokens and sent with the reply, a round trip sooner
    # than the web app asking for them (app/voice/first_speech.py).
    voice_server_speech: Literal["off", "qa", "on"] = "off"

    # ---- Study time (app/study): how long without input counts as idle, by mode. Reading allows
    # longer (reading and thinking need no clicks); the reading prompt is always shorter than this.
    study_idle_reading_s: int = Field(default=300, ge=60, le=1800)
    study_idle_solving_s: int = Field(default=120, ge=30, le=1800)

    # ---- RAG (LangGraph + LangChain retriever over PostgreSQL/pgvector) ----
    rag_enabled: bool = True
    rag_hybrid: bool = False
    rag_top_k: int = Field(default=5, ge=1, le=1_000)
    rag_dense_candidates: int = Field(default=20, ge=1, le=1_000)
    rag_lexical_candidates: int = Field(default=20, ge=1, le=1_000)
    rag_rrf_k: int = Field(default=60, ge=1, le=1_000)
    # Cosine similarity floor (0..1). Uncalibrated default: tune with scripts/eval_retrieval.py.
    rag_min_score: float = Field(default=0.30, ge=-1, le=1)
    rag_max_context_chars: int = Field(default=8_000, ge=0, le=1_000_000)
    rag_retrieval_timeout_s: float = Field(default=8.0, gt=0, le=60)
    rag_hnsw_ef_search: int = Field(default=100, ge=1, le=1_000)
    # The NCERT Knowledge Service (app/rag/ncert.py): with a URL and a key, the tutor's knowledge
    # source; empty = the pgvector knowledge base. The key is the service's client key (a secret).
    # The timeout is the whole call: live searches took ~1.7 s, p95 2.8 s, at most 3.9 s (2026-10-15..19).
    ncert_kb_base_url: str = ""
    ncert_kb_api_key: SecretStr = SecretStr("")
    ncert_kb_timeout_s: float = Field(default=6.0, gt=0, le=10)
    rag_chunk_size: int = Field(default=1_000, ge=50, le=100_000)
    rag_chunk_overlap: int = Field(default=150, ge=0)  # below rag_chunk_size (checked below)
    rag_max_document_bytes: int = 5 * 1024 * 1024

    # ---- embeddings ----
    # "openai_compatible" calls an OpenAI-compatible /embeddings API.
    # "fake" is a deterministic keyword-hashing embedder for tests and offline development.
    embedding_provider: Literal["openai_compatible", "fake"] = "openai_compatible"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "text-embedding-3-small"
    # Must equal the pgvector column dimension (app/db/models.py EMBEDDING_DIMENSIONS).
    embedding_dimensions: int = 1536
    embedding_batch_size: int = 64
    embedding_timeout_s: float = 20.0

    # ---- explicit personal memory (app/memory) ----
    # User-controlled CRUD is independent from chat recall, which remains off until evaluated.
    memory_enabled: bool = True
    memory_recall_enabled: bool = False
    memory_automatic_proposals: Literal["off", "shadow", "on"] = "off"
    memory_processor_version: str = Field(default="v1", min_length=1, max_length=64)
    memory_embedding_version: str = Field(default="v1", min_length=1, max_length=32)
    memory_deletion_policy_version: str = Field(default="v1", min_length=1, max_length=32)
    memory_max_item_chars: int = Field(default=MEMORY_MAX_CONTENT_CHARS, ge=100, le=MEMORY_MAX_CONTENT_CHARS)
    memory_recall_limit: int = Field(default=4, ge=1, le=10)
    memory_recall_candidates: int = Field(default=12, ge=1, le=50)
    memory_recall_per_kind: int = Field(default=2, ge=1, le=10)
    memory_max_context_chars: int = Field(default=1_200, ge=100, le=8_000)
    # Disabled-by-default recall has a smaller deadline than knowledge retrieval; it is optional
    # personalization and must not add a provider-scale delay to every chat turn.
    memory_recall_timeout_s: float = Field(default=2.0, gt=0, le=10)
    memory_min_score: float = Field(default=0.45, ge=0, le=1)
    # Durable automatic-extraction worker bounds. It receives only the completed user message.
    memory_worker_model: str = ""
    memory_worker_max_input_chars: int = Field(default=4_000, ge=100, le=16_000)
    memory_worker_max_output_chars: int = Field(default=8_000, ge=100, le=32_000)
    memory_worker_max_output_tokens: int = Field(default=1_000, ge=32, le=4_096)
    memory_worker_timeout_s: float = Field(default=20.0, gt=0, le=120)
    memory_worker_max_per_run: int = Field(default=20, ge=1, le=500)
    memory_worker_max_per_user_per_run: int = Field(default=2, ge=1, le=20)
    memory_worker_lease_s: int = Field(default=120, ge=10, le=3_600)
    memory_worker_max_attempts: int = Field(default=4, ge=1, le=10)
    memory_worker_backoff_base_s: int = Field(default=60, ge=1, le=3_600)
    memory_worker_backoff_max_s: int = Field(default=3_600, ge=1, le=86_400)

    # ---- user files: File Library and chat attachments (app/files) ----
    # Off = no uploads and no processing; READY attachments keep answering. Text chat never
    # depends on files, and no files setting can stop the API from starting.
    files_enabled: bool = True
    # Where untrusted files are parsed. "systemd": a sandboxed transient unit (no network, no
    # secrets, own memory limit); "subprocess": a plain child process with an empty environment
    # (development and tests); "auto": systemd in production, subprocess otherwise. Production
    # never parses unsandboxed: with anything but systemd there, files are unavailable.
    files_extractor: Literal["auto", "subprocess", "systemd"] = "auto"

    # ---- tutor ----
    # Teach the model the interactive practice blocks (quizzes, flashcards, graphs, file previews;
    # app/chat/assistant.py INTERACTIVE_BLOCKS). The web app renders them either way. Off until
    # the owner turns it on; it costs prompt characters on every turn.
    tutor_interactive_blocks: bool = False
    # Give the tutor its teaching defaults (app/chat/teaching.py: direct answers when asked, mistakes
    # diagnosed before they are corrected, one hint at a time when stuck); the student's own settings
    # still come first. Off until the owner turns it on; it costs prompt characters on every turn.
    tutor_teaching_policy: bool = False
    # Practice tests in the chat come only from the question bank (app/qbank/chat_tests.py): the
    # model names a chapter, the app sets stored questions, and a practice block the model writes
    # itself is never shown as a test. The owner's decision, 2026-10-16.
    question_bank_tests: bool = True
    # A bank test chooses each next question from the answer before it (app/qbank/chat_tests.py). Off:
    # every question is chosen when the test is set (a fixed-length test), still recorded as practice.
    # A kill switch, no rollback needed (docs/question_bank.md).
    adaptive_chat_tests: bool = True
    # The certification bank turn (app/chat/service.py _app_turn, app/qbank/request.py, finder.py): a message that
    # clearly asks for a quiz is answered with stored questions by the app itself, with no model or embedding call.
    # Rollout off -> qa (only QUESTION_BANK_QA_EMAILS) -> on; off until the bank's content is imported.
    question_bank_turn: Literal["off", "qa", "on"] = "off"
    question_bank_qa_emails: str = ""
    # NetworkersHome students: verified emails at these domains (comma-separated, exact, case-folded) get the
    # NHprep-derived bank and NHprep content (app/access/students.py). Empty: nobody (fails closed).
    nhprep_allowed_email_domains: str = "networkershome.com"
    # NHprep video courses (app/nhprep; plan rev 4 §8.8, Revision 5): lesson cards in the chat and /v1/nhprep, for
    # NetworkersHome students only (app/nhprep/access.py). Rollout off -> qa (only NHPREP_QA_EMAILS) -> on.
    nhprep_mode: Literal["off", "qa", "on"] = "off"
    nhprep_qa_emails: str = ""
    # How a video gets its short-lived link: off (none plays; the card says so), secret (NHprep's signing secret) or
    # endpoint (NHprep signs). Production refuses both until their signer exists (app/nhprep/playback.py).
    nhprep_playback: Literal["off", "secret", "endpoint"] = "off"
    # (A) "secret": NHprep's link-signing secret, written into /etc/netsecai/api.env by the owner only; never logged.
    nhprep_video_signing_secret: SecretStr | None = None
    # One deadline for the whole NHprep finder on a chat turn (its query embedding plus every SQL leg).
    nhprep_find_timeout_s: float = Field(default=1.0, gt=0, le=10)
    # Per user, on every /v1/nhprep route; a playback also counts against its own, lower limit.
    nhprep_requests_per_minute: int = Field(default=600, ge=1, le=10_000)
    nhprep_playbacks_per_minute: int = Field(default=20, ge=1, le=600)

    @field_validator("default_timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            zoneinfo.ZoneInfo(value)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("DEFAULT_TIMEZONE must be an IANA time zone name, e.g. Asia/Kolkata") from error
        return value

    @model_validator(mode="after")
    def _consistent_numbers(self) -> Settings:
        """Pairs that only work together (K-28d, K-28e)."""
        if self.llm_first_token_timeout_s > self.llm_total_timeout_s:
            raise ValueError("LLM_FIRST_TOKEN_TIMEOUT_S must not exceed LLM_TOTAL_TIMEOUT_S")
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP must be smaller than RAG_CHUNK_SIZE")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def ncert_kb_enabled(self) -> bool:
        return bool(self.ncert_kb_base_url and self.ncert_kb_api_key.get_secret_value())

    @property
    def intent_router_enabled(self) -> bool:
        """Whether messages are sent to be classified (an explicit "hint only" needs no classifier)."""
        return self.intent_router != "off" and bool(self.ollalink_api_key.get_secret_value())

    @property
    def decision_key(self) -> str:
        """The decision models' key: DECISION_API_KEY, else the fallback's OpenRouter key when the URLs are the same."""
        own = self.decision_api_key.get_secret_value()
        if own:
            return own
        same = self.decision_base_url.rstrip("/") == self.llm_fallback_base_url.rstrip("/")
        return self.llm_fallback_api_key.get_secret_value() if same else ""

    @property
    def study_scope_enabled(self) -> bool:
        """Whether messages are sent for their study scope (small talk and bare files need no classifier)."""
        if self.study_scope == "off":
            return False
        if self.study_scope_classifier == "decision":
            return bool(self.decision_key)
        return bool(self.ollalink_api_key.get_secret_value())

    @property
    def voice_qa_list(self) -> frozenset[str]:
        return frozenset(email.strip().lower() for email in self.voice_qa_emails.split(",") if email.strip())

    @property
    def question_bank_qa_list(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.question_bank_qa_emails.split(",") if e.strip())

    @property
    def nhprep_qa_list(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.nhprep_qa_emails.split(",") if e.strip())

    def question_bank_turn_allows(self, email: str) -> bool:
        """QUESTION_BANK_TURN for this account: `on` everyone, `qa` only the listed accounts, `off` nobody."""
        if self.question_bank_turn == "on":
            return True
        return self.question_bank_turn == "qa" and email.strip().lower() in self.question_bank_qa_list

    def voice_rollout_allows(self, email: str) -> bool:
        """VOICE_MODE for this account: `on` everyone, `qa` only the listed accounts, `off` nobody."""
        return self.voice_feature_allows(self.voice_mode, email)

    def voice_feature_allows(self, mode: str, email: str) -> bool:
        """A voice rollout setting (off|qa|on) for this account: `qa` is the VOICE_QA_EMAILS accounts."""
        if mode == "on":
            return True
        return mode == "qa" and email.strip().lower() in self.voice_qa_list

    @property
    def files_runner(self) -> Literal["subprocess", "systemd"] | None:
        """How files are parsed, or None when that would be unsafe (production without systemd)."""
        runner = self.files_extractor
        if runner == "auto":
            runner = "systemd" if self.is_production else "subprocess"
        if self.is_production and runner != "systemd":
            return None
        return runner

    @staticmethod
    def _items(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def notification_allowlist(self) -> set[str]:
        return {item.lower() for item in self._items(self.notifications_allowlist)}

    @property
    def notification_kinds(self) -> set[str] | None:
        """None = every kind."""
        return set(self._items(self.notifications_kinds)) or None

    @property
    def public_app_origin(self) -> str | None:
        """PUBLIC_APP_URL as a bare safe origin, or None when unset or invalid."""
        raw = self.public_app_url.strip().rstrip("/")
        if not raw or any(ord(char) < 0x21 or char in '\\"<>`\x7f' for char in raw):
            return None
        try:
            parts = urlsplit(raw)
            _ = parts.port
        except ValueError:
            return None
        local = parts.hostname in ("localhost", "127.0.0.1")
        secure = parts.scheme == "https" or (parts.scheme == "http" and local and not self.is_production)
        if not secure or not parts.hostname or parts.username or parts.password:
            return None
        if parts.path or parts.query or parts.fragment:
            return None
        return f"{parts.scheme}://{parts.netloc}"

    def jobs_problems(self) -> list[str]:
        """What stops the worker from sending emails safely (checked by every run)."""
        problems = []
        if self.notifications_mode != "off" and self.public_app_origin is None:
            problems.append("NOTIFICATIONS_MODE needs a valid PUBLIC_APP_URL (a bare HTTPS origin)")
        if self.notifications_mode == "allowlist" and not self.notification_allowlist:
            problems.append("NOTIFICATIONS_MODE=allowlist needs NOTIFICATIONS_ALLOWLIST")
        return problems

    @staticmethod
    def _provider_url_is_safe(value: str) -> bool:
        try:
            parts = urlsplit(value.strip())
            _ = parts.port
        except ValueError:
            return False
        loopback = parts.hostname in ("localhost", "127.0.0.1", "::1")
        return (
            bool(parts.hostname)
            and not parts.username
            and not parts.password
            and (parts.scheme == "https" or (parts.scheme == "http" and loopback))
        )

    def validate_for_env(self) -> None:
        if not self.is_production:
            return
        problems = []
        if self.secret_key.get_secret_value() in ("", DEV_SECRET_KEY) or len(self.secret_key.get_secret_value()) < 32:
            problems.append("SECRET_KEY must be a random value of at least 32 characters")
        if len(self.auth_internal_token.get_secret_value()) < 32:
            problems.append("AUTH_INTERNAL_TOKEN must be a random value of at least 32 characters")
        if "aichat:aichat@" in self.database_url:
            problems.append("DATABASE_URL uses the development credentials")
        if self.llm_provider == "fake":
            problems.append("LLM_PROVIDER=fake is not allowed in production")
        else:
            if not self.llm_primary_api_key.get_secret_value():
                problems.append("LLM primary provider needs LLM_PRIMARY_API_KEY")
            if not self._provider_url_is_safe(self.llm_primary_base_url):
                problems.append("LLM_PRIMARY_BASE_URL must be HTTPS or explicit loopback")
            if self.llm_fallback_model:
                if not self.llm_fallback_api_key.get_secret_value():
                    problems.append("enabled LLM fallback needs LLM_FALLBACK_API_KEY")
                if not self._provider_url_is_safe(self.llm_fallback_base_url):
                    problems.append("LLM_FALLBACK_BASE_URL must be HTTPS or explicit loopback")
        if self.email_backend not in ("smtp", "ollastack"):
            problems.append("EMAIL_BACKEND must be smtp or ollastack in production")
        if self.email_backend == "ollastack" and not (
            self.ollastack_token.get_secret_value() and self.ollastack_mailbox_id
        ):
            problems.append("EMAIL_BACKEND=ollastack needs OLLASTACK_TOKEN and OLLASTACK_MAILBOX_ID")
        if self.app_name != "NetSec AI":
            problems.append("APP_NAME must be NetSec AI in production")
        if self.public_app_url and self.public_app_origin is None:
            problems.append("PUBLIC_APP_URL must be a bare HTTPS origin")
        if self.rag_enabled and self.embedding_provider == "fake":
            problems.append("EMBEDDING_PROVIDER=fake is not allowed in production while RAG is enabled")
        if self.embedding_provider == "openai_compatible" and (self.rag_enabled or self.memory_recall_enabled):
            if not self.embedding_api_key.get_secret_value():
                problems.append("enabled embeddings need EMBEDDING_API_KEY")
            if not self._provider_url_is_safe(self.embedding_base_url):
                problems.append("EMBEDDING_BASE_URL must be HTTPS or explicit loopback")
        if self.memory_recall_enabled and not self.memory_enabled:
            problems.append("MEMORY_RECALL_ENABLED requires MEMORY_ENABLED")
        if self.memory_automatic_proposals != "off" and not self.memory_enabled:
            problems.append("MEMORY_AUTOMATIC_PROPOSALS requires MEMORY_ENABLED")
        if self.memory_worker_backoff_max_s < self.memory_worker_backoff_base_s:
            problems.append("MEMORY_WORKER_BACKOFF_MAX_S must be at least MEMORY_WORKER_BACKOFF_BASE_S")
        if self.memory_recall_enabled and self.embedding_provider == "fake":
            problems.append("EMBEDDING_PROVIDER=fake is not allowed in production while memory recall is enabled")
        if self.ncert_kb_base_url or self.ncert_kb_api_key.get_secret_value():
            problems.append("NCERT_KB_* is PadhAI-only and must be unset in NetSec AI production")
        if self.intent_router == "on":
            problems.append("INTENT_ROUTER=on is not allowed in production; use off or shadow")
        if self.study_scope != "off":
            if self.study_scope_classifier != "decision":
                problems.append(
                    "STUDY_SCOPE in production needs STUDY_SCOPE_CLASSIFIER=decision (Ollalink is not evaluated)"
                )
            if not self.decision_key:
                problems.append("STUDY_SCOPE needs DECISION_API_KEY (or the fallback's OpenRouter key)")
            if not self._provider_url_is_safe(self.decision_base_url):
                problems.append("DECISION_BASE_URL must be HTTPS or explicit loopback")
            if not (self.decision_primary_model.strip() and self.decision_escalation_model.strip()):
                problems.append("STUDY_SCOPE needs DECISION_PRIMARY_MODEL and DECISION_ESCALATION_MODEL")
        if self.intent_router != "off" and not self.ollalink_api_key.get_secret_value():
            problems.append("INTENT_ROUTER needs OLLALINK_API_KEY")
        if not self.ollalink_base_url.startswith("https://"):
            problems.append("OLLALINK_BASE_URL must be https:// (the key travels with every call)")
        if self.voice_provider == "fake":  # whatever VOICE_MODE says
            problems.append("VOICE_PROVIDER=fake is not allowed in production")
        if self.voice_mode != "off" and not self.ollalink_api_key.get_secret_value():
            problems.append("VOICE_MODE needs OLLALINK_API_KEY")
        if self.voice_mode == "qa" and not self.voice_qa_list:
            problems.append("VOICE_MODE=qa needs VOICE_QA_EMAILS")
        if self.question_bank_turn == "qa" and not self.question_bank_qa_list:
            problems.append("QUESTION_BANK_TURN=qa needs QUESTION_BANK_QA_EMAILS")
        if self.nhprep_mode == "qa" and not self.nhprep_qa_list:
            problems.append("NHPREP_MODE=qa needs NHPREP_QA_EMAILS")
        if self.nhprep_playback == "secret" and not (
            self.nhprep_video_signing_secret and self.nhprep_video_signing_secret.get_secret_value().strip()
        ):
            problems.append("NHPREP_PLAYBACK=secret needs NHPREP_VIDEO_SIGNING_SECRET")
        if self.nhprep_playback != "off":  # the company has not chosen how NetSec gets a signature (Revision 5)
            problems.append("NHPREP_PLAYBACK must be off: no video signer exists yet")
        if not (self.ollalink_stt_url.startswith("https://") and self.ollalink_tts_url.startswith("https://")):
            problems.append("OLLALINK_STT_URL and OLLALINK_TTS_URL must be https:// (the key travels with every call)")
        tracing_names = ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING")
        if any(os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"} for name in tracing_names):
            problems.append("LangChain/LangSmith tracing must be disabled in production")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
