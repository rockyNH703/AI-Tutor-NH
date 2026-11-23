# Deployment

> NetSec AI's production deploy, checked against `deploy-netsec/` on 2026-10-29. **Every production action here
> needs the owner's explicit authorization**; read-only checks (`status:netsec`, logs) don't. The owner's rules are
> in [CLAUDE.md](../CLAUDE.md); the release checklist is
> [security/production-security-checklist.md](security/production-security-checklist.md).

## Contents

- [How a change reaches production](#how-a-change-reaches-production)
- [Deploy](#deploy)
- [Check production](#check-production)
- [When a deploy fails](#when-a-deploy-fails)
- [On the server](#on-the-server)
- [Migrations](#migrations)
- [One-time host preparation](#one-time-host-preparation)
- [Production database isolation](#production-database-isolation)
- [Environment files](#environment-files)
- [Sign-in (Better Auth)](#sign-in-better-auth)
- [Quick Tunnel address changed](#quick-tunnel-address-changed)
- [File Library](#file-library)
- [Jobs worker](#jobs-worker)
- [Switches that are owner decisions](#switches-that-are-owner-decisions)
- [Changing the database container on purpose](#changing-the-database-container-on-purpose)
- [Never do this](#never-do-this)
- [PadhAI's `deploy/` (history)](#padhais-deploy-history)

## How a change reaches production

```
commit ─► bun run security (PASS receipt) ─► git push origin main ─► deploy:netsec ─► production Git
       ─► production checkout ─► changed parts only ─► local smoke ─► MATCH ─► status:netsec + live check
```

1. Commit the change on `main` in the one checkout.
2. `bun run security` on the clean tree: a PASS writes the receipt `.git/netsec-security-gate` for that commit
   (`scripts/security.sh`, [security/security-tools.md](security/security-tools.md)).
3. `git push origin main`.
4. `deploy:netsec` checks the receipt, pushes the **same commit** to `/opt/netsecai/app.git` on the host (which has
   no GitHub access), and runs `deploy-netsec/server/update.sh` there.
5. The host moves its checkout to exactly that commit, rebuilds and restarts only the parts that changed, runs a
   local smoke check, and prints `MATCH` or `MISMATCH`.
6. You run `status:netsec` and check what changed through the public URL.

There are no pull requests, and GitHub Actions do not run (the account is out of credit): `status:netsec` prints
the CI line as `not run (Actions disabled)`: the release gate is the `bun run security` receipt.

## Deploy

```sh
git push origin main
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run deploy:netsec
```

`deploy-netsec/deploy.sh` takes the host from `--host`, then `NETSEC_DEPLOY_HOST`, then `DEPLOY_HOST`, then
`root@10.1.50.94` (`deploy.sh:30`). `status:netsec` and `rollback:netsec` require `--host` or `NETSEC_DEPLOY_HOST`
(no default); `smoke:netsec` reads only `NETSEC_DEPLOY_HOST`. It refuses, before touching the host, when:

- there is no PASS receipt for HEAD (`no PASS security receipt for <sha>`). `NETSEC_SECURITY_GATE=skip:<reason>`
  deploys without one: a banner, and a line in `.git/netsec-security-gate.overrides`. Use it only with the owner's
  approval;
- the checkout has uncommitted or untracked changes, or is not exactly GitHub `main` (fetched now).

The work on the host runs as a detached systemd unit (`netsecai-deploy-<sha>-…`) under a lock, so a dropped
connection does not stop it halfway, and a second deploy stops at once ("another deploy is running"). Every
deploy ends with:

```
GitHub main SHA:          <sha>
Production checkout SHA:  <sha>
Backend running SHA:      <sha>   (same backend code as the checkout)
Frontend running SHA:     <sha>
Status: MATCH
```

A part that did not change keeps running an older commit with identical code: still `MATCH`.

### What runs for which change

`update.sh` compares content keys (git objects of each part's files, tests excluded; `deploy-netsec/server/lib.sh`)
with what each part runs, and does only what differs, in this order:

| Changed | What happens |
|---|---|
| `backend/requirements.lock` | `pip install --require-hashes` into the existing venv, then `pip check` |
| a `package.json`, `frontend/bun.lock` or `.bun-version` | `bun install --frozen-lockfile --force` into the existing `node_modules` (`--force` relinks every package, dropping stale nested copies; the running web app is not restarted until the end, see [known-limitations.md](known-limitations.md)) |
| `frontend/`, `.node-version`, `.bun-version`, the web unit, or any `NEXT_PUBLIC_*` line in `web.env` | the frontend is **built beside the served build** (`.next-build`, under a 2 GB memory cap); a failed build never reaches users. If the build the last deploy replaced (`.next-prev`) is this code, it is reused without building |
| `deploy-netsec/systemd/*` | changed unit files rewritten (old ones kept as `<unit>.before-<time>`); the affected service restarts |
| `backend/alembic/versions/` | if a migration is pending: a backup, then `alembic upgrade head` ([Migrations](#migrations)) |
| always | `database-grants.sh` re-applies the two login roles and their grants, then the web app's role is checked against its tables |
| `backend/` or the API unit | restart `netsecai-api`; it must report the new commit on `/health` |
| the frontend (built above) | stop `netsecai-web`, swap the new build in, start it; the replaced build becomes `.next-prev` |
| anything else (docs, tests) | nothing is rebuilt or restarted |

Then the local smoke: both units active, API `/health` and `/ready`, web `/login`, the web app's sign-in database
(`/api/health`), and a request through web → API → database whose request id must appear in the API's log.

## Check production

```sh
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run status:netsec   # read-only; exits 1 unless healthy and MATCH
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run smoke:netsec    # today: deploy-netsec/smoke.sh runs status.sh (the same checks)
```

`status:netsec` runs `deploy-netsec/server/status.sh` on the host: the checkout against production Git, what each
part runs, both units, `/ready`, `/login`, `API_BASE_URL`, the sign-in settings (names only, never values), the
tunnel address against `NEXT_PUBLIC_SITE_URL`, `PUBLIC_APP_URL` against it, that the public `/login` answers 200, that the
backup timer is on and a dump from the last 26 hours exists (each of these fails the status, K-10b), the jobs timer, files,
and that no legacy port (4000/3210/3211/6791) listens. While a deploy runs, the jobs and knowledge workers skip their
timer ticks (`ConditionPathExists=!/run/netsecai-deploying`).

On the server:

```sh
systemctl status netsecai-api netsecai-web netsecai-postgres netsecai-cloudflared
journalctl -u netsecai-api -u netsecai-web -n 50
journalctl -u 'netsecai-deploy-*'                   # deploy logs
tail /opt/netsecai/app-state/HISTORY                # every deploy and rollback: who, what, how long
bash /opt/netsecai/app/deploy-netsec/infra-check.sh # status plus loopback ports, isolation, backups, disk, OCR
```

**Browser smoke:** `bun run smoke` (Playwright, `frontend/apps/app/playwright.smoke.config.ts`) was written for
PadhAI and defaults to `https://ayur.namahos.com`, reading credentials from `dummy_account.txt`. Never run it without
`SMOKE_BASE_URL` set to NetSec's tunnel URL and `SMOKE_EMAIL`/`SMOKE_PASSWORD` set; its checks are not verified
against NetSec. Until they are, the live check of a release is done by hand through the tunnel URL.

## When a deploy fails

A deploy stops at the first failure with `STOP:` and the reason, prints what each part runs, and exits non-zero.
What was not restarted keeps serving; the next deploy redoes whatever did not finish.

| It says | Do |
|---|---|
| `no PASS security receipt for …` | commit, `bun run security` on the clean tree until PASS, then deploy |
| `uncommitted or untracked changes` / `is not GitHub main` | commit, `git push origin main`, deploy again |
| `another deploy is running` | wait for it, then deploy again |
| `missing /etc/netsecai/…` / `web.env must set …` / `AUTH_INTERNAL_TOKEN must be set, and the same` | fix the env file on the host (authorized), deploy again |
| `… must target only the isolated NetSec database` / `database identity marker missing` | stop: the env files or the database are not NetSec's. Investigate before anything else |
| `the frontend build failed` | users still get the previous build. Fix the code, push, deploy |
| `pip install failed` / `bun install failed` | nothing was restarted. Fix the lockfile, push, deploy |
| `the migration failed (backup: …)` | the API was not restarted. Read `journalctl -u 'netsecai-deploy-*'`, fix the migration, push, deploy |
| `the API is not ready after its restart` | `rollback:netsec`, then read `journalctl -u netsecai-api` |
| `the new frontend did not start` | the previous build is already serving again; read `journalctl -u netsecai-web` |

## On the server

```
/opt/netsecai/app.git      the production Git repository: deploys push here
/opt/netsecai/app          the production checkout: the code that runs
  backend/.venv              Python packages (reinstalled only when requirements.lock changes)
  frontend/node_modules      JS packages (reinstalled only when package.json/bun.lock change)
  frontend/apps/app/.next    the served build (.next-prev: the one before; .next-cache: Next's cache)
  RELEASE                    the commit, reported by the API's /health and the web app's /api/health
/opt/netsecai/app-state    content keys of each part, deployed, previous, HISTORY
/opt/netsecai/shared       postgres/: compose file, backup.sh, database-grants.sh, their helpers (never part of a deploy)
/opt/netsecai/runtime      the pinned Node versions (checksum-verified downloads)
/etc/netsecai/*.env        the secrets (mode 600; never in Git, never touched by a deploy)
/var/backups/netsecai      the dumps
```

`git reset --hard` changes only tracked files; the venv, `node_modules`, builds, database, backups and secrets are
never deleted by a deploy (it never runs `git clean`). A deploy never restarts PostgreSQL.

## Migrations

A migration runs while the previous API is still serving, and a rollback runs older code on the newer schema, so
**every migration must be backward compatible** (expand, then contract):

- **In one step:** new tables, nullable columns or columns with a server default, indexes (for example
  `0020_engineering_profiles`: one new table).
- **Over several deploys** (new code first, removal later): renaming or dropping a column or table, a NOT NULL
  without a default, a type change.
- **A backup first:** `netsecai-pg-backup.service` runs before any pending migration; the dump is named in the
  deploy output (`/var/backups/netsecai/netsecai-*.dump`).
- **Run as the owner role** from `migration.env` (`netsecai_migrator`), never as the API's role. The historical
  migrations 0012/0013 grant to `aichat_auth`, so `update.sh` creates that role as `NOLOGIN` just before Alembic and
  `database-grants.sh` moves its grants to `netsecai_auth` and drops it afterwards.

## One-time host preparation

Only for a new host, with the owner's approval. From any copy of this repository on the host:

```sh
sudo bash deploy-netsec/install.sh   # lists the secret files it needs; fill them from deploy-netsec/env/*.example, run again
```

Before any change it validates `postgres.env` (fixed names, port 55432, backup directory, the identity marker) and
checks that ports 4300, 4400 and 55432 are free or owned by the matching NetSec unit. It then installs Git,
Python 3.12, Tesseract (`eng`, `hin`), Docker only if absent (an existing Docker is used as is), the pinned Bun and
Node, creates `/opt/netsecai/{app-state,shared,runtime}`, installs the units, starts PostgreSQL and the backup
timer, and on a brand-new, empty database writes its identity marker. It never builds or starts the app (the first
`deploy:netsec` does), never writes secrets and never imports data.

## Production database isolation

PostgreSQL 16 + pgvector in the Compose project `netsecai-postgres` (container `netsecai-postgres-postgres-1`,
network `netsecai_postgres_network`, volume `netsecai_postgres_data`, database `netsecai`) on `127.0.0.1:55432`.
These names are pinned: never share or change them (a different volume is an empty database).

| Role | Used by | Where |
|---|---|---|
| `netsecai_migrator` | owner of the database: Compose initialization, backups, migrations | `postgres.env` (`POSTGRES_USER`), `migration.env` (`MIGRATION_DATABASE_URL`) |
| `netsecai_runtime` | the API (`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`; table and sequence DML) | `api.env` (`DATABASE_URL`) |
| `netsecai_auth` | the web app's Better Auth: identity tables only | `web.env` (`AUTH_DATABASE_URL`) |

`database-grants.sh` creates or repairs both login roles from `postgres.env` on every deploy (passwords pass
through the environment, never process arguments). Every script that touches the database first proves the
container, volume, network and the identity marker (`NETSECAI_DB_IDENTITY`, stored as the database's comment), and
that each URL targets `127.0.0.1:55432/netsecai` with its expected role.

## Environment files

All production values live in `/etc/netsecai/{api,web,postgres,migration}.env` (root, mode 600), never in git.
Templates: [`deploy-netsec/env/`](../deploy-netsec/env/). Every variable, its default and who reads it:
[environment-variables.md](environment-variables.md). The deploy refuses to proceed unless `web.env` sets
`API_BASE_URL=http://127.0.0.1:4400`, `NEXT_PUBLIC_SITE_URL`, `AUTH_DATABASE_URL`, `BETTER_AUTH_SECRET` and
`AUTH_INTERNAL_TOKEN` (the same value as in `api.env`). The API itself refuses unsafe production values at start
(`Settings.validate_for_env`), including any `APP_NAME` other than `NetSec AI`.

A runtime value takes effect on the next restart of the service that reads it (authorized); a `NEXT_PUBLIC_*` value
also changes the frontend's content key, so the next `deploy:netsec` rebuilds the frontend and `status:netsec`
reports the frontend as not current until then.

To run a one-off command with production settings, load the file the way the service does; never `source` it:
`systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/netsecai/api.env -p WorkingDirectory=/opt/netsecai/app/backend /opt/netsecai/app/backend/.venv/bin/python <script>`.

## Sign-in (Better Auth)

Better Auth runs in the web app (`frontend/apps/app/src/lib/auth`); the API checks every session. The deploy and
`status:netsec` check these (names only, never values):

| File | Variable | What |
|---|---|---|
| `web.env` | `AUTH_DATABASE_URL` | `postgresql://netsecai_auth:<password>@127.0.0.1:55432/netsecai` (the role `database-grants.sh` manages) |
| `web.env` | `BETTER_AUTH_SECRET` | signs the session cookie (`__Host-padhai_session`; Better Auth's other cookies are `__Host-netsecai.*`). At least 32 random characters |
| `web.env` + `api.env` | `AUTH_INTERNAL_TOKEN` | the same value in both: Better Auth's calls to the API's `/internal/auth/*` (codes and security notices). At least 32 characters |
| `web.env` | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | optional; unset hides "Continue with Google" |
| `web.env` | `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | optional; unset turns the adaptive CAPTCHA off (the hard limits stay) |

Generate secrets on the host and append them without printing them, e.g.
`printf 'BETTER_AUTH_SECRET=%s\n' "$(openssl rand -base64 48)" >> /etc/netsecai/web.env`.

**Rotating** (authorized; each needs a restart of what reads it):
- `BETTER_AUTH_SECRET`: every session stops verifying, so everyone signs in again. Restart `netsecai-web`.
- `AUTH_INTERNAL_TOKEN`: change both files, then restart `netsecai-api` and `netsecai-web` together.
- A database role's password: change `POSTGRES_RUNTIME_PASSWORD` / `POSTGRES_AUTH_PASSWORD` in `postgres.env` and
  the matching URL, let `database-grants.sh` apply it (it runs on every deploy), restart the reader, and prove the
  old password is refused.
- The Google or Turnstile secret: create the new one at the provider, switch `web.env`, restart `netsecai-web`,
  then delete the old one.

**Limits, CAPTCHA, two-step sign-in, breached passwords and Google linking** work as the code and
[security/README.md](security/README.md) describe: per-account limits in `auth_attempts` (atomic across both
processes), Turnstile only when configured, TOTP + recovery codes asked at every way in, HIBP range checks
(k-anonymity), and Google never attached to an existing account by matching its address. Security notices are
emailed at once, whatever `NOTIFICATIONS_MODE` says. Backups leave out `auth_sessions` and `auth_verifications`: after
a restore everyone signs in again.

**Google sign-in for NetSec:** an OAuth client of type "Web application" (consent screen "NetSec AI", scopes
`openid email profile`), JavaScript origin `https://<host>` and redirect URI exactly
`https://<host>/api/auth/callback/google`, where `<host>` is the current tunnel address. Set both values in
`web.env`, restart `netsecai-web`; `status:netsec` then says "Google sign-in: configured". Every tunnel address
change needs the client updated too.

## Quick Tunnel address changed

The public address is a Cloudflare Quick Tunnel (`netsecai-cloudflared.service`: `cloudflared tunnel --url
http://127.0.0.1:4300`). Cloudflare picks a new `*.trycloudflare.com` host whenever cloudflared restarts. Sign-in
trusts only `NEXT_PUBLIC_SITE_URL` (Better Auth's origin check), so a new host breaks every sign-in, and
`status:netsec` fails with `tunnel is … but NEXT_PUBLIC_SITE_URL is …`. To recover (authorized):

1. Find the new address: `status:netsec` prints it (the last `trycloudflare.com` URL in this run of the unit's
   journal).
2. Set `NEXT_PUBLIC_SITE_URL=https://<new host>` in `/etc/netsecai/web.env` and restart `netsecai-web` (Better Auth
   reads it at runtime).
3. Set `PUBLIC_APP_URL=https://<new host>` in `/etc/netsecai/api.env` and restart `netsecai-api` (email links and
   logo).
4. If Google is configured, update the client's origin and redirect URI.
5. The web build still carries the old value (`NEXT_PUBLIC_*` is baked in), and the changed value makes the
   frontend's content key differ: `status:netsec` reports the frontend as not current until `deploy:netsec` (of the
   same `main` commit, with its receipt) rebuilds it.
6. `status:netsec` must show `tunnel https://<new host> is NEXT_PUBLIC_SITE_URL`, `api.env PUBLIC_APP_URL is
   NEXT_PUBLIC_SITE_URL` and `Status: MATCH`.

A named tunnel with a fixed hostname (`deploy-netsec/cloudflared/config.yml.template`, prepared for
`chat.netsecai.com`, needing its own tunnel, credential and DNS, all owner approvals) removes this failure mode.
Never reuse PadhAI's cloudflared service, config, tunnel or credential.

## File Library

- **Needs:** embeddings configured (`EMBEDDING_*`; 1536 dimensions) and Tesseract with English and Hindi
  (`install.sh` installs them).
- **After a start** the API's file worker kills leftover `netsecai-extract-*` units, settles files a previous
  process left, and runs a self-test through the real sandbox. Files are available only if it passes:
  `GET /health` → `"files": "available"` (shown by `status:netsec` and `infra-check.sh`). Otherwise uploads get 503
  and chat is unaffected.
- **Sandbox check on the host:** `cd /opt/netsecai/app/backend && .venv/bin/python scripts/sandbox_probe.py`.
- **During a deploy** `update.sh` writes `/run/netsecai-deploying`; the worker makes no new claims while it exists.
- **Capacity:** uploads stop (503 `files_capacity_reached`) when the database reaches 3 GiB or the disk has less than
  5 GB free (`app/files/service.py`). `backup.sh` refuses to dump with less than twice the database size free.
- **Kill switch:** `FILES_ENABLED=false` in `api.env` and restart `netsecai-api`: no uploads or processing; ready
  attachments keep working.

## Jobs worker

`python -m app.jobs` sends the email outbox, plans reminders and applies retention. `netsecai-jobs.timer` starts one
run a minute. It ships **installed but not enabled**, and every email to users is **off** until the owner turns it
on:

| Setting (`api.env`) | Default | Meaning |
|---|---|---|
| `JOBS_ENABLED` | `false` | `false`: a run exits at once |
| `NOTIFICATIONS_MODE` | `off` | `off` → `dry_run` (recorded, never sent) → `allowlist` (only `NOTIFICATIONS_ALLOWLIST`) → `on` |
| `NOTIFICATIONS_ALLOWLIST`, `NOTIFICATIONS_KINDS`, `NOTIFICATIONS_MAX_PER_RUN` | –, every kind, 50 | who, which kinds, how many per run |
| `PUBLIC_APP_URL` | – | the tunnel's address (a bare HTTPS origin); required for any mode but `off` |
| `SUPPORT_EMAIL` | – | where support requests are forwarded; empty: stored only |

The worker reads these on every run (no restart); the API reads them at start, so restart `netsecai-api` after a
change for Settings to say the same. Enabling, in order, each step authorized and checked:
`JOBS_ENABLED=true` and `systemctl enable --now netsecai-jobs.timer`, then `dry_run`, then `allowlist`, then `on`.
Delivery is at most once (a run that dies mid-send leaves the row `unknown`, never resent); the guarantees are tested
in `backend/tests/test_jobs.py`.

## Switches that are owner decisions

All default off (`backend/app/settings.py`, `deploy-netsec/env/api.env.example`); turning one on is an `api.env`
change plus an API restart, with the owner's approval:

| Switch | What it does | Before turning it on |
|---|---|---|
| `TUTOR_INTERACTIVE_BLOCKS` | teaches the model the MCQ/quiz/fill-blank/flashcards/graph/diagram blocks and question-bank tests | a certification question bank; `eval_engineering.py` |
| `TUTOR_TEACHING_POLICY` | coaching defaults (`app/chat/teaching.py`) | `eval_engineering.py` |
| `INTENT_ROUTER` | Ollalink classifier (message text leaves unmasked) | new cases and `eval_intents.py` for the 2026-10-29 taxonomy |
| `STUDY_SCOPE` | OpenRouter decision models (masked: the message and the last exchange) | `eval_decision_scope.py` on `backend/evals/scope` (dev) and one held-out run; kill switch `STUDY_SCOPE=off` and an API restart |
| `VOICE_MODE` (+ `VOICE_ACK`, `VOICE_PARALLEL_ROUTING`, `VOICE_SERVER_SPEECH`) | OllaLink speech, English only | a live check on `qa` accounts |
| `MEMORY_RECALL_ENABLED`, `MEMORY_AUTOMATIC_PROPOSALS` | memory in chat, automatic memory proposals | [memory-evaluation.md](memory-evaluation.md) |
| the jobs timer, `NOTIFICATIONS_MODE` | email to users | [Jobs worker](#jobs-worker) |

## Changing the database container on purpose

`install-units.sh` never replaces `/opt/netsecai/shared/postgres/docker-compose.yml`; if a commit changes
`deploy-netsec/compose/docker-compose.yml`, the deploy prints a warning. To apply it (authorized): back up
(`systemctl start netsecai-pg-backup`), copy the file into `/opt/netsecai/shared/postgres/`, then
`systemctl restart netsecai-postgres netsecai-api` (a short outage). Never change the pinned project, network or
volume names.

## Never do this

- Edit files in `/opt/netsecai/app` by hand (the next deploy resets them; `status:netsec` flags local edits).
- Run migrations outside a deploy, or `git clean` in `/opt/netsecai/app`.
- `docker compose down -v`, or anything that recreates the database volume.
- Change `API_BASE_URL` in `web.env` (it must stay `http://127.0.0.1:4400`).
- Touch anything of PadhAI's (`/opt/aichat`, `/etc/aichat`, `aichat-*`, ports 3000/4100/4200/5432,
  `ayur.namahos.com`).
- Put secrets in Git, or print them.

## PadhAI's `deploy/` (history)

`deploy/` is PadhAI's production deploy (`/opt/aichat`, `aichat-*` units), kept in this repository as history,
together with `bun run deploy | rollback | status | rehearse | verify` in `package.json`. `deploy/deploy.sh` now
stops at once when `deploy-netsec/` exists ("this is the NetSec AI repository …"), so it can never ship NetSec code
into PadhAI's production. Its documentation is [deploy/README.md](../deploy/README.md) and the git history of this
page.
