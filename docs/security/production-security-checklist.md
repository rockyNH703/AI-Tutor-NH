# Production security checklist

> NetSec AI production: an Ubuntu VM shared with PadhAI production, `/opt/netsecai`, units `netsecai-*`, web on
> `127.0.0.1:4300`, API on `127.0.0.1:4400`, PostgreSQL on `127.0.0.1:55432`, public through a Cloudflare Quick
> Tunnel. Written 2026-10-29 from the scripts in `deploy-netsec/`. Apart from the read-only probes listed in
> [security-audit.md, "Method"](security-audit.md#method), none of these checks was run on the host while writing it.
> **Production changes need the owner's explicit approval**; the read-only checks below don't.
> Never print an env file or a secret: check presence with `grep -c` and modes with `stat`.

## Contents

- [Release acceptance](#release-acceptance)
- [Live probes (benign)](#live-probes-benign)
- [Secrets on the host](#secrets-on-the-host)
- [Rotating a key](#rotating-a-key)
- [Backups and the restore drill](#backups-and-the-restore-drill)
- [Sign-in configuration](#sign-in-configuration)
- [CI](#ci)
- [Periodic checks](#periodic-checks)
- [Open follow-ups](#open-follow-ups)

## Release acceptance

For every release, in order. Stop at the first failure: fix it, or `bun run rollback:netsec`.

1. **The commit is on GitHub `main`** and the checkout is clean (`git status` shows nothing).
2. **The gate passed this commit:** `bun run security` on the clean tree ends in PASS and writes
   `.git/netsec-security-gate` with `commit=<sha>` and `result=PASS` ([security-tools.md](security-tools.md)).
   `.git/netsec-security-gate.overrides` has no new line, or the release notes give the reason.
3. **Deploy** (with the owner's approval): `NETSEC_DEPLOY_HOST=<user@host> bun run deploy:netsec`. It refuses a
   commit without a PASS receipt **(released 0d868b7)**, then must end with `Status: MATCH`.
4. **Status:** `NETSEC_DEPLOY_HOST=<user@host> bun run status:netsec` exits 0, and shows
   ([`server/status.sh`](../../deploy-netsec/server/status.sh)):
   - `Status: MATCH` and every unit healthy;
   - `tunnel https://<host> is NEXT_PUBLIC_SITE_URL` (the Quick Tunnel's address changes when cloudflared
     restarts; [deployment.md, "Quick Tunnel address changed"](../deployment.md#quick-tunnel-address-changed));
   - `api.env PUBLIC_APP_URL is NEXT_PUBLIC_SITE_URL`;
   - `no stale release listening`;
   - the Google and Turnstile lines, as expected.
   `bun run smoke:netsec` runs the same status check.
5. **After any change to the extractor, its sandbox, systemd or the host's layout:** on the host, as root,
   `cd /opt/netsecai/app/backend && .venv/bin/python scripts/sandbox_probe.py`. Every line must pass: the PDF and
   OCR extractions work, and the sandbox cannot read `/etc/netsecai/*.env`, `/opt/netsecai/app-state`,
   `/opt/netsecai/shared`, `/var/backups/netsecai`, PadhAI's `/etc/aichat` and `/opt/aichat`, `/etc/cloudflared`
   or `/var/lib/docker`, nor reach the network ([`sandbox_probe.py`](../../backend/scripts/sandbox_probe.py)). It
   starts short-lived sandbox units and writes nothing else. **Not yet run for the F-FILES-1 fix.**
6. **After any change to what the model is told:** `eval_engineering.py` on the host (the command is in its
   docstring; it uses the production key's quota). Compare with the previous run; the baseline at `e439b96` was
   37/57, FAIL.
7. **The [live probes](#live-probes-benign)**, and a live check of what the release changed.
8. **Report only what is live:** the deployed SHA, MATCH, the probe results, what was proven, and what was NOT
   TESTED.

## Live probes (benign)

From the workstation, against the public address only. Each is one ordinary request, needs no account, and sends
nothing a browser could not. Never run scanners or load tests against production ([SECURITY.md](../../SECURITY.md)).

```sh
SITE=https://<host>    # the current Quick Tunnel address, as status:netsec prints it

# Security headers: CSP, HSTS, X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy
curl -sI "$SITE/login" | grep -iE '^(content-security-policy|strict-transport-security|x-frame-options|x-content-type-options|referrer-policy|permissions-policy):'

# No session: 401, the API is never called
curl -s -o /dev/null -w '%{http_code}\n' "$SITE/api/v1/conversations"

# A mutation from another origin: 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Origin: https://example.invalid' \
  -H 'Content-Type: application/json' -d '{}' "$SITE/api/v1/chat/stream"

# Not on the allowlist: 404 (the internal routes are never proxied)
curl -s -o /dev/null -w '%{http_code}\n' "$SITE/api/v1/internal/auth/events"

# A-17, once released: the redirect target is dropped (Location is $SITE/login?reason=expired, no redirect=)
curl -s -o /dev/null -w '%{redirect_url}\n' "$SITE/api/session/expired?redirect=/.//example.invalid"

# F-BFF-1 (fixed): the Location must stay on $SITE. The tunnel passes the header through, so example.invalid here is a regression
curl -s -o /dev/null -w '%{redirect_url}\n' -H 'X-Forwarded-Host: example.invalid' "$SITE/api/session/expired"
```

On the host (read-only):

```sh
ss -ltnH | awk '{print $4}' | grep -E ':(4300|4400|55432)$'   # each on 127.0.0.1 only
docker version --format '{{.Server.Version}}'                  # A-23: 28 or later
```

In a browser, once A-0 is released and Google is configured: start "Continue with Google" and check in the
developer tools that the state cookie is `__Host-netsecai.state`.

## Secrets on the host

```sh
stat -c '%a %U:%G %n' /etc/netsecai/*.env              # each: 600 root:root
stat -c '%a %U:%G %n' /var/backups/netsecai            # 700 (backup.sh:46)
test -e /opt/netsecai/app/backend/.env && stat -c '%a %U:%G %n' /opt/netsecai/app/backend/.env || echo "no backend/.env"
```

- The last line matters because pydantic reads `backend/.env`, and the extractor sandbox binds the backend
  directory read-only (an uncovered note of the audit). Expect "no backend/.env".
- No secret in `ps` during a deploy: the smoke cookie and the role passwords travel in environments **(working
  tree)**.
- LangSmith tracing must be off: `grep -cE '^(LANGCHAIN_TRACING_V2|LANGSMITH_TRACING)=true' /etc/netsecai/api.env`
  prints `0`.
- The switches that change what data leaves or is kept hold no secrets, so their values may be shown:
  `grep -E '^(MEMORY_ENABLED|MEMORY_RECALL_ENABLED|MEMORY_AUTOMATIC_PROPOSALS|INTENT_ROUTER|STUDY_SCOPE|VOICE_MODE|JOBS_ENABLED|NOTIFICATIONS_MODE)=' /etc/netsecai/api.env`.
  Any that is on was approved by the owner.

## Rotating a key

Each rotation is a production change: the owner approves it, it is done on the host without printing values
(append with `printf 'NAME=%s\n' "$(openssl rand -base64 48)" >> …` after removing the old line), and the unit
that reads it is restarted. The procedures are in [deployment.md, "Sign-in (Better Auth)"](../deployment.md#sign-in-better-auth).

| Secret | Effect of rotating | Restart |
|---|---|---|
| `BETTER_AUTH_SECRET` (`web.env`) | Every session ends; Google sign-ins in progress fail once | `netsecai-web` |
| `AUTH_INTERNAL_TOKEN` (`api.env` and `web.env`, the same value) | Codes and notices stop until both run the new value | `netsecai-api` and `netsecai-web` together |
| `SECRET_KEY` (`api.env`) | Pending email codes and every unsubscribe link already sent stop working | `netsecai-api` |
| Database role passwords (`postgres.env`, with the URL in `api.env`, `web.env` or `migration.env`) | Every deploy runs `database-grants.sh`, which sets the runtime and auth passwords from `postgres.env` ([`update.sh:218-221`](../../deploy-netsec/server/update.sh)). Change the password and its URL together, apply, restart the unit that uses it, and prove the old password is refused. Not rehearsed for NetSec | The unit using it |
| Provider keys (LLM, embeddings, Ollalink, email) | Replace at the provider, then in `api.env`; revoke the old key | `netsecai-api` |
| Google and Turnstile secrets (`web.env`) | Create the new one, switch, then delete the old one | `netsecai-web` |
| The GitHub token on the workstation | Revoke at GitHub; replace locally | — |

After a leak: rotate, then check the provider's usage and the logs for misuse ([SECURITY.md](../../SECURITY.md#if-a-secret-is-exposed)).

## Backups and the restore drill

- **Nightly:** `systemctl list-timers netsecai-pg-backup.timer` shows the next run (03:15 plus up to 5 minutes);
  the newest `/var/backups/netsecai/netsecai-*.dump` is under 26 hours old. One more is taken before every
  migration ([`update.sh:196-209`](../../deploy-netsec/server/update.sh)).
- **What a dump holds:** everything except `file_chunks`, `auth_sessions` and `auth_verifications`
  ([`backup.sh`](../../deploy-netsec/backup.sh)). It is **not encrypted** and stays **on the same host**, kept 7
  days: a host compromise or loss takes the backups too (follow-up below).
- **Restore drill** (every three months, with the owner's approval; **none is on record for NetSec**): restore the
  newest dump into a separate, clearly named database in the existing container (for example
  `netsecai_restore_drill`), never into `netsecai`, using the `pg_restore` command in the header of `backup.sh`
  pointed at it. Check row counts of `users`, `conversations`, `messages`, `user_files` and `memory_items`
  against the live database, then drop it. Start no new container (the owner's rules) and never copy a dump off
  the host (off-host copies are an owner decision not yet made). First confirm with the owner that a second database does not trip the isolation checks in
  `backup.sh` and `status.sh`.

## Sign-in configuration

Presence only, never values:

```sh
grep -c '^TURNSTILE_SITE_KEY=.' /etc/netsecai/web.env     # 1 when configured
grep -c '^TURNSTILE_SECRET_KEY=.' /etc/netsecai/web.env   # 1 when configured
grep -c '^GOOGLE_CLIENT_ID=.' /etc/netsecai/web.env       # 1 when configured
grep -c '^GOOGLE_CLIENT_SECRET=.' /etc/netsecai/web.env   # 1 when configured
```

`status:netsec` prints the same two facts. Turnstile raises the cost of spending another address's hard limits
but does not stop it (A-1). With Google, the redirect URI must follow the Quick Tunnel's address
([deployment.md, "Sign-in (Better Auth)"](../deployment.md#sign-in-better-auth), "Google sign-in for NetSec").

## CI

GitHub Actions do not run for this repository (the account is out of credit). `.github/workflows/ci.yml` is kept
current and pinned by SHA, but it enforces nothing: report CI as **NOT RUN**. The enforced check is the local gate
and its receipt.

## Periodic checks

| When | Check |
|---|---|
| Every release | [Release acceptance](#release-acceptance) |
| Weekly | `bun run status:netsec`; the newest backup's age; the web log's `[auth] failed` and `[auth] limited` lines for spikes; the providers' usage for anything unexpected |
| Monthly | `bun run security` on `main` (new advisories appear without code changes); `.git/netsec-security-gate.overrides`; the [secrets checks](#secrets-on-the-host); the [live probes](#live-probes-benign) |
| Every three months | The restore drill; the full audit ([secure-development.md](secure-development.md#which-review-when)); the status of every open follow-up below |
| After a Docker, kernel or systemd update on the host | `sandbox_probe.py`; `docker version` (A-23) |

## Open follow-ups

From the [2026-10-29 audit](security-audit.md) and its triage. `code` is a change in this repository; `owner` is
the repository owner (host, production configuration or account).

| Follow-up | Ref | Owner |
|---|---|---|
| Done 2026-10-29: the audit's fixes (A-0, A-4/A-11/A-15, A-6 and its body deadline, A-13, A-17, A-22, A-28's pin, A-30, F-MEM-1 and the memory approval parsing, F-FILES-1/2, F-DEPLOY-1, F-AUTH-1, the masking, the dependency upgrades, the gate) released in `0d868b7`, `Status: MATCH` | — | — |
| Clear the 8 moderate `bun audit` advisories that come through `@sentry/nextjs` 8 (OpenTelemetry instrumentation, `uuid`; `uuid` also through mermaid). The gate lists them without failing | Gate | `code` |
| Apply A-28's pin on the host: back up, copy `deploy-netsec/compose/docker-compose.yml` into `/opt/netsecai/shared/postgres`, restart ([deployment.md](../deployment.md#changing-the-database-container-on-purpose)). Deploys never do it | A-28 | `owner` |
| Run units as non-root users, starting with the web app (`User=netsecai-web`, `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, an empty `CapabilityBoundingSet`); then the API and jobs | A-21 | `code` + `owner` |
| Harden `netsecai-web.service` (today only `MemoryMax`); the API and jobs units have `NoNewPrivileges` and `PrivateTmp` only | A-21 | `code` + `owner` |
| Install Bun from the pinned release zip with a SHA-256 kept in the repository, not `curl \| bash`; commit Node's tarball SHA-256 | A-24, A-27 | `code` |
| NetSec's own Bun under `/opt/netsecai/runtime`, not the shared `/root/.bun` | A-26 | `code` |
| Hash-pin the workstation's Python tools; `--only-binary=:all:` in the production install | A-29 | `code` |
| Key the new-device alert on a server-issued device id, not the User-Agent | A-31 | `code` (migration) |
| Stop others spending an address's hard limits; configure Turnstile | A-1 | `code` + `owner` |
| Sign out other sessions when two-step sign-in is turned on or off | A-2 | `code` |
| Take the per-user memory lock in the chat "remember" and "forget" commands | A-14 | `code` |
| A `lock_timeout` on API memory locks; keep the worker's provider call outside the lock | A-16 | `code` |
| Decode avatars in the sandbox, or at least `Image.open(..., formats=[expected])` | A-32 | `code` |
| A regression test that a failed generation's log carries no provider text (A-13 is fixed in code) | A-13 | `code` |
| A per-user lock for the chat quota | A-10 | `code` |
| A per-user upload cap in the BFF (the body deadline frees a slot after 300 s, but one account can hold both) | A-6 follow-up | `code` |
| Stream file downloads, with a delivery deadline | A-7 | `code` (after validation) |
| Decide whether to keep an encrypted copy of the backups off the host (an owner decision not yet made; until then no dump leaves the host, and the rules forbid copying production data to a laptop) | Backups | `owner` |
| The deploy gate's limits: a rollback to any ancestor of `main` is ungated and unlogged; rollback runs the local `HEAD`'s `deploy-netsec/` tooling; overrides are logged only in the local `.git`; the receipt scans the working tree ([known-limitations.md](../known-limitations.md#netsec-ai-2026-10-29)) | Gate | `code` |
| Replace CSP `'unsafe-inline'` for scripts with nonces | CSP | `code` |
| Move the notifications cross-user case into `test_authorization.py` | A-5 | `code` |
| Validate: response backpressure (A-7), Docker version (A-23) | A-7, A-23 | `owner` |
| Done 2026-10-29: `sandbox_probe.py` on the host after the F-FILES-1 release, 12/12 PASS (blocks the secrets, PadhAI, the tunnel, Docker and the network) | F-FILES-1 | — |
| A named tunnel on an owned domain instead of the Quick Tunnel (removes the shared parent domain and the address drift) | A-0, B8 | `owner` |
| Confirm the old GitHub token gitleaks found in local `refs/agents/*` is revoked, then delete those refs (never print them) | Gate | `owner` |
