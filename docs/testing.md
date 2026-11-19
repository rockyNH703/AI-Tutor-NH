# Testing

> NetSec AI, checked on 2026-10-29 against `scripts/run.mjs`, `scripts/security.sh`, `backend/tests/conftest.py`,
> `frontend/apps/app/playwright.config.ts`, `playwright.smoke.config.ts`, `deploy-netsec/tests/` and
> `scripts/tests/`. The owner's directives (2026-10-29, [CLAUDE.md](../CLAUDE.md)) require focused regression tests
> for security and behaviour changes. Which test protects which security property:
> [security/security-regression-tests.md](security/security-regression-tests.md). The learning suite (the interactive
> blocks' end-to-end tests, which run on the owner's PC) was added on 2026-11-18.

## Contents

- [The layers](#the-layers)
- [Where each layer runs](#where-each-layer-runs)
- [Backend tests in WSL](#backend-tests-in-wsl)
- [Local hybrid retrieval benchmark](#local-hybrid-retrieval-benchmark)
- [Local restore rehearsal](#local-restore-rehearsal)
- [Commands](#commands)
- [End-to-end tests](#end-to-end-tests)
- [The learning suite](#the-learning-suite)
- [Model behaviour and production checks](#model-behaviour-and-production-checks)
- [CI](#ci)
- [Writing a test](#writing-a-test)

## The layers

| Layer | What it proves | Where |
|---|---|---|
| Backend tests (pytest) | API behaviour against a real PostgreSQL + pgvector: auth, isolation, chat, RAG, files, memory, secret masking, the NetSec prompt, migrations, architecture rules | `backend/tests/` (49 `test_*.py` files) |
| Web app unit tests (`bun test`) | Pure logic: the proxy, cookies, middleware, safe redirects, sign-in config, the SSE parser, the chat store, renderers, copy vocabulary, settings models | `frontend/apps/app/src/**/*.test.ts(x)` |
| Contract tests | The API contract is current, and every route and request field the web app proxies exists in it | `backend/scripts/export_openapi.py --check`; `src/lib/api/routes.test.ts`, `contract.gen.test.ts` |
| NetSec deploy script tests | The namespace and PadhAI denylist, the legacy `deploy/` guard, the security-gate receipt, the files sandbox wiring | `deploy-netsec/tests/*.test.mjs` |
| Graph boundary tests | Graphify never reads an ignored secret and stays inside its boundaries | `scripts/tests/graph.test.mjs` |
| The security gate | Semgrep (registry packs + `.semgrep/netsec.yml` and its self-test), gitleaks (tree and history), pip-audit, `bun audit`, zizmor, pinned tool versions | `scripts/security.sh` ([security/security-tools.md](security/security-tools.md)) |
| End to end (Playwright + axe) | The built web app, proxy, API and database together in browsers | `frontend/apps/app/e2e/` (25 specs, written for PadhAI); the learning suite `e2e/learning/*.learning.ts` ([below](#the-learning-suite)) |

PadhAI's `deploy/tests/` still exist but are not part of `bun run test`.

## Where each layer runs

| Layer | On the owner's PC | Needs |
|---|---|---|
| Frontend typecheck, lint, unit, contract, build | Windows, with Bun | `bun install --frozen-lockfile` in `frontend/` |
| Backend lint and import check | Windows (`backend/.venv`) | `bun run setup` |
| Backend pytest | **WSL Ubuntu 24.04**, against an isolated PostgreSQL 16 + pgvector cluster | a `*_test` database ([below](#backend-tests-in-wsl)) |
| Deploy script and graph tests | Windows, with Bun (graph tests also need `uv`) | – |
| `bun run security` | Git Bash on Windows, scanners in WSL (`/root/sec-tools`) | about 3 minutes |
| End to end | **Does not run** (needs Mailpit, normally from Docker) | an API on `:4100`, Mailpit, Playwright browsers |
| The learning suite (end to end) | **Runs:** Windows, headed, with the database in WSL | the steps [below](#the-learning-suite); `bunx playwright install chromium` (and `webkit` for the `learning-webkit` project); no Mailpit, no Docker |
| Browser smoke (`bun run smoke`) | Written for PadhAI; not verified against NetSec | `SMOKE_BASE_URL` set to NetSec's URL (it defaults to PadhAI's site) |

## Backend tests in WSL

The harness (`backend/tests/conftest.py`) uses a fake LLM, fake embeddings and an in-memory mailbox (no Mailpit).
It reads `TEST_DATABASE_URL`, **refuses any database whose name does not end in `_test`**, then drops, recreates and
migrates it once per session and truncates every table before each test. Destructive tests are allowed only there
(owner, 2026-10-29); never point it at a production database.

The steps (copy the backend into WSL without `.venv` or any `.env*` file, create a venv from the hashed lock, set
`TEST_DATABASE_URL`) are in [security/security-regression-tests.md, "How to run"](security/security-regression-tests.md#how-to-run).
Give each parallel run its own directory and database name. Then, in WSL:

```sh
.venv/bin/python -m pytest -o addopts="" -ra tests/test_redact.py tests/test_netsec_prompt.py   # focused
.venv/bin/python -m pytest -o addopts="" -ra                                                    # everything
```

`bun run test` at the repository root also runs the backend step, which succeeds only where a `*_test` database is
reachable from Windows (otherwise that step fails and the frontend and deploy steps still run).

## Local hybrid retrieval benchmark

`backend/scripts/benchmark_hybrid_retrieval.py` measures the PostgreSQL part of dense and hybrid retrieval without
calling an embedding provider. It requires `TEST_DATABASE_URL` explicitly, does not read application settings or
`.env*`, and refuses non-loopback hosts or database names that do not end in `_test`. Run it only against an
isolated database whose migrations are already at the current head. The script creates uniquely named synthetic
users, sources, documents and chunks, and removes them on exit; use a dedicated disposable database anyway.

The default corpus is 3,000 documents with 20 generic chunks each (60,000 generic chunks), plus four sentinel
chunks. Generic chunks are distributed across 12 deterministic topic vectors and six overlapping lexical families.
Sentinels cover an exact interface identifier, two private owners, and a second embedding model. Vectors are local,
deterministic 1,536-dimensional values, so reported latency is database search latency and excludes provider
embedding latency.

For dense and hybrid modes, the harness uses the production-default candidate bounds (20 dense and 20 lexical),
runs warmups and then recorded repetitions at concurrency 1, 4 and 8. Its
bounded JSON report contains sample/failure counts, p50/p95/p99, exact-identifier retrieval and cross-user/model-isolation
assertions, and compact `EXPLAIN (ANALYZE, BUFFERS)` evidence for the HNSW and GIN indexes. It exits nonzero unless
all hybrid concurrency levels have zero failures and p95 is strictly below 300 ms. The JSON contains no document
content, query text, vectors, connection URL or credentials.

Run in WSL with the backend venv and a local PostgreSQL + pgvector test cluster:

```sh
TEST_DATABASE_URL='postgresql+asyncpg://benchmark_operator@127.0.0.1:55432/retrieval_benchmark_test' \
.venv/bin/python scripts/benchmark_hybrid_retrieval.py --output retrieval-benchmark.json
```

Scale can be changed with `--documents` and `--chunks-per-document`; the product must remain between 100 and
250,000 generic chunks. `--warmups` and `--repetitions` control measurement counts. Do not publish the report as
production evidence: it is a local acceptance measurement and production performance remains unverified until an
authorized host run.

## Local restore rehearsal

`deploy-netsec/restore-drill.sh` is a local, destructive recovery check for an explicit PostgreSQL custom-format
archive. It never reads `.env*`, has no host or database default, and accepts only an explicit loopback database
whose lowercase name ends in `_test` and does not look like a NetSec/PadhAI/production target. The database is
force-dropped before the drill and again on exit, so use a unique disposable name. The script validates the
archive, restores without owners or grants, upgrades to the current Alembic head, starts the API in test mode with
fake LLM/embeddings and in-memory email, checks `/health` and `/ready`, then cleans up.

Run it in WSL (or another local Linux environment with the PostgreSQL client tools, the backend venv, and a local
PostgreSQL + pgvector test cluster). Do not put the password in the URL; use a mode-600 password file. Example:

```sh
TEST_DATABASE_URL='postgresql+asyncpg://restore_operator@127.0.0.1:55432/netsec_restore_rehearsal_test' \
PGPASSWORD_FILE="$HOME/.config/netsecai/restore-test-password" \
bash deploy-netsec/restore-drill.sh "$HOME/backups/netsecai-example.dump"
```

The supplied database user must be able to create/drop the named test database, restore the archive, create
extensions, and run migrations. This is a local rehearsal only; it must never be pointed at production or a
non-loopback host. The deploy-script suite validates the fail-closed guards and command sequence with mocks; it
does not restore a real archive automatically.

## Commands

| Command | Runs |
|---|---|
| `bun run test` | backend pytest, frontend typecheck + unit tests, `bun test deploy-netsec/tests`; every step runs, and it fails at the end if any failed |
| `bun run lint` | `ruff check`, `ruff format --check`, Biome |
| `bun run build` | the contract check, then the frontend production build |
| `bun run contract` | regenerate `docs/openapi.json` and `contract.gen.ts`, then the contract tests |
| `bun test deploy-netsec/tests` | the NetSec deploy script tests |
| `bun run test:graph` | the graph boundary tests |
| `bun run security` | the security gate; with uncommitted files it is check-only (no receipt) |

Directly: `frontend/` `bun run typecheck | lint | test`; `frontend/apps/app` `bun test src/lib/api/sse.test.ts`
(one file) or `bun test ./src -t "stop"` (by name). When piping output (`| tail`), check the exit code separately.

## End-to-end tests

`bun run test:e2e` builds the web app into `.next-e2e`, serves it on `:3100` (and a preview build on `:3101`), and
runs it against an API on `:4100` with the fake LLM (started from `backend/` unless one runs; `E2E_BACKEND_DIR=none`
never starts one), reading sign-up codes from Mailpit (`:8025`). `global-setup.ts` refuses any non-loopback URL, so
the suite can never touch production. The specs were written for PadhAI's copy; after the 2026-10-29 renames they
are updated in the working tree but have not been run on this PC. Variables:
[environment-variables.md, "Tests"](environment-variables.md#tests).

## The learning suite

`frontend/apps/app/playwright.learning.config.ts` runs `e2e/learning/*.learning.ts`: the interactive blocks (the 12
question types, practice and mock-exam modes, the v1 blocks, flashcards, cheat sheets and print, video, topology)
and the brutal cases. **It runs on this PC** with no Mailpit and no Docker, apart from the default suite. It has its
own ports (web 3210, preview 3211, which `serve.mjs` always starts, and API 4210), its own build
(`.next-learning-e2e`, git-ignored) and its own database (`netsec_learning_e2e_test` in WSL's PostgreSQL 16).
`bunx playwright test --list` (the default config) lists none of its files. Playwright starts and stops both
servers itself (`reuseExistingServer: false`), so ports 3210, 3211 and 4210 must be free first. The stack was
proven on 2026-11-18 (`stack.learning.ts`, a build run and a rerun).

1. **Database** (once; nothing is dropped). In WSL, wait until `pg_isready -h 127.0.0.1` answers (a cold VM runs
   crash recovery first). Then, as `aichat`, create `netsec_learning_e2e_test` owned by `aichat` if it is missing,
   and run `CREATE EXTENSION IF NOT EXISTS` for `vector`, `citext` and `pgcrypto`. Migration 0012 creates the
   NOLOGIN role `aichat_auth` itself.
2. **Migrations** (once, and after any new revision). From `backend/` on Windows, with `PYTHONUTF8=1`:
   `.venv/Scripts/python.exe -m alembic -x url=<DATABASE_URL from e2e/learning/env.ts> upgrade head`.
3. **Backend snapshot.** The e2e API runs the code of a copy of `backend/` kept outside the repository
   (`<evidence>\stack\backend-snapshot`, copied without `.venv`, caches or any `.env*` file; `robocopy /MIR`,
   where exit 1 means files were copied). It uses the repository's venv and an empty working directory
   (`<evidence>\stack\api-cwd`), so no `.env` is read. Copy again to test newer backend code.
4. **Run** from `frontend/apps/app`. The projects set headed mode, so pass no `--headed`: it would break
   `learning-pdf`, because `page.pdf()` is headless only.

   ```sh
   bunx playwright test -c playwright.learning.config.ts --list                 # what will run
   bunx playwright test -c playwright.learning.config.ts                        # the first run builds (~4 min)
   E2E_SKIP_BUILD=1 bunx playwright test -c playwright.learning.config.ts       # reuse the kept build
   bunx playwright test -c playwright.learning.config.ts --project learning-demo   # watch the @demo flows
   LEARNING_LIVE=1 bunx playwright test -c playwright.learning.config.ts video-live   # the real YouTube player
   ```

   Before a building run, save a copy of `tsconfig.json`. `next build` rewrites the file, and `serve.mjs` puts
   its bytes back. Compare the file afterwards, and if it differs, copy the saved bytes back (never
   `git checkout`).

- **Projects:**
  - `learning`: desktop Chrome at 1440 px; the specs pick 834, 390 or 320 px (a Pixel 7 at phone widths); video on.
  - `learning-demo`: the `@demo` flows, slowed down (slowMo 400).
  - `learning-webkit`: an iPhone 14 (WebKit) for `@webkit` (numeric, cli, video).
  - `learning-pdf`: headless, for `@pdf` (print to PDF, a real clipboard read-back).
  - The live YouTube check (`@live`) never gates a release. Offline, or with automation blocked, it reports
    BLOCKED.
- **What it starts:**
  - **The API**, from the snapshot on :4210. The model, embeddings and email are fakes (`LLM_FAKE_TOKEN_DELAY_S=0.01`).
    `QUESTION_BANK_TESTS=false`, so a model-written test is recorded as written; production keeps the bank-only
    rule. `FILES_ENABLED=false`. The intent router, study scope, voice, NCERT and memory recall or proposals are
    off. The chat generation limits are raised. `PYTHONUTF8=1`.
  - **The web app**, `e2e/serve.mjs` with `NEXT_DIST_DIR=.next-learning-e2e`, `API_BASE_URL=http://127.0.0.1:4210`,
    `NEXT_PUBLIC_SITE_URL=http://localhost:3210`, a libpq `AUTH_DATABASE_URL` and `AUTH_RATE_LIMIT=off`.
  - `BETTER_AUTH_SECRET` and `AUTH_INTERNAL_TOKEN` are the default suite's e2e-only literals
    (`e2e/learning/env.ts`). Every value is set explicitly, so it wins over `apps/app/.env.local` (never open it).
- **Guards:**
  - The config refuses a database that is not on loopback or whose name does not match `^[a-z0-9_]+_test$`. It
    checks this when it loads, before any server starts.
  - `e2e/learning/global-setup.ts` checks the same again. It also checks that every `baseURL` and the API are on
    loopback, and requires `/ready` to return `{status: ok, schema: current}`.
- **Sign-in is seeded per test** (`e2e/learning/support.ts`, as `smoke_chat.py` does):
  - a `users` row and an `auth_sessions` row with a random token;
  - the host-only `padhai_session` cookie, signed byte for byte as Better Auth signs it (`e2e/learning/wire.test.ts`);
  - the user is deleted afterwards.
- **Content:** each reply comes from `backend/tests/fixtures/learning/*.json` (also checked by
  `tests/test_learning_fixtures.py`). The fixture is sent to `/api/v1/chat/stream`, and the fake model echoes it.
  `e2e/learning/fixtures.test.ts` and `wire.test.ts` are `bun test` files for the suite's own logic.
- **Evidence stays outside the repository.** Each run writes to
  `C:\Users\test\Desktop\netsec-learning-evidence\runs\<UTC time>\` (`EVIDENCE_DIR` in `e2e/learning/env.ts`):
  `results\` holds videos, screenshots and traces on failure, and `report\` the HTML report
  (`bunx playwright show-report <dir>`). A `--list` run leaves an empty report folder; delete it.

## Model behaviour and production checks

- **`backend/scripts/eval_engineering.py`:** the real model on synthetic engineering cases
  (`backend/tests/fixtures/engineering_eval_cases.jsonl`), scored by regex with pass-rate gates, including a
  forbidden school-tutor register. Run on the host after any change to what the model is told; it uses the
  production key, so the owner decides when. Its scorer has unit tests (`tests/test_eval_engineering.py`). On the
  host, with production's settings (a candidate prompt can run from a copy of `backend/` under `/tmp`, with
  `PYTHONPATH` pointing at it, without a deploy):

  ```sh
  systemd-run --pipe --wait --collect --quiet -p EnvironmentFile=/etc/netsecai/api.env \
    -p WorkingDirectory=/opt/netsecai/app/backend --setenv=PYTHONPATH=/opt/netsecai/app/backend \
    /opt/netsecai/app/backend/.venv/bin/python scripts/eval_engineering.py --runs 3 [--show-failures]
  ```

  Results on 2026-10-29 (`gemini-3.5-flash-lite`, 3 runs per case, 57 replies):

  | Prompt | Pass rate |
  |---|---|
  | PadhAI's prompt | 37/57 (65%) |
  | The first NetSec prompt (`0d868b7`), temperature 0.7 | 43/57 (75%) |
  | The same at temperature 0.3 | 39/57 (68%), so the default 0.7 stays |
  | The tuned prompt (3,780 characters) | 45/57 (79%) |
  | The citation-honesty prompt (2026-11-16, 4,099 characters) | NOT RUN (needs the host and the production key's quota) |

  The 2026-11-16 prompt adds the citation-honesty clauses (cite only the passage that supports the specific
  claim; say when the library lacks a topic and label the answer not source-backed; never present a command or
  its output as from the material unless it shows it). Its live validation run is pending; the frozen-prompt
  fixtures and `tests/test_netsec_prompt.py` follow the new text.

  Still below their gates with the tuned prompt: a vague request ("my VPN is down") asks for too little (1/3); AWS
  egress never names VPC flow logs; the Ansible backup rarely offers check mode; the OSPF review rarely advises a lab
  test (1/3). A difference of two or three replies is within run-to-run noise. Known scorer false negative:
  `k8s_netpol` does not accept "deny-by-default".
- **After a release:** `deploy:netsec` runs a local smoke on the host (units, `/health`, `/ready`, `/login`, sign-in
  database, web → API → database). Then `status:netsec`; `smoke:netsec` currently runs the same read-only checks.
  The live check of what changed is done by hand through the tunnel URL.

## CI

`.github/workflows/ci.yml` exists, but **GitHub Actions do not run** for this repository (the account is out of
credit). Report CI as NOT RUN, never as passed. The enforced check is `bun run security`, whose PASS receipt the
deploy requires; the suites above are run by hand.

## Writing a test

- **A security or behaviour change gets a test that fails on the old code and passes on the new**; record the
  failing run. A new route with ownership gets its cross-user case in `test_authorization.py`.
- **Prefer the layer closest to the rule:** a pure function gets a unit test; an API rule a pytest against the real
  database (in WSL); a user flow an e2e spec.
- **Use the fakes:** the fake LLM echoes `You said: <message>`; fake embeddings measure shared vocabulary only
  (plumbing, not relevance); the in-memory mailbox holds codes in backend tests.
- **A new endpoint for the browser** needs its `routes.ts` entry and `bun run contract`.
- **Test databases' names must end in `_test`.** Use made-up configs and `@example.com` addresses, never real data
  or real secrets (secret-masking tests use synthetic values).
