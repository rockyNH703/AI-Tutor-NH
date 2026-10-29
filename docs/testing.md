# Testing

> NetSec AI, checked on 2026-10-29 against `scripts/run.mjs`, `scripts/security.sh`, `backend/tests/conftest.py`,
> `frontend/apps/app/playwright.config.ts`, `playwright.smoke.config.ts`, `deploy-netsec/tests/` and
> `scripts/tests/`. The owner's directives (2026-10-29, [CLAUDE.md](../CLAUDE.md)) require focused regression tests
> for security and behaviour changes. Which test protects which security property:
> [security/security-regression-tests.md](security/security-regression-tests.md).

## Contents

- [The layers](#the-layers)
- [Where each layer runs](#where-each-layer-runs)
- [Backend tests in WSL](#backend-tests-in-wsl)
- [Commands](#commands)
- [End-to-end tests](#end-to-end-tests)
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
| End to end (Playwright + axe) | The built web app, proxy, API and database together in browsers | `frontend/apps/app/e2e/` (25 specs, written for PadhAI) |

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
