# Infrastructure

> NetSec AI's production host, as the scripts in [deploy-netsec/](deploy-netsec/) define it (checked against them on
> 2026-10-29). What actually runs on the host is shown by `bun run status:netsec` and, on the host,
> `deploy-netsec/infra-check.sh`; this page does not claim live state.

## The host is shared with PadhAI

One Ubuntu 24.04 VM, `10.1.50.94` (`ssh root@10.1.50.94`), runs **two separate applications**:

| | NetSec AI (this repository) | PadhAI (another repository's production) |
|---|---|---|
| Code | `/opt/netsecai/app` | `/opt/aichat` |
| Secrets | `/etc/netsecai` | `/etc/aichat` |
| Backups | `/var/backups/netsecai` | `/var/backups/aichat` |
| Units | `netsecai-*` | `aichat-*` |
| Ports (loopback) | 4300 web, 4400 API, 55432 PostgreSQL | 3000, 4100, 4200, 5432 |
| Public address | a Cloudflare Quick Tunnel (`*.trycloudflare.com`) | `ayur.namahos.com` |

**Never touch PadhAI.** The isolation comes from NetSec's own names, hard-coded in every script (`/opt/netsecai`,
`/etc/netsecai`, `netsecai-*` units, ports 4300/4400/55432, the `netsecai-postgres` Compose project), and from
[`deploy-netsec/tests/namespace.test.mjs`](deploy-netsec/tests/namespace.test.mjs) (`bun test deploy-netsec/tests`),
which fails if a NetSec deploy file names PadhAI's paths, `systemctl start aichat-` or PadhAI's ports. Every script
that touches the database first proves NetSec's container, volume, network and identity marker
(`assert_database_identity`). [`deploy-netsec/server/safety.sh`](deploy-netsec/server/safety.sh), sourced by the
deploy, rollback, smoke, install, backup and grants scripts, is only a self-check of NetSec's own constants at
preflight: no real target passes through its denylist, and its Docker prefix `compose` is the removed Ayurveda
stack's, not PadhAI's `padhai-postgres*`.

## NetSec layout

| Unit | Listens | What |
|---|---|---|
| `netsecai-postgres.service` | 127.0.0.1:55432 | PostgreSQL 16 + pgvector in Docker (`/opt/netsecai/shared/postgres/docker-compose.yml`; project `netsecai-postgres`, network `netsecai_postgres_network`, volume `netsecai_postgres_data`, database `netsecai`); not part of releases. NetSec's only container |
| `netsecai-api.service` | 127.0.0.1:4400 | FastAPI from `/opt/netsecai/app/backend`; `MemoryMax=900M`; never migrates on start; `GET /health` reports its commit |
| `netsecai-web.service` | 127.0.0.1:4300 | Next.js from `/opt/netsecai/app/frontend/apps/app` on the pinned Node (`/opt/netsecai/runtime`); reaches the API through `API_BASE_URL`; `GET /api/health` reports its commit (loopback only) |
| `netsecai-cloudflared.service` | outbound | `cloudflared tunnel --url http://127.0.0.1:4300`: a Quick Tunnel to the web app only. Installed by `install-units.sh`, not enabled by any script |
| `netsecai-pg-backup.timer` | – | nightly `pg_dump` at 03:15 to `/var/backups/netsecai` (without the rows of `file_chunks`, `auth_sessions`, `auth_verifications`), 7 days kept; the same service runs before every migration |
| `netsecai-jobs.timer` | – | the jobs worker every minute; installed, **not enabled** |
| `netsecai-deploy-*` | – | transient units: one deploy or rollback, detached from SSH, one at a time |
| `netsecai-extract-*` | – | transient units started by the API: one uploaded file read in a sandbox (no network, no secrets); needs Tesseract (`eng`, `hin`) |

Database roles: `netsecai_migrator` (owner; migrations and backups only), `netsecai_runtime` (the API),
`netsecai_auth` (the web app's sign-in, identity tables only). The database carries an identity marker
(`NETSECAI_DB_IDENTITY`) that every migration, grant, backup and status check verifies first.

```
engineer ──► Cloudflare Quick Tunnel ──► netsecai-cloudflared ──► web :4300 ──(loopback)──► API :4400 ──► PostgreSQL :55432
                                                                    └──── both from the checkout /opt/netsecai/app
```

```
/opt/netsecai/app.git            the production Git repository (deploys push here; the host has no GitHub access)
/opt/netsecai/app                the production checkout: the code that runs, its .venv, node_modules and builds
/opt/netsecai/app-state          content keys of what each part runs, `previous`, `deployed`, HISTORY
/opt/netsecai/runtime/           pinned Node runtimes
/opt/netsecai/shared/postgres/   compose file, backup.sh, database-grants.sh (installed once; never part of a deploy)
/etc/netsecai/{api,web,postgres,migration}.env   settings and secrets (mode 600), never in git; templates: deploy-netsec/env/
/var/backups/netsecai/           the dumps
```

Nothing but the web app is reachable from outside the host, and only through the tunnel.

## Operations

```sh
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run status:netsec    # workstation: GitHub main vs production, health, tunnel, MATCH
bash /opt/netsecai/app/deploy-netsec/infra-check.sh          # on the host: status + loopback ports, database isolation, backups, disk, OCR
systemctl status netsecai-postgres netsecai-api netsecai-web netsecai-cloudflared
journalctl -u netsecai-api -f                                # JSON logs; never message content or keys
journalctl -u 'netsecai-deploy-*'                            # deploy logs
tail /opt/netsecai/app-state/HISTORY
```

Deploy and roll back from a workstation: [docs/deployment.md](docs/deployment.md), [docs/rollback.md](docs/rollback.md).
Restore a backup: the header of [`deploy-netsec/backup.sh`](deploy-netsec/backup.sh). Every production change needs
the owner's explicit authorization.

## Known limits

- One host, no staging, no replica: a host failure is an outage until restored from the nightly dump, and the dumps
  share the database's disk, unencrypted. Copying them off the host is not set up: whether to keep an encrypted
  off-host copy is an owner decision not yet made, and until then no dump leaves the host.
- The Quick Tunnel's address changes whenever cloudflared restarts, and sign-in fails until web.env and api.env
  follow it ([docs/deployment.md](docs/deployment.md#quick-tunnel-address-changed)). A named tunnel
  (`deploy-netsec/cloudflared/config.yml.template`, for `chat.netsecai.com`) is prepared but not in use.
- A deploy restarts only what changed: the web app or the API is unavailable for a few seconds; a reply being
  written at that moment is saved as cancelled.
- The API runs as one process per host (in-flight replies are tracked per process).
- The services run as root, and there is no alerting: status and `infra-check.sh` are run by hand.
