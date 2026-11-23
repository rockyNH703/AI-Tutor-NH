# Environment variables

> Checked on 2026-10-29 against `backend/app/settings.py` (then 147 fields; 165 with the question bank's and NHprep's,
> 2026-11-20), `deploy-netsec/env/*.example`,
> `frontend/apps/app/src/env.mjs`, `src/lib/auth/`, `src/lib/server/config.ts`, `src/lib/legal.ts`, the Sentry and
> Playwright configs and `backend/tests/conftest.py`. Every name below exists in that code; none is invented.
> Production's actual values are on the host only and are not described here.

An **environment variable** is a named setting a program reads when it starts. NetSec AI keeps settings out of the
code so the same commit runs on a laptop and in production. Defaults are safe for development, except secrets, which
have none.

## Where each process reads its settings

| Process | Read by | On your machine | In production |
|---|---|---|---|
| API (FastAPI) | `Settings` in `backend/app/settings.py`: the environment, then `.env` in the working directory | `backend/.env` (created from `backend/.env.example` by `bun run setup`) | `/etc/netsecai/api.env` |
| Jobs worker (`python -m app.jobs`) | the same `Settings` | `backend/.env` | `/etc/netsecai/api.env`, read again on every run |
| Migrations, `migration_status.py` | `alembic/env.py`, the script | `DATABASE_URL` | `/etc/netsecai/migration.env` (`MIGRATION_DATABASE_URL`) plus `api.env` |
| Web app (Next.js) | `src/env.mjs` (validated at build), `src/lib/auth/*`, `src/lib/server/config.ts` | `frontend/apps/app/.env.local` | `/etc/netsecai/web.env` |
| PostgreSQL container, backups, grants | `deploy-netsec/compose/docker-compose.yml`, `backup.sh`, `database-grants.sh`, `server/lib.sh` | `docker-compose.dev.yml` (fixed development credentials) | `/etc/netsecai/postgres.env` |
| Tests | `backend/tests/conftest.py`, the Playwright configs | your shell | – |

Templates for production: [`deploy-netsec/env/`](../deploy-netsec/env/) (`api.env.example`, `web.env.example`,
`postgres.env.example`, `migration.env.example`). There is no root `.env`.

## Rules for secrets

- A real value never goes into git. `.env*` files are git-ignored; templates hold placeholders only. Never open or
  print another person's env file, or the root key files (`dummy_acc.txt`, `git.txt`, `google_auth.txt`,
  `ollalink.txt`, `ollastack.txt`, `ollima.txt`, `openrouter.txt`).
- In production, secrets live only in `/etc/netsecai/*.env` (mode 600), which a deploy never touches. To run a
  one-off command with them, load the file the way the service does
  (`systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/netsecai/api.env …`); never `source` it.
- Generate secrets on the machine that uses them (`openssl rand -base64 48`), and never print them. Pass them to
  processes through the environment, never as arguments.
- `NEXT_PUBLIC_*` values are **built into** the web app's JavaScript and reach every browser. Never put a secret in
  one.

## When a change takes effect

Every production change needs the owner's explicit authorization.

| What you changed | When it applies |
|---|---|
| API setting (`api.env`) | the next start of `netsecai-api`. A deploy restarts the API only when `backend/` changed, so a settings-only change needs a restart |
| Jobs worker setting (`JOBS_ENABLED`, `NOTIFICATIONS_*`) | the next run, within a minute: no restart (restart the API too, so Settings shows the same) |
| Web server-only value (`API_BASE_URL`, `BETTER_AUTH_SECRET`, …) | the next start of `netsecai-web` |
| Web `NEXT_PUBLIC_*` value | read at runtime by the server (sign-in), baked into the browser code at **build** time: the next `deploy:netsec` rebuilds the frontend, and `status:netsec` reports it as not current until then |
| `EMBEDDING_MODEL` or `EMBEDDING_DIMENSIONS` | re-ingestion, and for the dimension a migration ([rag.md](rag.md)) |

## Production safety checks

With `APP_ENV=production`, the API refuses to start (`Settings.validate_for_env`) when any of these is true:

- `SECRET_KEY` is the development placeholder or shorter than 32 characters;
- `AUTH_INTERNAL_TOKEN` is shorter than 32 characters;
- `DATABASE_URL` uses the development credentials;
- `LLM_PROVIDER=fake` or `VOICE_PROVIDER=fake`;
- `EMBEDDING_PROVIDER=fake` while RAG or memory recall is enabled;
- `EMAIL_BACKEND` is not `smtp` or `ollastack`, or `ollastack` lacks its token or mailbox;
- `APP_NAME` is not `NetSec AI`;
- `PUBLIC_APP_URL` is set but is not a bare HTTPS origin;
- `MEMORY_RECALL_ENABLED` or `MEMORY_AUTOMATIC_PROPOSALS` is on without `MEMORY_ENABLED`, or
  `MEMORY_WORKER_BACKOFF_MAX_S` is below `MEMORY_WORKER_BACKOFF_BASE_S`;
