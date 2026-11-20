# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. It is the
canonical instruction file for every coding agent (`AGENTS.md` points here). Details live in `docs/`; this page links
to them instead of repeating them.

## What this repo is

**NetSec AI**: a chat assistant for network, security and infrastructure engineers (routing and switching,
firewalls and VPNs, cloud, Linux/Windows, SOC and incident response, authorised penetration testing, DevSecOps,
automation). This repository is the NetSec fork of PadhAI, a Class 6–12 school tutor. PadhAI's school features
were **adapted, not deleted** (the owner's rule): practice tests and the question bank became certification
practice (domains and topics), the learning streak a practice streak, the study timer a lab timer, My Learning
Skills & Certifications, Homework Helper the Troubleshooter (preview), the Academic Profile an Engineering
Profile (`/v1/engineering/profile`, `/v1/catalog/engineering`, migration `0020_engineering_profiles`, Settings ›
Engineering Profile; being finished in the working tree on 2026-10-29).

There is **one repository, and each commit is one release** of both halves:

- `backend/`: FastAPI API on PostgreSQL 16 + pgvector (the only datastore). Its own Python 3.12 venv, ruff, pytest.
- `frontend/`: Next.js 15 (React 19) web app with its server-side proxy (BFF). A Bun + Turborepo workspace
  (`apps/app`, `packages/ui`, `tooling/typescript`). `frontend/CLAUDE.md` has the frontend detail.

Remote: `origin` is `https://github.com/rockyNH703/AI-Tutor-NH.git` (private). Push `main`; no pull requests, no
branches. **Work in this one checkout: no separate git worktrees** (owner, 2026-10-29). Other people and agents
edit the same checkout: never commit, revert or reformat their uncommitted work.

Honest state (2026-10-29):
- **The knowledge base is empty and will be provided by the owner.** The pgvector pipeline and
  `backend/scripts/ingest_documents.py` (repeatable `--metadata key=value`, e.g. vendor, platform, version, topic)
  are ready. The NCERT client (`backend/app/rag/ncert.py`) is PadhAI-only and dormant unless `NCERT_KB_*` is set.
- **The certification question bank** (docs/question_bank.md, qbank/README.md):
  - **In production since 2026-11-20, with `QUESTION_BANK_TURN=on`:** a quiz request in the chat, or the composer's
    Quiz me button, is answered from stored questions with no model call.
  - **Content:**
    - 54 books (NHprep's quiz categories plus the CCNA manifest), 713 topics;
    - NHprep's 1,664 quiz questions, 182 of them inactive (disputed keys; no trainer review, owner);
    - about 6,100 agent-written, blind-checked questions from the NHprep lesson notes.
  - **Audience:** NHprep-derived questions are for NetworkersHome students only (`NHPREP_ALLOWED_EMAIL_DOMAINS`).
  - **Host import:** `scripts/import_questions.py ../qbank --apply` through `systemd-run` with api.env
    (docs/deployment.md), after a deploy that carries new `qbank/` content.
- **GitHub Actions do not run** (the account is out of credit). The enforced check is the local gate
  `bun run security`.

## Architecture guardrail: web-first, not web-only

NetSec AI is built for the web today; the platform under it must serve future native and desktop clients without a
rewrite. The web app (with its BFF) is the API's first client, not the architecture. Priorities: correctness,
security, reliability, maintainability, scalability, performance, simplicity.

- **Durable data and rules live in the API** (routers → services/repositories → PostgreSQL). Browser storage is for
  UI state only; preferences belong in `/v1/me/settings` and the profile resources. A client may mirror a rule for
  UX; the API enforces it.
- **APIs model resources, not screens.** `/v1` changes are additive; anything breaking gets a new endpoint or
  version ([docs/api-contract.md](docs/api-contract.md), "Compatibility").
- **Authorization is per user, in the API.** Never rely on a client or on the BFF allowlist.
- **Web-only mechanisms stay at the client layer:** the BFF, cookies, CSP, layout, `PREVIEW_FEATURES`.
- **Don't** build other clients, add cross-platform abstractions, or refactor working code for scalability alone.
- **Enforced by tests:** contract sync (`export_openapi.py --check`, `routes.test.ts`), dependency direction
  (`test_architecture_guards.py`), per-user isolation (`test_authorization.py`). The rest is review.

## Commands

From the repository root (`scripts/run.mjs` is a thin runner over each half's own tools; `package.json` lists them):

```sh
bun run setup | dev | dev:backend | dev:frontend   # dev needs Docker (docker-compose.dev.yml): not on the owner's PC
bun run test          # backend pytest (needs a reachable *_test database) + frontend typecheck/unit tests + bun test deploy-netsec/tests
bun run lint | build | contract                    # contract: regenerate docs/openapi.json + contract.gen.ts, then the contract tests
bun run security      # THE local security gate (scripts/security.sh); a PASS on a clean commit writes the deploy receipt
bun run graph         # code graph (Graphify) into graphify-out/; bun run test:graph tests its boundaries
bun test deploy-netsec/tests                       # the NetSec deploy scripts' tests

# Production (NetSec), only with the owner's explicit authorization:
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run deploy:netsec     # GitHub main -> production; ends "Status: MATCH"
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run status:netsec     # read-only: health, tunnel URL, MATCH/MISMATCH
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run smoke:netsec      # today: runs the same read-only status checks
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run rollback:netsec [-- <commit>]
```

- The host: `deploy:netsec` takes `--host`, then `NETSEC_DEPLOY_HOST`, then `DEPLOY_HOST`, then `root@10.1.50.94`
  (`deploy-netsec/deploy.sh:30`). `status:netsec` and `rollback:netsec` require `--host` or `NETSEC_DEPLOY_HOST` (no
  default); `smoke:netsec` reads only `NETSEC_DEPLOY_HOST` and runs `deploy-netsec/status.sh` with it.
- **`bun run deploy | rollback | status | rehearse | verify` are PadhAI's** (`deploy/`): `deploy/deploy.sh` refuses
  to run from this repository. **`bun run smoke`** (Playwright) defaults to PadhAI's `https://ayur.namahos.com`
  (`playwright.smoke.config.ts`): never run it without `SMOKE_BASE_URL` set to the NetSec URL, and its checks were
  written for PadhAI (not verified against NetSec).
- **Where to install:** never `bun install` at the root; the Bun workspace root is `frontend/`.
- **Backend tests on this PC** run in WSL Ubuntu 24.04 against an isolated PostgreSQL 16 + pgvector cluster, with
  `TEST_DATABASE_URL` pointing at a database whose name ends in `_test` (the harness drops, recreates and migrates
  it, and refuses any other name). How: [docs/testing.md](docs/testing.md). They use a fake LLM, fake embeddings
  and an in-memory mailbox (no Mailpit).
- **Pipes hide failures:** when piping pytest/ruff/bun output, check the exit code separately.
- **Offline dev:** `LLM_PROVIDER=fake`, `EMBEDDING_PROVIDER=fake` (fake embeddings prove plumbing, not quality).
- **Backend scripts** (`backend/scripts/`): `eval_engineering.py` (the real model on synthetic engineering cases,
  pass-rate gates; run on the host after any change to what the model is told), `ingest_documents.py`
  (`--global` or `--owner-email` is mandatory), `eval_retrieval.py`, `eval_intents.py`, `eval_scope.py`,
  `memory_eval.py`, `migration_status.py`, `sandbox_probe.py` (the file sandbox, on the host), `smoke_chat.py`
  (never against production), `import_questions.py` / `qbank_coverage.py` (the question bank),
  `knowledge_admin.py` (Knowledge Library on the host: admins, sources, import batches, review and publish) and
  `import_windows_folder.py` (uploads PDFs through an SSH forward); procedure in
  [docs/knowledge-library-readiness.md](docs/knowledge-library-readiness.md).

## How the halves connect

**Browser → BFF → API.**
- The browser only calls its own origin: `/api/v1/*` and `/api/auth/*`.
- `frontend/apps/app/src/lib/server/proxy-core.ts` forwards the allowlist in `lib/api/routes.ts` to
  `API_BASE_URL` (server-only). It adds `Authorization: Bearer <token>` from Better Auth's httpOnly cookie
  `__Host-padhai_session` (name kept from PadhAI; other Better Auth cookies are `__Host-netsecai.*`), after
  verifying its HMAC signature. It enforces Origin on mutations, a 64 KB JSON body limit, passes SSE through
  unbuffered and aborts upstream when the browser leaves (the API treats that as stop).
- **Sign-in is Better Auth** in the web app (`lib/auth`): email-first sign-up, password and email-code sign-in,
  optional Google, reset, two-step sign-in (TOTP + recovery codes), adaptive Turnstile CAPTCHA, HIBP check. It
  writes the identity tables as the database role `netsecai_auth` and calls the API's loopback `/internal/auth/*`
  with the shared `AUTH_INTERNAL_TOKEN`. The API finds each session by its token's SHA-256 and requires a verified
  address.
- Server components call the API with `lib/server/backend.ts`; an unreachable API shows an outage screen, not a
  sign-out.

Never let the browser call the API, and never hand the token to page code.

**One contract.** `docs/openapi.json` is generated from the backend (`backend/scripts/export_openapi.py`).
`routes.test.ts` fails if a proxied route or request field no longer exists. Change both halves in the same
commit, run `bun run contract`, and add any new browser endpoint to `routes.ts`.

**Backend internals** (details: [docs/backend-architecture.md](docs/backend-architecture.md),
[docs/ai-architecture.md](docs/ai-architecture.md), [docs/rag.md](docs/rag.md); those pages were written for PadhAI
on 2026-10-26 and are not yet re-verified for NetSec):
- **Composition root:** `backend/app/main.py` `create_app(settings, *, llm, email_sender, embeddings, intents,
  voice)`; tests inject fakes there. `Settings.validate_for_env()` refuses unsafe production values (including any
  `APP_NAME` but `NetSec AI`).
- **A chat turn** (`app/chat/service.py`): *prepare* in the request transaction (conversation, 409 if a reply is
  already streaming, quotas, user message + assistant placeholder, the user's context). The prompt is the frozen
  `SYSTEM_PROMPT` (`app/chat/assistant.py`, the engineering assistant), then what `app/learning/tutor.py` adds: the
  Engineering Profile (role, experience, focus areas, platforms, certification goal), six answer preferences, and
  a "Practice so far" section once the user has practised. Nothing set and nothing practised: exactly
  `SYSTEM_PROMPT`. *Generate* is an asyncio task (`app/chat/streams.py`) running the LangGraph graph
  `receive_query → retrieve_context → assemble_context → generate_answer` (`app/rag/graph.py`), emitting SSE
  `meta`/`token`/`done`/`error`. Stop and disconnect cancel it.
- **Secret masking:** `build_llm` and `build_embeddings` wrap every model provider (primary and fallback) and the
  embeddings provider in `RedactingProvider` / `RedactingEmbeddings` (`app/llm/redact.py`): passwords, keys,
  community strings and PSKs in pasted configs become `<redacted>` before a provider sees them. Pattern-based;
  stored messages keep the original; Ollalink requests (classifiers, voice) are **not** masked.
- **One model call per turn:** no graph retries; `FallbackProvider` switches only before the first token.
- **Kept but off by default** (each needs the owner's decision in production): `TUTOR_INTERACTIVE_BLOCKS` (MCQ,
  quiz, fill-blank, flashcards, graph and diagram widgets, and question-bank tests in the chat; the web app renders
  them either way), `TUTOR_TEACHING_POLICY`, `INTENT_ROUTER` and `STUDY_SCOPE` (Ollalink classifiers; the
  2026-10-29 taxonomy needs a new `eval_intents.py` run before `on`), `VOICE_MODE` (English only),
  `MEMORY_RECALL_ENABLED` and `MEMORY_AUTOMATIC_PROPOSALS` (explicit memory APIs are on), the jobs worker.
- **Interactive learning blocks (2026-11-18), dormant in production:** the web app draws them, but nothing teaches
  the model to write them (no prompt change), and `QUESTION_BANK_TESTS` (on in production) still turns a test the
  model writes into a note. They are a ```` ```quiz ```` of schemaVersion 2 with 12 question types (single, multi, fill, numeric, short,
  long, match, order, cli, output lines, a scenario, diagram) in practice, mock-exam and review modes
  (`app/assessments/kinds.py`, `rules.py`, `modes.py`; save/clear/submit only on v2 records; docs/api-contract.md
  "Practice tests"), plus flashcards, `:::cheatsheet[Title]`, ```` ```video ```` (YouTube privacy-enhanced,
  click-to-load: the CSP's one frame host besides Turnstile) and ```` ```topology ````. Details:
  `frontend/CLAUDE.md`. Teaching them is a follow-up ([ROADMAP.md](ROADMAP.md)).
- **Retrieval never fails a turn:** skipped or failed with a reason code. Every search is scoped by
  `AccessScope(user_id)` (global sources plus the user's own); options come only from `resolve_assistant_config`.
  The embedding dimension is fixed at **1536** in three places, kept in sync by a test; changing model or dimension
  needs a migration plus a re-ingest.
- **Files** (`app/files`): the File Library, 100 MB per user, bytes in PostgreSQL. Untrusted bytes are parsed only
  by `python -m app.files.extract` in a sandboxed systemd transient unit (`netsecai-extract-*`) in production;
  `/health` reports `"files"`.
- **Enforced by `tests/test_architecture_guards.py`:** routers never import `app.rag` or pgvector; `app/chat/*`
  imports only `app.rag.graph`; `graph.py` depends on abstractions; `app/db/*` and `store.py` never import
  LangChain/LangGraph; the question bank never calls a model; no GraphRAG, Chroma, Pinecone, Weaviate or Qdrant.
- **Cross-cutting:** one error envelope `{"error": {code, message, requestId}}`; JSON logs never contain message or
  document text, or keys; per-account limits in `auth_attempts` under an advisory lock; asyncpg on purpose
  (psycopg async fails on Windows' Proactor loop).
- **Schema changes:** edit `app/db/models.py` and add a hand-written Alembic revision (`tests/test_foundation.py`
  checks parity). Migrations must be backward compatible: production runs them before switching releases.

## Deployment and production

Full procedure: [docs/deployment.md](docs/deployment.md); host layout: [INFRASTRUCTURE.md](INFRASTRUCTURE.md);
rollback: [docs/rollback.md](docs/rollback.md).

- **Host:** Ubuntu 24.04 VM `10.1.50.94` (`ssh root@10.1.50.94`), **shared with PadhAI's production**
  (`/opt/aichat`, `/etc/aichat`, `aichat-*` units, ports 3000/4100/4200/5432, `ayur.namahos.com`). Never touch
  PadhAI. Isolation comes from NetSec's hard-coded `netsecai` names, kept by `deploy-netsec/tests/namespace.test.mjs`
  (no PadhAI path, port or `systemctl start aichat-` in a NetSec deploy file) and the database identity checks;
  `deploy-netsec/server/safety.sh` only self-checks NetSec's own constants at preflight.
- **NetSec layout:** checkout `/opt/netsecai/app`, bare repository `/opt/netsecai/app.git`, state
  `/opt/netsecai/app-state` (`HISTORY`, `previous`), shared `/opt/netsecai/shared` (PostgreSQL compose file,
  backup and grants scripts), secrets `/etc/netsecai/{api,web,postgres,migration}.env` (mode 600; never touched by
  a deploy), backups `/var/backups/netsecai`.
- **Units:** `netsecai-api` (127.0.0.1:4400), `netsecai-web` (127.0.0.1:4300), `netsecai-postgres` (compose project
  `netsecai-postgres`, 127.0.0.1:55432), `netsecai-pg-backup` (+ timer, nightly), `netsecai-jobs` (+ timer,
  installed, not enabled), `netsecai-cloudflared`.
- **Public URL:** a Cloudflare **Quick Tunnel** (`*.trycloudflare.com`) to the web app. Its address changes
  whenever cloudflared restarts; `NEXT_PUBLIC_SITE_URL` (web.env) and `PUBLIC_APP_URL` (api.env) must follow it,
  and `status:netsec` fails until they do ([deployment.md, "Quick Tunnel address changed"](docs/deployment.md#quick-tunnel-address-changed)).
- **How a commit goes live:** commit → `bun run security` (PASS on the clean commit) → `git push origin main` →
  `deploy:netsec`. The deploy refuses a commit without a PASS receipt (`NETSEC_SECURITY_GATE=skip:<reason>`
  overrides it, logged in the local `.git` only) and a checkout that is not clean or not exactly GitHub `main`. On the host, `deploy-netsec/server/update.sh`
  (a detached systemd unit, one at a time) resets the checkout, rebuilds and restarts only the parts whose content
  changed, runs pending migrations after a backup, smoke-checks locally, and prints MATCH or MISMATCH.
- **`deploy/`** is PadhAI's deploy, kept as history; it refuses to run from this repository.
- **Never, by hand:** edit files in `/opt/netsecai/app`, run migrations outside a deploy, `git clean` there,
  `docker compose down -v`, or change `API_BASE_URL` in web.env (must stay `http://127.0.0.1:4400`).
- **Production changes need the owner's explicit authorization.** Read-only checks (`status:netsec`, logs) don't.

## The owner's hard rules (2026-09-29): production, testing and release

These are quoted exactly. Where they conflict with the owner directives of 2026-10-29 (next section), the
directives win.

> **PRODUCTION RULE:** Keep production simple and deterministic — Git main is the source of truth; deploy the exact verified commit to the server, build/run directly on the production VM, and keep frontend/backend versions strictly matched. No unnecessary containers, infrastructure layers, or deployment complexity; use system services/process supervision, health checks, backups, and rollback capability. After every release: verify the exact commit is live, services are healthy, and no stale/old release is serving traffic.

> **TESTING / WORK RULE:** Minimum safe code, maximum output — no Docker, no local setup, no unnecessary refactor, no test-suite expansion, no time/token waste; only check obvious build/runtime breakage. Commit/push to Git main and deploy directly to production, ensuring production runs the exact final main commit. Finish in one pass; report only what is actually live.

What they mean here:
- **Strictly matched** = `Status: MATCH` from `deploy:netsec` / `status:netsec`: the backend and the frontend both
  run the production checkout's code. A part whose code did not change keeps running (content keys); still MATCH.
- **No containers beyond the one that exists:** NetSec's PostgreSQL (compose project `netsecai-postgres`) is its
  only container. No Docker on the development PC (never reinstall or ask); WSL is used instead.
- **Checks before committing:**
  - backend: `ruff check`, `ruff format --check`, an import/compile check, and the focused tests (directives);
  - frontend: `bun run typecheck`, `bun run lint`, `bun run test` and `bun run build`;
  - `bun test deploy-netsec/tests` when deploy scripts changed; `bun run test:graph` when Graphify wiring changed;
  - `bun run security`: PASS (on the committed, clean tree for the deploy receipt).
- **Every task, in one pass:**
  1. **Implement:** minimum safe code; reuse what exists; no new dependencies, services or abstractions; no
     subagents unless the owner asks.
  2. **Check** as above. A failure is fixed, never shipped.
  3. **Push:** commit on `main`, `bun run security` PASS, `git push origin main`. CI does not run: report it as
     NOT RUN.
  4. **Release, only when the owner has authorized it:** `deploy:netsec`, which must end with `Status: MATCH`.
  5. **Verify live:** `status:netsec` (MATCH, healthy, tunnel = `NEXT_PUBLIC_SITE_URL`), `smoke:netsec`, and a live
     check of what changed through the tunnel URL. If anything fails: stop, fix, or `rollback:netsec`.
  6. **Report only what is actually live:** the deployed SHA, MATCH, the checks' results, what was proven live and
     what was not.

## Owner directives (2026-10-29)

These take precedence over the 2026-09-29 rules where they conflict.

- **Focused regression tests are required** for security and behaviour changes (a test that fails on the old code
  and passes on the new). "No test-suite expansion" no longer applies to them.
- **Isolated WSL `*_test` databases are allowed** for destructive tests (never a production database).
- **`bun run security` must PASS before every push and deploy;** the deploy checks its receipt.
- **Work in the one checkout:** no separate git worktrees.
- **Nothing is deleted without the owner's approval:** adapt PadhAI features to NetSec instead of removing them.
- **No production mutation without explicit authorization** (deploys, rollbacks, host changes, switches, data).
- **Plans use the code graph:** `bun run graph`, then `graphify affected` on what will change.
- **Work live, in the foreground (2026-11-04):** no subagents and no background jobs unless the owner asks for them;
  run independent checks as parallel foreground commands, using the whole machine.
- **Follow [docs/security/secure-development.md](docs/security/secure-development.md).**

## Security-first workflow

- **Before planning:** read this file and [docs/security/secure-development.md](docs/security/secure-development.md);
  run `bun run graph` + `affected`; name the trust boundaries and invariants at risk
  ([docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md)); use the planning template there.
- **Skills** (vendored in `.claude/skills/`, provenance in `.claude/skills/SOURCES.md`): `security-audit` for
  security questions and audits, `owasp-security-review` for an independent review of a diff,
  `gha-security-review` for `.github/workflows/`.
- **During:** authorise per user at the data layer; validate input at the boundary; get models and embeddings only
  from `build_llm` / `build_embeddings` (masking); never log or pass secrets in process arguments; bound SQL
  parameters only.
- **Before completion:** focused tests, the static checks, `bun run security`
  ([docs/security/security-tools.md](docs/security/security-tools.md)), docs updated, evidence recorded (BLOCKED or
  NOT TESTED where true). Releases follow
  [docs/security/production-security-checklist.md](docs/security/production-security-checklist.md).

## Code graph (Graphify)

`bun run graph` builds `graphify-out/` (pinned `graphifyy==0.9.97`, AST only, local, git-ignored; never commit,
ingest or deploy it). Query it with `affected`, `path`, `explain` and `query`. A missing edge proves nothing (it
does not see `Depends(...)` or SQL). Commands and boundaries: [docs/graphify.md](docs/graphify.md).

## Repo hygiene

- The root files `dummy_acc.txt`, `git.txt`, `google_auth.txt`, `ollalink.txt`, `ollastack.txt`, `ollima.txt` and
  `openrouter.txt` hold live keys and credentials. They are git-ignored (`*.txt`); **never open, print or commit
  them**, and never copy their values anywhere. The same goes for every `.env*` file.
- `.claude/settings.local.json` (per-user permission rules) is git-ignored; `.claude/skills/` holds the vendored security skills.
- `.gitattributes` forces LF (`* text=auto eol=lf`), for the shell scripts and for Biome.
- On this Windows machine Python's default encoding is cp1252: pass `encoding="utf-8"` when reading or writing files
  from scripts, and set `PYTHONIOENCODING=utf-8` when printing non-ASCII.
