# Local development

> NetSec AI, checked against `scripts/run.mjs`, `package.json` and `docker-compose.dev.yml` on 2026-10-29. The rules
> for changes are in [CLAUDE.md](../CLAUDE.md); tests are in [testing.md](testing.md).

One clone has everything: `backend/` (FastAPI) and `frontend/` (Next.js). Run commands from the repository root.

**Requirements:** Git, Python 3.12, [Bun](https://bun.sh) 1.3.14 (`.bun-version`), Node 24 (`.node-version`; the
web app runs on Node in production and in the end-to-end tests). On the owner's Windows PC also **WSL Ubuntu 24.04**:
the backend tests run there against a PostgreSQL 16 + pgvector cluster, and the security gate's scanners live there
(`/root/sec-tools`). **There is no Docker on the owner's PC** (owner's rule): never install it or ask for it.

## Set up

```sh
git clone https://github.com/rockyNH703/AI-Tutor-NH.git && cd AI-Tutor-NH   # private repository
bun run setup      # backend/.venv + requirements-dev.txt, backend/.env from its example, frontend bun install
```

Never run `bun install` at the repository root: the Bun workspace root is `frontend/`.

Settings: `backend/.env` for the API (`LLM_PROVIDER=fake` and `EMBEDDING_PROVIDER=fake` work offline; the fake
model replies `You said: <message>`), `frontend/apps/app/.env.local` for the web app (sign-in needs
`AUTH_DATABASE_URL`, `BETTER_AUTH_SECRET` and the API's `AUTH_INTERNAL_TOKEN`). Every variable:
[environment-variables.md](environment-variables.md). Never commit or print an env file.

## Run

- **With Docker** (other machines): `bun run dev` starts PostgreSQL 16 + pgvector on `127.0.0.1:5432` and Mailpit
  (SMTP `:1025`, inbox `http://127.0.0.1:8025`) from `docker-compose.dev.yml`, migrates, then runs the API on `:4000`
  (with reload) and the web app on `:3000`. Open `http://localhost:3000`, not `127.0.0.1` (the session cookie is per
  host name). API docs: `http://127.0.0.1:4000/docs` (development only).
- **Without Docker:** `bun run dev:backend` and `bun run dev:frontend` run the two halves; the API then needs a
  reachable PostgreSQL + pgvector in `DATABASE_URL` (for example a database in the WSL cluster) and an email
  backend. This combination is **not verified** end to end on the owner's PC; frontend checks and the WSL backend
  tests are the verified local workflow.

## Commands

| Command (repository root) | What it runs |
|---|---|
| `bun run test` | backend pytest (needs a reachable `*_test` database), frontend typecheck + unit tests, `bun test deploy-netsec/tests` |
| `bun run lint` | `ruff check`, `ruff format --check`, Biome |
| `bun run build` | "is `docs/openapi.json` current?", then the frontend production build |
| `bun run contract` | regenerate `docs/openapi.json` and `contract.gen.ts`, then the contract tests |
| `bun run security` | the local security gate ([security/security-tools.md](security/security-tools.md)); PASS on a clean commit writes the deploy receipt |
| `bun run graph`, `bun run test:graph` | the code graph and its boundary tests ([graphify.md](graphify.md)) |
| `bun run test:e2e` | Playwright + axe against a local API on `:4100` and Mailpit (not on the owner's PC) |
| `bun run lock:backend` | regenerate `backend/requirements.lock` in an Ubuntu 24.04 container: needs Docker, so it does not run on the owner's PC |
| `bun run deploy:netsec`, `status:netsec`, `rollback:netsec`, `smoke:netsec` | NetSec production ([deployment.md](deployment.md)); changes need the owner's authorization |

`bun run deploy`, `rollback`, `status`, `rehearse` and `verify` belong to PadhAI's `deploy/` and are not used here
(`deploy/deploy.sh` refuses to run from this repository). `bun run smoke` defaults to PadhAI's site: see
[deployment.md](deployment.md#check-production).

Directly: in `backend/`, `.venv\Scripts\python -m ruff check .` and `.venv\Scripts\python -m ruff format --check .`
(Linux: `.venv/bin/python`); in `frontend/`, `bun run typecheck`, `bun run lint`, `bun run test`; in
`frontend/apps/app`, `bun test src/lib/api/routes.test.ts`.

## Before you change anything

Follow [security/secure-development.md](security/secure-development.md): `git status` (others share this checkout),
`bun run graph`, then `affected` on what you will change, and the planning template for anything security-relevant.

## Dependencies

Production installs the backend from `backend/requirements.lock` (exact versions with hashes, `pip install
--require-hashes`); `requirements.txt` holds the ranges, and `requirements-dev.txt` adds the test tools. The frontend
is locked by `frontend/bun.lock`. Runtimes are pinned by `.node-version` and `.bun-version`. `bun run security`
audits both lockfiles (pip-audit, `bun audit`). Changing `requirements.txt` needs a regenerated lock, which needs
Docker (`bun run lock:backend`); doing it in WSL instead is not scripted (unverified).

## Changing the API

The contract is `docs/openapi.json`, generated from the backend. After changing a route or a request/response model,
run `bun run contract`: it regenerates the file and the web app's `contract.gen.ts` and runs `routes.test.ts`, which
fails if a route the proxy allows, or a request field it sends, no longer exists. Both halves change in the same
commit. A new endpoint the browser needs also goes into `frontend/apps/app/src/lib/api/routes.ts`.

## Adding a migration

Change `backend/app/db/models.py`, then in `backend/`: `.venv\Scripts\python -m alembic revision -m "describe it"`,
and write the upgrade by hand. `tests/test_foundation.py` fails if the models and the migrated schema differ.
Migrations must be backward compatible: production runs them before the new API starts
([deployment.md](deployment.md#migrations)).

## Knowledge base

The owner will provide NetSec's documents. Ingest `.md`/`.txt` files with an explicit visibility:

```sh
cd backend
.venv\Scripts\python scripts\ingest_documents.py --source <name> --global --metadata vendor=<v> --metadata platform=<p> <folder>
```

`--metadata key=value` is repeatable and stored with every chunk; the keys are conventions, not enforced. Without an
embeddings key (and not `fake`), retrieval is skipped and chat still works. Never point the script at production
without an approved plan ([rag.md](rag.md), written for PadhAI).

## Checks with a real model

`backend/scripts/eval_engineering.py` asks the configured model synthetic engineering questions and scores the
replies (pass-rate gates); it runs on the host with production settings and uses the production key, so the owner
decides when. `backend/scripts/smoke_chat.py` drives a full chat flow against a local API with Mailpit; never point
it at production.