- `NCERT_KB_BASE_URL` is neither `https://` nor loopback;
- `INTENT_ROUTER` or `VOICE_MODE` is on without `OLLALINK_API_KEY`;
- `STUDY_SCOPE` is not `off` without `STUDY_SCOPE_CLASSIFIER=decision`, a decision key (`DECISION_API_KEY`, or the
  fallback's OpenRouter key), an HTTPS `DECISION_BASE_URL` and both decision model ids;
- `VOICE_MODE=qa` has no `VOICE_QA_EMAILS`;
- an Ollalink URL is not `https://`.

The deploy (`deploy-netsec/server/update.sh`) also refuses unless `web.env` sets `API_BASE_URL=http://127.0.0.1:4400`,
`NEXT_PUBLIC_SITE_URL`, `AUTH_DATABASE_URL`, `BETTER_AUTH_SECRET` and `AUTH_INTERNAL_TOKEN` (equal to `api.env`'s),
and unless the three database URLs target `127.0.0.1:55432/netsecai` with their expected roles. The web app refuses
to sign anyone in without `BETTER_AUTH_SECRET` and `AUTH_INTERNAL_TOKEN` of at least 32 characters, plus
`AUTH_DATABASE_URL`.

## Web app

| Variable | Kind | Default | What it does |
|---|---|---|---|
| `API_BASE_URL` | server-only | `http://127.0.0.1:4000` | Where the API listens. NetSec production: `http://127.0.0.1:4400` (the deploy refuses anything else). Never `NEXT_PUBLIC_`: browsers never call the API |
| `NEXT_PUBLIC_SITE_URL` | public, build (and read at runtime by sign-in) | none (sign-in falls back to `http://127.0.0.1:3000`) | The public origin: page metadata and Better Auth's base URL and trusted origin. In production, the current Quick Tunnel address |
| `PREVIEW_FEATURES` | server-only, read per request | unset | `1` shows preview pages with sample data (Troubleshooter, Summarizer). Never in production |
| `NEXT_PUBLIC_SENTRY_DSN` | public, build | unset | Sentry is bundled only when this is set |
| `NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE` | public, build | unset | Trace sampling, when Sentry is on |
| `NEXT_PUBLIC_TERMS_URL`, `NEXT_PUBLIC_PRIVACY_URL` | public, build | unset | Links to terms and privacy pages (`src/lib/legal.ts`), shown only when set to an http(s) address |
| `NEXT_DIST_DIR` | build | `.next` | Build into another folder (`NEXT_DIST_DIR=.next-verify bun run build`); the deploy builds into `.next-build` |
| `BETTER_AUTH_SECRET` | **secret** | none | Signs the session cookie (at least 32 characters). Rotating it signs everyone out |
| `AUTH_DATABASE_URL` | **secret** | none | Better Auth's connection, as `netsecai_auth` in production (identity tables only) |
| `AUTH_INTERNAL_TOKEN` | **secret** | none | The same value as the API's; authenticates the web app's calls to `/internal/auth/*` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | the secret is **secret** | unset | "Continue with Google" appears only with both. Redirect URI: `https://<app host>/api/auth/callback/google` |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | the secret is **secret** | unset | The adaptive CAPTCHA (Cloudflare Turnstile) is on only with both |
| `AUTH_RATE_LIMIT` | server-only | on | `off` disables Better Auth's own rate limiter (tests) |
| `SKIP_ENV_VALIDATION`, `CI` | build | unset | Skip `env.mjs` validation at build |

## PostgreSQL, migrations and backups (production only)

`/etc/netsecai/postgres.env` (template `deploy-netsec/env/postgres.env.example`) and `migration.env`. The deploy and
install scripts refuse other values for the fixed names.

| Variable | What it does |
|---|---|
| `POSTGRES_USER`, `POSTGRES_DB` | The database owner and database: must be `netsecai_migrator` and `netsecai` |
| `POSTGRES_PASSWORD` | **Secret.** The owner role's password |
| `POSTGRES_HOST_PORT` | Loopback host port: must be `55432` |
| `POSTGRES_RUNTIME_USER`, `POSTGRES_RUNTIME_PASSWORD` | The API's role, `netsecai_runtime` (**secret** password); `database-grants.sh` creates or repairs it on every deploy |
| `POSTGRES_AUTH_USER`, `POSTGRES_AUTH_PASSWORD` | The web app's sign-in role, `netsecai_auth` (**secret** password), identity tables only |
| `NETSECAI_DB_IDENTITY` | A random 32–128 character marker written once into a brand-new database; every migration, grant, backup and status check verifies it |
| `BACKUP_DIR` | Must be `/var/backups/netsecai` |
| `KEEP_DAYS` | Days of dumps kept (7; `infra-check.sh` fails above 7) |
| `MIGRATION_DATABASE_URL` (`migration.env`) | **Secret.** The owner role's connection, loaded only by the deploy's migration and status commands (`alembic/env.py`, `scripts/migration_status.py`) |

## Tests

| Variable | Read by | Default | What it does |
|---|---|---|---|
| `TEST_DATABASE_URL` | `backend/tests/conftest.py` | `postgresql+asyncpg://aichat:aichat@127.0.0.1:5432/aichat_test` | The backend suite's database. Its name must end in `_test`: the harness drops, recreates and migrates it. On the owner's PC, a database in the WSL PostgreSQL cluster ([testing.md](testing.md)) |
| `E2E_API_URL` | e2e (`e2e/env.ts`) | `http://127.0.0.1:4100` | The API the suite uses; must be loopback |
| `E2E_MAILPIT_URL` | e2e | `http://127.0.0.1:8025` | Where sign-up codes are read; must be loopback |
| `E2E_BACKEND_DIR` | e2e | this repository's `backend/`, if its `.venv` exists | Where to start the API from when none runs on `:4100`; `none` never starts one |
| `E2E_AUTH_DATABASE_URL` | `playwright.config.ts` | `postgresql://aichat:aichat@127.0.0.1:5432/aichat` | The web app's sign-in database during the suite |
| `E2E_SKIP_BUILD`, `E2E_REUSE_APP` | e2e | unset | Reuse the last `.next-e2e` build or a running app |
| `E2E_GOOGLE` | `playwright.config.ts`, `auth.e2e.ts` | unset | Turns on Google sign-in with fake client values |
| `E2E_VISUAL`, `E2E_VISUAL_DIR` | `e2e/visual.e2e.ts` | unset | Capture-only screenshots, saved to a folder |
| `PREVIEW_PORT` | `e2e/serve.mjs` | `3101` | Port of the second build, with preview features on |
| `LEARNING_LIVE` | `e2e/learning/video-live.learning.ts` | unset | `1` runs the learning suite's live YouTube check (needs the internet; never gating) |
| `LEARNING_RUN` | `playwright.learning.config.ts` | the start time (UTC) | The run's folder under the evidence folder outside the repository; set once by the runner, inherited by workers |
| `SMOKE_BASE_URL` | the browser smoke (`bun run smoke`) | `https://ayur.namahos.com` (**PadhAI**) | Always set it to NetSec's tunnel URL; never run the smoke without it |
| `SMOKE_EMAIL`, `SMOKE_PASSWORD` | the browser smoke | the first two lines of `dummy_account.txt` | The account the smoke signs in as. Never print them |
| `SMOKE_HEADLESS`, `SMOKE_SHOTS` | the browser smoke | unset | Hide the browser; save screenshots to an absolute folder outside the repository |

**The learning suite** (`playwright.learning.config.ts`, `e2e/learning/env.ts`) reads none of the `E2E_*` URLs.
It sets its own values: `PORT=3210`, `PREVIEW_PORT=3211`, `NEXT_DIST_DIR=.next-learning-e2e`, the API on 4210, the
database `netsec_learning_e2e_test` (loopback and `*_test` only), the fake providers and the e2e-only secrets.
`E2E_SKIP_BUILD=1` reuses its build. The full list is in
[testing.md, "The learning suite"](testing.md#the-learning-suite).

## Deploy commands (workstation)

| Variable | Read by | Default | What it does |
|---|---|---|---|
| `NETSEC_DEPLOY_HOST` | `deploy-netsec/deploy.sh`, `status.sh`, `rollback.sh`, `smoke.sh` | `deploy.sh`: `DEPLOY_HOST`, then `root@10.1.50.94`; the others: none (required) | The host, e.g. `root@10.1.50.94`. `--host` overrides it in `deploy.sh`, `status.sh` and `rollback.sh`; `smoke.sh` reads only this variable |
| `NETSEC_SECURITY_GATE` | `deploy-netsec/deploy.sh` | unset | `skip:<reason>` deploys without a PASS receipt (logged; owner approval only) |
| `DEPLOY_HOST` | `deploy-netsec/deploy.sh` | `root@10.1.50.94` | The deploy's host when neither `--host` nor `NETSEC_DEPLOY_HOST` is given (`deploy.sh:30`: `--host`, then `NETSEC_DEPLOY_HOST`, then `DEPLOY_HOST`, then `root@10.1.50.94`) |

## API settings

All 165 fields of `Settings` (`backend/app/settings.py`), in the file's own order. The name is the field's name in
capitals (`database_url` is `DATABASE_URL`).

**Secret** marks a value that must never be printed or committed. "Declared but not read" means the field exists
and does nothing today.

### Process

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `APP_ENV` | one of `development` \| `test` \| `production` | `development` | `production` turns on the startup safety checks (`validate_for_env`), hides `/docs` and selects the systemd file sandbox. Tests run with `test`. |
| `APP_NAME` | string | `Assistant` | The product name in emails, prompts and the API title. Production refuses anything but `NetSec AI`. |
| `HOST` | string | `127.0.0.1` | Declared but not read: the address comes from uvicorn's `--host` (`scripts/run.mjs`, `deploy-netsec/systemd/netsecai-api.service`). |
| `PORT` | integer | `4000` | Declared but not read: the port comes from uvicorn's `--port` (4000 in development, 4400 in NetSec production). |
| `LOG_LEVEL` | string | `INFO` | Level of the JSON logs (`app/logging.py`), for the API and the jobs worker. |
| `SECRET_KEY` | string | `dev-only-secret-change-me` (refused in production) | **Secret.** Keys HMACs of one-time email codes. Must be set to a long random value in production. |
| `AUTH_INTERNAL_TOKEN` | string | empty | **Secret.** Shared with the web app's Better Auth (web.env): it authenticates its calls to /internal/auth/* (app/auth/internal.py). Empty = those routes do not exist (404). |

### Database

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `DATABASE_URL` | string | `postgresql+asyncpg://aichat:aichat@127.0.0.1:5432/aichat` | The PostgreSQL connection (asyncpg driver), the single source of truth. Production refuses the development credentials. |
| `DB_POOL_SIZE` | integer | `5` | SQLAlchemy connection pool size (`app/db/session.py`). |
| `DB_MAX_OVERFLOW` | integer | `5` | Extra connections allowed above the pool size. |
| `DB_CONNECT_TIMEOUT_S` | number | `5.0` | Seconds to wait for a database connection. |

### HTTP

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `ALLOWED_ORIGINS` | string | empty | Comma-separated browser origins. Empty = no CORS (the API is called by the web app's server-side proxy, not by browsers directly). |
| `TRUST_FORWARDED_FOR` | true/false | `False` | Only enable when the API is reachable exclusively through a trusted proxy. |
| `MAX_BODY_BYTES` | integer | `64 * 1024` | Largest JSON request body (`app/middleware.py`); the photo, file and voice uploads have their own limits. |

### Accounts and throttling

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `EMAIL_CODE_TTL_MINUTES` | integer | `15` | Lifetime of an email-change code issued by the API. |
| `EMAIL_CODE_MAX_ATTEMPTS` | integer | `5` | Wrong guesses allowed per email-change code. |
| `CODE_REQUESTS_PER_EMAIL` | integer | `5` | per auth_window_minutes |
| `AUTH_WINDOW_MINUTES` | integer | `15` | The window the per-account auth limits count in (`app/auth/service.py`). |
| `EMAIL_CHANGE_REQUESTS_PER_USER` | integer | `3` | per auth_window_minutes |
| `ARGON2_TIME_COST` | integer | `3` | Argon2id time cost for the password checks the API does (re-authentication). |
| `ARGON2_MEMORY_KIB` | integer | `64 * 1024` | Argon2id memory cost (KiB). |
| `ARGON2_PARALLELISM` | integer | `2` | Argon2id parallelism. |

### Profile photo

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `AVATAR_MAX_BYTES` | integer (ge 64 * 1024, le 20 * 1024 * 1024) | `5 * 1024 * 1024` | The upload is the original file (JPEG/PNG/WebP); the stored photo is a 256 px WebP. The web app's proxy caps uploads at the default (docs/openapi.json `x-max-bytes`), so raising this alone changes nothing for browsers. |
| `AVATAR_UPLOADS_PER_USER` | integer | `10` | per auth_window_minutes |

### Progress

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `DEFAULT_TIMEZONE` | string | `Asia/Kolkata` | Decides which day "today" is for a user who has not chosen a time zone (users.timezone). |

### Jobs worker and email notifications

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `JOBS_ENABLED` | true/false | `False` | (python -m app.jobs, started every minute by netsecai-jobs.timer once enabled) Read on every run, so a change to api.env applies from the next minute, no restart needed. |
| `NOTIFICATIONS_MODE` | one of `off` \| `dry_run` \| `allowlist` \| `on` | `off` | Rollout of emails to users: off (nothing is planned or sent) -> dry_run (planned and recorded as `dry_run`, nothing sent) -> allowlist (sent only to NOTIFICATIONS_ALLOWLIST) -> on. Support forwards to SUPPORT_EMAIL do not depend on this. |
| `NOTIFICATIONS_ALLOWLIST` | string | empty | comma-separated user ids or email addresses |
| `NOTIFICATIONS_KINDS` | string | empty | comma-separated kinds to allow; empty = every kind |
| `NOTIFICATIONS_MAX_PER_RUN` | integer (ge 1, le 1000) | `50` | Most emails the jobs worker sends in one run. |
| `PUBLIC_APP_URL` | string | empty | The public web address, for links in emails (unsubscribe). Required once emails are sent. |
| `SUPPORT_EMAIL` | string | empty | Where support requests are forwarded. Empty: requests are only stored. |

### Email delivery

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `EMAIL_BACKEND` | one of `smtp` \| `ollastack` \| `memory` | `smtp` | `smtp` (Mailpit locally), `ollastack` (the Agent Mail API) or `memory` (tests only; refused in production). |
| `EMAIL_FROM` | string | `Assistant <no-reply@localhost>` | Sender address for SMTP (unused with `ollastack`, whose mailbox sets the sender). |
| `SMTP_HOST` | string | `127.0.0.1` | SMTP server host. |
| `SMTP_PORT` | integer | `1025` | SMTP server port (Mailpit listens on 1025). |
| `SMTP_USERNAME` | string | empty | SMTP login, if the server needs one. |
| `SMTP_PASSWORD` | string | empty | **Secret.** SMTP password. |
| `SMTP_STARTTLS` | true/false | `False` | Upgrade the SMTP connection with STARTTLS. |
| `SMTP_SSL` | true/false | `False` | Connect to SMTP over TLS from the start. |
| `OLLASTACK_BASE_URL` | string | `https://login.ollastack.com` | Ollastack Agent Mail (EMAIL_BACKEND=ollastack): the mailbox sets the sender; EMAIL_FROM is unused. |
| `OLLASTACK_TOKEN` | string | empty | **Secret.** Token for the Ollastack Agent Mail API. |
| `OLLASTACK_MAILBOX_ID` | string | empty | The Ollastack mailbox that sends the app's email. |

### Language model

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `LLM_PROVIDER` | one of `openai_compatible` \| `fake` | `openai_compatible` | "openai_compatible" uses the primary (and optional fallback) provider below. "fake" is a deterministic local provider for tests and offline development. |
| `LLM_PRIMARY_NAME` | string | `ollima` | Label of the primary provider in logs and usage records. |
| `LLM_PRIMARY_BASE_URL` | string | `https://api.ollima.com/v1` | Base URL of the primary OpenAI-compatible chat-completions API. |
| `LLM_PRIMARY_API_KEY` | string | empty | **Secret.** Key for the primary provider. |
| `LLM_PRIMARY_MODEL` | string | `gemini-3.1-flash-lite` | Model the assistant answers with (also used by `scripts/eval_engineering.py`). |
| `LLM_PRIMARY_STREAM_USAGE` | true/false | `True` | Ask the primary provider to report token usage in the stream. |
| `LLM_FALLBACK_NAME` | string | `openrouter` | Label of the fallback provider. |
| `LLM_FALLBACK_BASE_URL` | string | `https://openrouter.ai/api/v1` | Base URL of the fallback provider. |
| `LLM_FALLBACK_API_KEY` | string | empty | **Secret.** Key for the fallback provider. |
| `LLM_FALLBACK_MODEL` | string | empty | empty = no fallback provider |
| `LLM_FALLBACK_STREAM_USAGE` | true/false | `True` | Ask the fallback provider to report token usage in the stream. |
| `LLM_TEMPERATURE` | number | `0.7` | Sampling temperature for the assistant's replies. |
| `LLM_MAX_OUTPUT_TOKENS` | integer | `4096` | Most tokens one reply may produce. |
| `LLM_CONNECT_TIMEOUT_S` | number | `10.0` | Seconds to connect to a provider. |
| `LLM_FIRST_TOKEN_TIMEOUT_S` | number | `60.0` | Seconds to wait for the first token; past it the turn fails, or the fallback provider is tried. |
| `LLM_TOTAL_TIMEOUT_S` | number | `180.0` | Most seconds one reply may take in total. |
| `LLM_FAKE_TOKEN_DELAY_S` | number | `0.0` | Delay between the fake provider's words (tests and demos only). |
| `LLM_MAX_INPUT_CHARS` | integer | `48000` | Total characters sent to the model per turn (system prompt + retrieved context + history + new message). Characters approximate tokens (~4 chars/token for English; Devanagari costs ~2.6x more tokens per character). See app/rag/context.py. |

### Chat

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `CHAT_MAX_MESSAGE_CHARS` | integer | `16000` | Longest message a user may send. |
| `CHAT_HISTORY_MAX_MESSAGES` | integer | `50` | Most earlier messages considered for the prompt (the character budget also applies). |
| `CHAT_GENERATIONS_PER_MINUTE` | integer | `20` | Per-user reply quota per minute (429 `rate_limited`). 0 to 10,000; 0 refuses every reply. |
| `CHAT_GENERATIONS_PER_DAY` | integer | `500` | Per-user reply quota per day. 0 to 1,000,000. |
| `CHAT_ENABLED` | bool | `true` | `false` switches chat off without a deploy (restart the API): new replies get 503 `service_unavailable`. |
| `CHAT_GENERATIONS_GLOBAL_PER_DAY` | integer | `0` | Replies per day for every account together (429 when reached); `0` = no cap. |
| `CHAT_LIVE_GENERATIONS_PER_USER` | integer | `3` | Replies one account may have generating at once, across its chats (429). 1 to 100. |
| `CHAT_LIVE_GENERATIONS_MAX` | integer | `64` | Replies this API process generates at once; beyond it, 503. |
| `STREAM_KEEPALIVE_S` | number | `15.0` | Seconds of silence before the SSE stream sends a `: keepalive` comment. |
| `STREAM_STALE_AFTER_S` | integer | `300` | A reply still `streaming` after this long is treated as dead and marked interrupted. |
| `STREAM_CANCEL_POLL_S` | number | `1.0` | How often a generation checks the database's stop flag (a stop from another tab or process). |

### JEV: intent router

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `INTENT_ROUTER` | one of `off` \| `shadow` \| `on` | `off` | (app/intents): Ollalink's classification model reads what each new message asks for (one of 46 intents; taxonomy `2026-10-29`, which needs new eval cases before `on`). Message text is sent unmasked. off: nothing is sent; shadow: read and recorded beside the reply, never applied; on: applied. Rollout off -> shadow -> on (docs/deployment.md). The key is a secret. |
| `OLLALINK_BASE_URL` | string | `https://ai.ollalink.com` | Ollalink's classification API (the intent router and the study scope). Must be `https://` in production. |
| `OLLALINK_API_KEY` | string | empty | **Secret.** Ollalink key: the intent router, the study scope and voice mode all need it. |
| `INTENT_ROUTER_TIMEOUT_S` | number (gt 0, le 5) | `1.5` | The whole reading (both requests); never retried. Then the turn goes on without an intent. |
| `INTENT_MIN_CONFIDENCE` | number (ge 0, le 1) | `0.5` | An intent is applied only when P(group) and P(group) x P(intent \| group) reach these; below the group threshold the second request is not made. scripts/eval_intents.py chooses both (dev split). |
| `INTENT_MIN_GROUP_CONFIDENCE` | number (ge 0, le 1) | `0.5` | P(group) needed before the second classification request is made and an intent applied. |

### JEV: study scope filter

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `STUDY_SCOPE` | one of `off` \| `shadow` \| `on` | `off` | (app/intents/scope.py, app/intents/decision.py): is the latest message something NetSec AI helps with, and is answering it genuine study (the streak's rule)? off: nothing is read; shadow: read and recorded, never applied; on: unrelated requests are redirected (both decision models must agree), and only study earns streak credit. |
| `SCOPE_MIN_REDIRECT` | number (ge 0, le 1) | `0.5` | P(unrelated) needed to redirect, and P(support \| unclear \| mixed) needed to apply one: chosen with scripts/eval_scope.py on the dev split (measured on PadhAI's cases; NetSec needs its own). |
| `SCOPE_MIN_CONFIDENCE` | number (ge 0, le 1) | `0.5` | P(support, unclear or mixed) needed to apply that scope decision. |
| `STUDY_SCOPE_CLASSIFIER` | `decision` \| `ollalink` | `decision` | Who reads the scope: the decision models (OpenRouter, masked; the only reader allowed in production) or the legacy Ollalink `/v1/classify` (never evaluated for NetSec). |
| `DECISION_BASE_URL` | string | `https://openrouter.ai/api/v1` | The decision models' OpenAI-compatible API. |
| `DECISION_API_KEY` | string | empty | **Secret.** Empty: `LLM_FALLBACK_API_KEY` when `DECISION_BASE_URL` equals `LLM_FALLBACK_BASE_URL` (the same OpenRouter account). |
| `DECISION_PRIMARY_MODEL` | string | `deepseek/deepseek-v4.1-flash-1017` | Reads every message that is not small talk (owner, 2026-11-23: the pinned snapshot). |
| `DECISION_ESCALATION_MODEL` | string | `openai/gpt-6.2-sol` | Reads again what the primary doubts: a label that could turn the message away (unrelated, mixed, unclear), a confidence below `DECISION_ESCALATE_BELOW`, a failure, or a message that tries to steer the classifier. |
| `DECISION_PRIMARY_TIMEOUT_S` | number (gt 0, le 20) | `3.0` | The primary's deadline (one retry of invalid output fits inside it). |
| `DECISION_ESCALATION_TIMEOUT_S` | number (gt 0, le 30) | `6.0` | The escalation model's deadline. Past it: answered, never redirected, no streak credit. |
| `DECISION_WAIT_S` | number (gt 0, le 5) | `0.9` | The most a reply waits for the primary's reading (the knowledge search runs beside it). Slower: the reply goes on with the policy and no decision; the streak still follows the final reading. |
| `DECISION_REDIRECT_ALONE` | number (ge 0, le 1) | `0.9` | The primary alone turns a message away from this confidence (0 of 457 in-scope eval cases at 0.9); the escalation model then reads it in the background, for the streak and the record. |
| `DECISION_WARM_AFTER_S` | number (ge 0, le 3600) | `45.0` | While users are signed in, one tiny request keeps the primary's connection warm after this many quiet seconds. 0: never. |
| `DECISION_ESCALATE_BELOW` | number (ge 0, le 1) | `0.85` | The primary's confidence below which the escalation model reads the message too; chosen with scripts/eval_decision_scope.py. |
| `OLLALINK_WARM_AFTER_S` | number (ge 0, le 3600) | `45.0` | While users use the app, one small warm-up to Ollalink when nothing was sent for this long, so a message after a quiet spell is not slow (IntentClassifier.nudge). 0: never. |

### Voice mode

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `VOICE_MODE` | one of `off` \| `qa` \| `on` | `off` | (app/voice): the user speaks, OllaLink's speech-to-text hears it, the assistant answers as for a typed message, and OllaLink's text-to-speech reads the answer aloud, sentence by sentence. English only (OllaLink's speech output has no Hindi), for users whose answer language is English or unset. Rollout off -> qa (only the accounts in VOICE_QA_EMAILS, comma-separated) -> on; every voice route checks it itself. The key is OLLALINK_API_KEY. |
| `VOICE_QA_EMAILS` | string | empty | Comma-separated accounts voice mode is offered to while `VOICE_MODE=qa` (read through `voice_rollout_allows`). |
| `VOICE_PROVIDER` | one of `ollalink` \| `fake` | `ollalink` | `ollalink`, or `fake` for tests (refused in production). |
| `OLLALINK_STT_URL` | string | `https://gpu-transcribe.ollalink.com` | OllaLink speech-to-text service (must be `https://` in production). |
| `OLLALINK_TTS_URL` | string | `https://gpu-tts.ollalink.com` | OllaLink text-to-speech service (must be `https://` in production). |
| `VOICE_TTS_VOICE` | string | `hadley` | English; GET {OLLALINK_TTS_URL}/voices lists them (hadley is its default) |
| `VOICE_TRANSCRIPTIONS_PER_MINUTE` | integer (ge 1, le 600) | `20` | Per user, counted in auth_attempts (they hold across processes and restarts). |
| `VOICE_TRANSCRIPTIONS_PER_DAY` | integer (ge 1, le 100000) | `400` | Per-user transcriptions per day. |
| `VOICE_SPEECH_PER_MINUTE` | integer (ge 1, le 6000) | `120` | Per-user speech requests per minute. |
| `VOICE_SPEECH_PER_DAY` | integer (ge 1, le 1000000) | `3000` | Per-user speech requests per day. |
| `VOICE_TRANSCRIPTIONS_IN_FLIGHT_PER_USER` | integer (ge 1, le 8) | `1` | Calls to OllaLink at once, counted in THIS process (the API runs as one: deploy-netsec/systemd/netsecai-api.service); with more processes each would allow these. Per user, and in all. |
| `VOICE_SPEECH_IN_FLIGHT_PER_USER` | integer (ge 1, le 8) | `2` | Speech requests one user may have running at once. |
| `VOICE_TRANSCRIPTIONS_IN_FLIGHT` | integer (ge 1, le 256) | `8` | Transcriptions running at once, all users together, in this process. |
| `VOICE_SPEECH_IN_FLIGHT` | integer (ge 1, le 256) | `16` | Speech requests running at once, all users together, in this process. |
| `VOICE_SPEECH_CHARS_PER_MESSAGE` | integer (ge 600, le 100000) | `8000` | Speech for one reply at most (characters, summed over its sentences), also counted in this process. |
| `VOICE_ACK` | one of `off` \| `qa` \| `on` | `off` | A short pre-recorded word the moment the user stops, before the answer (the web app's own audio; nothing is sent). Rolled out like `VOICE_MODE`, to the same `VOICE_QA_EMAILS` for `qa`. |
| `VOICE_PARALLEL_ROUTING` | one of `off` \| `qa` \| `on` | `off` | On voice turns, the scope filter, the intent router and the knowledge search start together instead of scope first (the same decisions). |
| `VOICE_SERVER_SPEECH` | one of `off` \| `qa` \| `on` | `off` | The server speaks the reply's first words from its tokens and sends them with the reply (`app/voice/first_speech.py`). |

### Lab time (formerly study time)

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `STUDY_IDLE_READING_S` | integer (ge 60, le 1800) | `300` | (app/study, shown as Lab Activity): how long without input counts as idle, by mode. Reading allows longer (reading and thinking need no clicks); the reading prompt is always shorter than this. |
| `STUDY_IDLE_SOLVING_S` | integer (ge 30, le 1800) | `120` | Seconds without input before practice or test time counts as idle. |

### Retrieval (knowledge base; NCERT is PadhAI-only and dormant)

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `RAG_ENABLED` | true/false | `True` | Search the knowledge base (pgvector, or the NCERT service) on each turn; `false` skips it (reason `disabled`). Files attached to a chat are read either way. |
| `RAG_HYBRID` | true/false | `False` | Opt in to PostgreSQL `simple` full-text search fused with dense pgvector results by reciprocal-rank fusion. Both legs use the same user/source/model/metadata filters; lexical failure degrades to dense-only. Leave off until the retrieval benchmark passes. NCERT is unaffected. |
| `RAG_TOP_K` | integer | `5` | Passages retrieved per turn (the NCERT service is asked for three times as many for a student with a curriculum, then ranked). |
| `RAG_DENSE_CANDIDATES` | integer (1..1000) | `20` | Dense candidates considered before hybrid fusion. Ignored when `RAG_HYBRID=false`. |
| `RAG_LEXICAL_CANDIDATES` | integer (1..1000) | `20` | Lexical candidates considered before hybrid fusion. Ignored when `RAG_HYBRID=false`. |
| `RAG_RRF_K` | integer (1..1000) | `60` | Reciprocal-rank-fusion damping constant. Ignored when `RAG_HYBRID=false`. |
| `RAG_MIN_SCORE` | number | `0.3` | Cosine similarity floor (0..1). Uncalibrated default: tune with scripts/eval_retrieval.py. |
| `RAG_MAX_CONTEXT_CHARS` | integer | `8000` | Most characters of retrieved passages put in the prompt (whole passages only). |
| `RAG_RETRIEVAL_TIMEOUT_S` | number | `8.0` | Seconds the knowledge-base search may take before the turn goes on without it. |
| `RAG_HNSW_EF_SEARCH` | integer | `100` | pgvector HNSW `ef_search` for knowledge-base queries. |
| `NCERT_KB_BASE_URL` | string | empty | PadhAI's NCERT Knowledge Service (app/rag/ncert.py): with a URL and a key, it replaces the pgvector knowledge base. Leave empty for NetSec AI (the NetSec template allows only a separately approved, isolated endpoint). The key is the service's client key (a secret). The timeout is the whole call: live searches took ~1.7 s, p95 2.8 s, at most 3.9 s (2026-10-15..19). |
| `NCERT_KB_API_KEY` | string | empty | **Secret.** Client key for the NCERT Knowledge Service. |
| `NCERT_KB_TIMEOUT_S` | number (gt 0, le 10) | `6.0` | Seconds for one NCERT search; slower calls fail with `ncert_timeout`. |
| `RAG_CHUNK_SIZE` | integer | `1000` | Characters per chunk when ingesting documents and reading uploaded files. |
| `RAG_CHUNK_OVERLAP` | integer | `150` | Characters shared by consecutive chunks. |
| `RAG_MAX_DOCUMENT_BYTES` | integer | `5 * 1024 * 1024` | Largest document `scripts/ingest_documents.py` accepts. |

### Embeddings

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `EMBEDDING_PROVIDER` | one of `openai_compatible` \| `fake` | `openai_compatible` | "openai_compatible" calls an OpenAI-compatible /embeddings API. "fake" is a deterministic keyword-hashing embedder for tests and offline development. |
| `EMBEDDING_BASE_URL` | string | `https://api.openai.com/v1` | OpenAI-compatible `/embeddings` API. |
| `EMBEDDING_API_KEY` | string | empty | **Secret.** Key for the embeddings API. Without one, the knowledge-base search is skipped and uploaded files cannot be read. Texts are secret-masked before they are sent (`RedactingEmbeddings`). |
| `EMBEDDING_MODEL` | string | `text-embedding-3-small` | Embedding model. Changing it hides older chunks until they are re-ingested (files: Retry). |
| `EMBEDDING_DIMENSIONS` | integer | `1536` | Must equal the pgvector column dimension (app/db/models.py EMBEDDING_DIMENSIONS). |
| `EMBEDDING_BATCH_SIZE` | integer | `64` | Texts per embeddings request. |
| `EMBEDDING_TIMEOUT_S` | number | `20.0` | Seconds per embeddings request. |

### Explicit personal memory

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `MEMORY_ENABLED` | true/false | `True` | The authenticated explicit-memory APIs (create, list, correct, delete, export). User-controlled; independent from recall. |
| `MEMORY_RECALL_ENABLED` | true/false | `False` | Recall relevant active memories into chat. Production refuses it without `MEMORY_ENABLED`, or with fake embeddings. Failures degrade to ordinary chat. |
| `MEMORY_AUTOMATIC_PROPOSALS` | one of `off` \| `shadow` \| `on` | `off` | The automatic-extraction worker that proposes memories from completed user messages. Production refuses it without `MEMORY_ENABLED`. |
| `MEMORY_PROCESSOR_VERSION` | string (1-64 characters) | `v1` | Version label of the extraction processor. |
| `MEMORY_EMBEDDING_VERSION` | string (1-32 characters) | `v1` | Version label of memory embeddings. |
| `MEMORY_DELETION_POLICY_VERSION` | string (1-32 characters) | `v1` | Version label of the deletion policy. |
| `MEMORY_MAX_ITEM_CHARS` | integer (ge 100, le 2000) | `2000` | Maximum normalized characters in one memory (`MEMORY_MAX_CONTENT_CHARS`). |
| `MEMORY_RECALL_LIMIT` | integer (ge 1, le 10) | `4` | Maximum recalled items per turn. |
| `MEMORY_RECALL_CANDIDATES` | integer (ge 1, le 50) | `12` | Maximum user-filtered vector candidates considered before policy limits. |
| `MEMORY_RECALL_PER_KIND` | integer (ge 1, le 10) | `2` | Maximum recalled items of one memory kind. |
| `MEMORY_MAX_CONTEXT_CHARS` | integer (ge 100, le 8000) | `1200` | Maximum memory characters considered for one turn. |
| `MEMORY_RECALL_TIMEOUT_S` | number (gt 0, le 10) | `2.0` | Whole optional recall deadline; a timeout does not fail chat. |
| `MEMORY_MIN_SCORE` | number (ge 0, le 1) | `0.45` | Cosine-similarity floor; tune only with the evaluation set ([memory-evaluation.md](memory-evaluation.md)). |
| `MEMORY_WORKER_MODEL` | string | empty | Model for the extraction worker. |
| `MEMORY_WORKER_MAX_INPUT_CHARS` | integer (ge 100, le 16000) | `4000` | Characters of the completed user message the worker reads. |
| `MEMORY_WORKER_MAX_OUTPUT_CHARS` | integer (ge 100, le 32000) | `8000` | Characters of worker output accepted. |
| `MEMORY_WORKER_MAX_OUTPUT_TOKENS` | integer (ge 32, le 4096) | `1000` | Tokens the worker's model may produce. |
| `MEMORY_WORKER_TIMEOUT_S` | number (gt 0, le 120) | `20.0` | Seconds per worker model call. |
| `MEMORY_WORKER_MAX_PER_RUN` | integer (ge 1, le 500) | `20` | Messages processed per worker run. |
| `MEMORY_WORKER_MAX_PER_USER_PER_RUN` | integer (ge 1, le 20) | `2` | Messages per user per run. |
| `MEMORY_WORKER_LEASE_S` | integer (ge 10, le 3600) | `120` | Lease on a claimed job (seconds). |
| `MEMORY_WORKER_MAX_ATTEMPTS` | integer (ge 1, le 10) | `4` | Attempts before a job fails. |
| `MEMORY_WORKER_BACKOFF_BASE_S` | integer (ge 1, le 3600) | `60` | First retry delay (seconds). |
| `MEMORY_WORKER_BACKOFF_MAX_S` | integer (ge 1, le 86400) | `3600` | Longest retry delay; production refuses a value below the base. |

Memory uses the configured embedding model and dimension. A model change leaves old memories stored but excludes
their vectors from recall until they are recreated or re-embedded.

### File Library

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `FILES_ENABLED` | true/false | `True` | (app/files) Off = no uploads and no processing; READY attachments keep answering. Text chat never depends on files, and no files setting can stop the API from starting. |
| `FILES_EXTRACTOR` | one of `auto` \| `subprocess` \| `systemd` | `auto` | Where untrusted files are parsed. "systemd": a sandboxed transient unit (no network, no secrets, own memory limit); "subprocess": a plain child process with an empty environment (development and tests); "auto": systemd in production, subprocess otherwise. Production never parses unsandboxed: with anything but systemd there, files are unavailable. |

### Assistant behaviour (kept from PadhAI)

| Variable | Type / allowed | Default | What it does |
|---|---|---|---|
| `TUTOR_INTERACTIVE_BLOCKS` | true/false | `False` | Teach the model the interactive blocks (MCQ, quiz, fill-blank, flashcards, graph, diagram, file previews; app/chat/assistant.py INTERACTIVE_BLOCKS, BANK_BLOCKS) and, with the question bank, chat tests. The web app renders them either way. Off until the owner turns it on; it costs prompt characters on every turn. It does not teach the 2026-11-18 blocks (the newer quiz kinds and modes, cheat sheets, video, topology): no prompt teaches those yet. |
| `TUTOR_TEACHING_POLICY` | true/false | `False` | Coaching defaults (app/chat/teaching.py: direct answers when asked, mistakes diagnosed before they are corrected, one hint at a time when stuck); the user's own settings still come first. Off until the owner turns it on; it costs prompt characters on every turn. |
| `QUESTION_BANK_TESTS` | true/false | `True` | Practice tests in the chat come only from the question bank (app/qbank/chat_tests.py; its content is still PadhAI's until the certification bank arrives): the model names a domain (the bank's chapter id), the app sets stored questions, and a practice block the model writes itself is never shown as a test. The owner's decision, 2026-10-16. |
| `ADAPTIVE_CHAT_TESTS` | true/false | `True` | A bank test chooses each next question from the answer before it (app/qbank/chat_tests.py). Off: every question is chosen when the test is set (a fixed-length test), still recorded as practice. A kill switch, no rollback needed (docs/question_bank.md). |
| `QUESTION_BANK_TURN` | off/qa/on | `off` | The certification bank's own chat turn (app/chat/bank_turn.py): a message that clearly asks for a quiz ("quiz me on OSPF", "5 hard MCQs on VLANs", "next 5") is answered by the app with stored questions, with no model, embedding or search call. `qa`: only `QUESTION_BANK_QA_EMAILS`. Off until the bank's content is imported (docs/question_bank.md). |
| `QUESTION_BANK_QA_EMAILS` | comma-separated emails | empty | The accounts `QUESTION_BANK_TURN=qa` serves. `qa` without any is refused in production. |
| `NHPREP_ALLOWED_EMAIL_DOMAINS` | comma-separated domains | `networkershome.com` | NetworkersHome students: a verified email whose domain (ASCII, after the last `@`, exact) is listed gets NHprep-derived bank questions and NHprep content (app/access/students.py). Empty: nobody. |
| `NHPREP_MODE` | off/qa/on | `off` | NHprep video courses (app/nhprep, [nhprep.md](nhprep.md)): lesson cards in the chat and `/v1/nhprep`, for NetworkersHome students only. `off`: nobody (every route 404, stored cards hidden); `qa`: only `NHPREP_QA_EMAILS`; `on`: every student. Read at startup: a change is a restart. |
| `NHPREP_QA_EMAILS` | comma-separated emails | empty | The accounts `NHPREP_MODE=qa` serves (still only NetworkersHome students). `qa` without any is refused in production. |
| `PROGRAMS_MODE` | off/qa/on | `off` | NH programs (app/programs, [api-contract.md](api-contract.md) "NH programs"): an NH invoice number, verified against `nh_invoices`, opens its program (month tutors, QnA, quizzes, mock interviews, videos). `off`: nobody (every route 404, the sidebar and prompt hidden, program chats unreachable); `qa`: only `PROGRAMS_QA_EMAILS`; `on`: everyone. Read at startup: a change is a restart. |
| `PROGRAMS_QA_EMAILS` | comma-separated emails | empty | The accounts `PROGRAMS_MODE=qa` serves. `qa` without any is refused in production. |
| `PROGRAMS_NHPREP_GROUNDING` | off/on | `off` | Month tutors (only program chats) may ground on the month's NHprep notes, flashcards and interview questions (app/nhprep/grounding.py); the text reaches the chat model masked. Egress basis `owner-D5-month-tutors-2026-11-23` (docs/decisions.md). Off: month tutors use the library documents mapped to the month only. |
| `NHPREP_PLAYBACK` | one of `off` \| `secret` \| `endpoint` | `off` | How a lesson video gets its short-lived `videos.nhprep.com` link: `secret` (NHprep's signing secret) or `endpoint` (NHprep signs). Neither signer exists yet, so the API refuses to start with them and production refuses anything but `off`; with `off` every video is `not_configured`. |
| `NHPREP_VIDEO_SIGNING_SECRET` | secret string | empty | Option (A): NHprep's link-signing secret for `NHPREP_PLAYBACK=secret`, written into `/etc/netsecai/api.env` by the owner only; production refuses `secret` without it. Held as a masked secret: never logged, printed or put in argv. |
| `NHPREP_FIND_TIMEOUT_S` | number (gt 0, le 10) | `1.0` | One deadline for the NHprep finder on a chat turn (its query embedding plus every SQL leg); past it the normal turn answers. |
| `NHPREP_REQUESTS_PER_MINUTE` | integer (ge 1, le 10000) | `600` | Per student, on every `/v1/nhprep` route (in process). |
| `NHPREP_PLAYBACKS_PER_MINUTE` | integer (ge 1, le 600) | `20` | Per student, on `POST /v1/nhprep/lessons/{id}/playback`, on top of the route limit. |

## What the production template lists

`deploy-netsec/env/api.env.example` lists 70 of the 165 API settings (every name in it exists in `Settings`); the
others keep their defaults above unless set. Not in the template, among others: the `VOICE_*` limits and rollout
switches (`VOICE_QA_EMAILS`, `VOICE_ACK`, `VOICE_PARALLEL_ROUTING`, `VOICE_SERVER_SPEECH`), `QUESTION_BANK_TESTS`,
`ADAPTIVE_CHAT_TESTS`, `MEMORY_AUTOMATIC_PROPOSALS` and the `MEMORY_WORKER_*` bounds, the `RAG_*` tuning values, and
the timeouts, pool sizes and Argon2 costs whose defaults are intended.

## Related

- [deployment.md](deployment.md): the env files on the host, sign-in setup, the Quick Tunnel, the jobs worker, the
  switches that are owner decisions.
- [rag.md](rag.md): retrieval and embedding settings in context (written for PadhAI).
- [security/README.md](security/README.md): how secrets and keys are handled.
