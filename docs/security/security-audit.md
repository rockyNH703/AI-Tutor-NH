# Security audit, 2026-10-29

> A read-only source audit of NetSec AI's surviving surfaces, using the Cloudflare security-audit methodology.
> Results are taken from the audit's findings file and its evidence ledger (kept outside the repository), and each
> fix was checked in the working tree on top of `e439b96` when this was written, and again after the final review
> the same day. **FIXED (working tree)** means the fix and its regression test exist (unless the row says there is
> no test) but were not committed or released: production does not have them yet.
> This report does not claim that NetSec AI is secure.

## Contents

- [Summary](#summary)
- [Method](#method)
- [Scope and exclusions](#scope-and-exclusions)
- [Coverage](#coverage)
- [Findings](#findings)
- [Rejected candidates](#rejected-candidates)
- [Needing validation](#needing-validation)
- [Earlier triage (F-*)](#earlier-triage-f-)
- [Other security work in the same pass](#other-security-work-in-the-same-pass)
- [What this audit is not](#what-this-audit-is-not)

## Summary

- **33 candidates** from 8 areas and a completeness critic: **24 confirmed, 2 need validation, 7 rejected**.
- **Severity after verification** of the confirmed ones: 0 critical, 0 high, **2 medium** (A-17, A-30), 13 low,
  9 info. The evidence ledger's summary line says "11 low, 11 info"; counting each finding's verifier severity in
  the findings file gives 13 and 9, and this report uses the findings file.
- **Kind:** 6 of the confirmed are vulnerabilities (A-0, A-1, A-6, A-17, A-30, A-31); the other 18 are
  hardening. A-7, a vulnerability candidate, needs validation. A-4, A-11 and A-15 are one issue found by three
  areas; A-24 and A-27 overlap.
- **Fixed in the working tree, each with a regression test:** A-0, A-4 (with A-11 and A-15), A-6 and its
  body-deadline follow-up, A-17, A-22, A-30, and the earlier F-MEM-1, F-FILES-1, F-FILES-2, F-DEPLOY-1 and F-AUTH-1.
  **Fixed without a test:** A-13. **Pinned in the repository, not yet applied on the host:** A-28.
- **Also fixed in the working tree since:** memory proposal approval reads consent defensively (as F-MEM-1's
  worker does), and the dependency audits are clean of high findings ([below](#other-security-work-in-the-same-pass)).
- **No cross-user data path** survived verification in the areas that looked for one (api-ownership,
  llm-rag-context, memory-lifecycle).

Numbering: `A-<n>` is the finding's position (from 0) in the findings file.

## Method

The vendored `security-audit` skill (Cloudflare, MIT, `c1c8a8c`; [security-tools.md](security-tools.md)):

1. **Recon:** the attack surface split into areas, each with a coverage ledger of units (entry points, trust
   transitions, sinks).
2. **Hunters:** one read-only agent per area (Read, Grep and Glob only), coverage-led: every unit is traced or
   listed as uncovered with a reason. A candidate needs a principal who gains something across a trust boundary;
   a missing best practice with no affected principal is a hardening note.
3. **Completeness critic:** a separate agent that looks for what the hunters missed (A-30, A-31, A-32).
4. **Fresh verifiers:** for each candidate, a new agent with no stake in it tries to refute every link of the code
   path, then gives a verdict (confirmed, needs_validation, rejected) and a severity. High and critical candidates
   need three votes; none reached that level, so every finding had one verifier.
5. **Report:** this page, with each fix checked in the working tree.

In all, 42 agents ran (evidence ledger). [Graphify](../graphify.md) helped choose what to read. Hunters ran no
code. **What ran against production during the audit work** (read-only, changing nothing in production):
- a length-only probe of passing an environment variable into the NetSec PostgreSQL container (`docker exec -e`),
  and a psql `\getenv` probe in that container (A-22's mechanism, in an owner-authorized session; no value
  printed);
- `docker inspect` of the running PostgreSQL image, for its digest (A-28);
- `git rev-parse` of the production checkout `/opt/netsecai/app`;
- the baseline behaviour eval [`eval_engineering.py`](../../backend/scripts/eval_engineering.py), run on the host
  with the API's own settings (`api.env`): it sent its synthetic cases to the production model and wrote nothing but
  its output.

## Scope and exclusions

**In scope:** what survives the move from PadhAI: sign-in and sessions (the BFF and Better Auth's configuration),
API ownership, files and the extractor sandbox, the LLM, retrieval and context path, persistent memory, the web
client and its renderer, the NetSec deploy (`deploy-netsec/`) and the supply chain and CI.

**Excluded:**
- Code being adapted from school to engineering at the time: `app/{qbank,assessments,progress,study,greetings,intents,learning}`,
  `app/chat/assistant.py`, `app/chat/teaching.py`, `app/rag/ncert.py`. So the system prompt's contents, the intent
  and study-scope classifiers, and the question-bank widgets were not reviewed line by line.
- `backend/app/llm/redact.py`, which was new and in progress (it has its own tests; see below).
- Problems already in the earlier triage (F-*), which were not reported again.
- PadhAI's `deploy/` scripts, apart from what NetSec shares with them.

## Coverage

| Area | Units | Not covered (summary of the hunters' notes) |
|---|---|---|
| auth-sessions | 27 | `X-Forwarded-Host` trust (F-BFF-1); `main.py` wiring and `avatar.py` (later covered by the critic); Better Auth internals (enumeration timing, session rotation) trusted; deployment facts (sibling hosts, HSTS preload, Cloudflare WAF rules, whether Turnstile is set); email HTML builders by grep only |
| api-ownership | 16 | Code being adapted; BFF path handling; memory settings keys (F-MEM-1); the memory worker beyond its foreign keys; Better Auth's own tables. Nothing executed |
| files-sandbox | 17 | Cloudflare edge buffering and timeouts (decides A-6 and A-18 in production); parser memory safety inside the sandbox (treated as contained); middleware body cloning on `/api/auth/*` and voice routes. Nothing executed |
| llm-rag-context | 22 | Code being adapted (the prompt's contents, the classifiers, NCERT); voice speech budgets skimmed; `redact.py`; provider-side behaviour (error echo, retention); production settings (workers, memory switches). Verified safe: tenant isolation in retrieval, attached files, memory recall, knowledge-base writes only by the CLI, fenced file and memory blocks, output handling |
| memory-lifecycle | 23 | PostgreSQL READ COMMITTED behaviour not observed in a `_test` database; F-MEM-1; account deletion races (no orphan found); backups and providers' retention; BFF memory routes and the memory tests. Verified: every memory read and write path is per user, with composite foreign keys |
| bff-client | 24 | URL normalisation behind A-17 (later proven by a failing test); Cloudflare buffering; mermaid, DOMPurify, KaTeX and pdf.js versions newer than the hunter's knowledge (no CVE check); Next.js behaviour from source, not run; quiz and card widgets by grep; voice client internals |
| infra-deploy | 30 | Host facts only the owner can check: Docker Engine version and iptables (A-23), `/proc` `hidepid` and non-root accounts, the cloudflared metrics listener, whether an untracked `backend/.env` exists in `/opt/netsecai/app/backend`; PadhAI's `deploy/`; `.github/workflows`, the gate and `deploy-netsec/tests` |
| supply-chain-ci | 24 | Bun's default trusted-dependency list (which lifecycle scripts run as root); host facts; GitHub branch protection; how the gate's WSL tools were installed; CVE lookups (no network) |

## Findings

Severity is shown as the hunter's, then the verifier's when they differ. **Owner:** `code` is a change in this
repository (any developer, reviewed, with a regression test); `owner` is the repository owner (host, production
configuration or account).

| ID | Finding | Area | Severity | Kind | Verdict | Status |
|---|---|---|---|---|---|---|
| A-0 | Better Auth's OAuth state cookie was not `__Host-`, so a sibling host on the shared parent domain could plant it: Google login CSRF | auth-sessions | low | vulnerability | confirmed | **FIXED (working tree)**: `cookiePrefix` ([`config.ts:313`](../../frontend/apps/app/src/lib/auth/config.ts)); `config.test.ts` "cookies on a shared parent domain" (2 tests, 11/11 config tests) |
| A-1 | Per-address hard limits can be spent by anyone who knows the address: blocks code sign-in and reset for 24 h, password sign-in continuously (no CAPTCHA) | auth-sessions | low | vulnerability | confirmed | OPEN. Owner: `code` (count a code check only when a code is pending; risk-based password limit) and `owner` (configure Turnstile) |
| A-2 | Turning two-step sign-in on or off signs out only the current device | auth-sessions | info | hardening | confirmed | OPEN. `code`: delete the user's other sessions in the after-hook ([`config.ts:425-438`](../../frontend/apps/app/src/lib/auth/config.ts)) |
| A-4 | Chat "remember …" / "forget …" ignore the `MEMORY_ENABLED` kill switch (same as A-11, A-15) | api-ownership | low | hardening | confirmed | **FIXED (working tree)**: `memory_on = self.settings.memory_enabled` gates the command parser ([`chat/service.py:304-305`](../../backend/app/chat/service.py)); `test_memory_commands.py::test_with_memory_off_a_remember_command_is_an_ordinary_message` failed before the fix |
| A-5 | The only cross-user test for `/v1/notifications/read` and `/clear` is in `test_learning_streak.py` | api-ownership | info | hardening | confirmed | OPEN. `code`: move the case into `test_authorization.py` when the learning tests change |
| A-6 | The middleware matcher covered `/api`, so Next buffered every request body before the BFF's checks and cut uploads above 10 MB | files-sandbox | medium → low | vulnerability | confirmed | **FIXED (working tree)**: `api/` excluded ([`middleware.ts:42-49`](../../frontend/apps/app/src/middleware.ts)); `middleware.test.ts` failed first; 56/56 middleware and server tests. **Follow-up FIXED (working tree)**: `readBody` has a deadline, 30 s (`BODY_TIMEOUT_MS`) or 300 s on upload routes (`UPLOAD_TIMEOUT_MS`), then 408 `request_timeout` ([`proxy-core.ts:129-170, 293-304`](../../frontend/apps/app/src/lib/server/proxy-core.ts)); `proxy-core.test.ts` "body deadline" (3 tests). One account can still hold both upload slots for 300 s each |
| A-7 | The BFF frees a file-download slot before the buffered 25 MB response is delivered | files-sandbox | low | vulnerability | needs_validation | [Needs validation](#needing-validation) |
| A-10 | The per-user chat quota is check-then-insert without a per-user lock: parallel requests overshoot it | llm-rag-context | low | hardening | confirmed | OPEN. `code`: a per-user advisory lock in `_check_turn_allowed` ([`chat/service.py:206`](../../backend/app/chat/service.py)) |
| A-11 | Duplicate of A-4 (also: text is embedded and stored while memory is off) | llm-rag-context | low | hardening | confirmed | **FIXED (working tree)**, with A-4 |
| A-12 | A memory command's validation or embedding error escapes the generation task: the reply stays "streaming", the receipt "started" | llm-rag-context | info | hardening | confirmed | OPEN. `code` (reliability, same user only) |
| A-13 | A provider's error text, which may echo user input, is logged as `detail` | llm-rag-context | info | hardening | confirmed | **FIXED (working tree)**: the "llm generation failed" log carries `error_code` and `error_type` only; `"detail": str(error)[:300]` is gone ([`chat/service.py:962`](../../backend/app/chat/service.py)). No regression test |
| A-14 | Not every memory writer takes the per-user lock: clear-all, delete and consent-off are not linearizable with concurrent writes | memory-lifecycle | low → info | hardening | confirmed | OPEN. `code`: lock in the "remember" and "forget" commands (the verifier refuted the create and worker parts) |
| A-15 | Duplicate of A-4 | memory-lifecycle | low | hardening | confirmed | **FIXED (working tree)**, with A-4 |
| A-16 | The extraction worker holds the per-user lock across the provider call; API callers wait with no `lock_timeout` | memory-lifecycle | info | hardening | confirmed | OPEN. `code`. Only with automatic proposals and the jobs timer on (both off) |
| A-17 | Open redirect: `safeRedirect` returned the dot-segment-normalised path, so `/.//evil.com` became `//evil.com` | bff-client | medium | vulnerability | confirmed | **FIXED (working tree)**: the parsed path is re-checked ([`safe-redirect.ts:49-56`](../../frontend/apps/app/src/lib/safe-redirect.ts)); `safe-redirect.test.ts` failed first, 5/5 after |
| A-21 | The web app, API and jobs worker run as root, on a host shared with PadhAI production | infra-deploy | medium → low | hardening | confirmed | OPEN. `code` (units, a `netsecai-web` system user) and `owner` (approve and apply on the host). Start with the web unit |
| A-22 | The database role passwords were passed to `psql` as arguments on every deploy | infra-deploy | low | hardening | confirmed | **FIXED (working tree)**: `-e NAME` and `\getenv` ([`database-grants.sh:29-36`](../../deploy-netsec/database-grants.sh)); the mechanism (Docker `exec -e`, psql 16 `\getenv`) was checked read-only on the production host with a harmless length-only probe, in an owner-authorized session ([Method](#method)); static test in `files-sandbox.test.mjs`; `deploy-netsec/tests` 35/35 then (36 tests in the four files now) |
| A-23 | PostgreSQL published on `127.0.0.1:55432` may be reachable from LAN neighbours on Docker Engine below 28 | infra-deploy | low | hardening | needs_validation | [Needs validation](#needing-validation) |
| A-24 | Bun is installed on the host by `curl https://bun.sh/install \| bash` as root, with no checksum | infra-deploy | info | hardening | confirmed | OPEN. `code`: download the pinned release zip and check a SHA-256 kept in the repository ([`lib.sh:305-312`](../../deploy-netsec/server/lib.sh), `install.sh:85`) |
| A-26 | `/root/.bun` is shared: a NetSec deploy replaces the Bun binary PadhAI's live web unit and deploy run | supply-chain-ci | low → info | hardening | confirmed | OPEN. `code`: NetSec's own Bun under `/opt/netsecai/runtime` |
| A-27 | Runtime bootstrap trusts the download origin only (Bun unverified, `.bun-version` unvalidated; Node checked against SHASUMS from the same origin) | supply-chain-ci | low | hardening | confirmed | OPEN, with A-24. Also commit Node's tarball SHA-256 per version |
| A-28 | The production PostgreSQL image is a mutable tag (`pgvector/pgvector:pg16`); CI uses the same tag | supply-chain-ci | low | hardening | confirmed | **PINNED in the repository (working tree)**: `pgvector/pgvector:pg16@sha256:ccc6e83d…`, the digest production ran (read with `docker inspect`, [Method](#method)), in [`docker-compose.yml:14`](../../deploy-netsec/compose/docker-compose.yml) and CI (`ci.yml` lines 23 and 115). Deploys never replace the host's copy: production uses it only once the file is copied into `/opt/netsecai/shared/postgres` on purpose (`owner`, [deployment.md](../deployment.md#changing-the-database-container-on-purpose)) |
| A-29 | The release workstation and the lock tools resolve Python packages without hashes | supply-chain-ci | low | hardening | confirmed | OPEN. `code`: a hashed `requirements-dev.lock`; `--only-binary=:all:` in the production install |
| A-30 | An avatar decoded up to 40 MP at full size in the API process, twice at once: above `MemoryMax=900M`, so any user could get the API killed | critic | medium | vulnerability | confirmed | **FIXED (working tree)**: a 16 MP decoded budget after `draft()` ([`avatar.py:36-40, 84-86`](../../backend/app/auth/avatar.py)); `test_identity.py::test_formats_decoded_at_full_size_have_a_smaller_pixel_budget` failed first (a 20 MP PNG was accepted); 28/28 `test_identity.py` |
| A-31 | The new-device sign-in alert is keyed on a label from the User-Agent, which an attacker with stolen credentials chooses | critic | low | vulnerability | confirmed | OPEN. `code`: a server-issued device id (needs a migration); interim, compare the raw UA with the masked network |
| A-32 | Avatar parsing runs every Pillow plugin on untrusted bytes in the root API process (`Image.open` without `formats=`) | critic | info | hardening | confirmed | OPEN. `code`: `formats=[expected]`; optionally decode in the sandbox |

## Rejected candidates

Each was rejected by its verifier. Several leave a hardening note, listed with it.

| ID | Candidate | Area | Why rejected |
|---|---|---|---|
| A-3 | `POST /v1/voice/speech` speaks client-supplied text, not tied to the reply it names | api-ownership | No cross-user access; the per-user caps (120/min, 3000/day) are the real limit, the per-reply budget never was; the tests rely on this behaviour, and the fix would break voice mode |
| A-8 | The extractor's "environment error" requeue has no ceiling | files-sandbox | The intended, tested design; reachable in production only with code execution in the sandbox, a worse precondition. Note: cap consecutive requeues; treat signals as failures under the subprocess runner |
| A-9 | Retry bypasses `MAX_PENDING`; the global size ceiling does not drop after deletes | files-sandbox | Claims are fair across users, so no one else is affected; the ceiling protects the disk on purpose. Note: check pending in retry; document `VACUUM FULL` |
| A-18 | Two slow upload bodies hold both upload slots for everyone | bff-client | Next read the whole body (through the middleware) before the handler ran, so the slot was never held during a slow body. **After A-6's fix this precondition came back;** the A-6 follow-up's deadline now frees a slot after 300 s at most |
| A-19 | Mermaid's `htmlLabels` can be turned back on by an in-diagram directive | bff-client | No sanitiser bypass exists; the output is still sanitised. Note: `securityLevel: "sandbox"`; the CSP's `'unsafe-inline'` would not stop an inline handler |
| A-20 | Model-named code downloads accept any extension (`.bat`, `.ps1`, `.hta`) | bff-client | Crosses no boundary: the user must save and run the file. Note: append `.txt` to shell-executed extensions |
| A-25 | Deploy tooling comes from local `HEAD`; the rollback path skips the clean-tree and gate checks for it | supply-chain-ci | The attacker must already create commits in the operator's checkout, which is the operator's power. The working tree now gates a rollback to any commit that is not an ancestor of `main` ([`deploy.sh:93-99`](../../deploy-netsec/deploy.sh)); an ancestor is not gated and not logged, whether or not it was ever released. Note: archive the tooling from `MAIN`, not `HEAD` ([known-limitations.md](../known-limitations.md#netsec-ai-2026-10-29)) |

## Needing validation

| ID | What decides it | Validation plan | Owner |
|---|---|---|---|
| A-7 | Whether a slow client's backpressure reaches Node through Cloudflare's edge and cloudflared, or the edge absorbs the response | 1. Locally: a `bun test` around `proxyRequest` with a fake 25 MB `application/pdf` upstream, five requests whose bodies are never read; all five resolve, showing the three slots were released while ~125 MB is still held. 2. Owner-observed: a slow download of the user's own large file through the public hostname while watching the web unit's memory. If confirmed and sustainable, severity rises to medium. Fix either way: stream the upstream body (as SSE does) with a total delivery deadline | `code` (1), `owner` (2) |
| A-23 | The host's Docker Engine version and its `raw`/`DOCKER-USER` iptables rules | On the host, read-only: `docker version --format '{{.Server.Version}}'` (28 or later closes it) and `iptables -t raw -S PREROUTING`. If older without the rule: upgrade Docker or add `iptables -t raw -I PREROUTING ! -i lo -d 127.0.0.0/8 -j DROP` and a `DOCKER-USER` drop, and teach `infra-check.sh` to check it. PadhAI's compose publishes the same way | `owner` |

## Earlier triage (F-*)

Found before the audit, during the move to NetSec, and fixed or tracked separately. Not rated by a verifier.

| ID | Problem | Status and evidence |
|---|---|---|
| F-MEM-1 | `PATCH /v1/me/settings` accepted the memory consent keys, so a user could write malformed values that made chat and the jobs worker fail (`ValueError`), and the string `"false"` read as consent; one job's failure stopped the batch | **FIXED (working tree)**: reserved keys refused ([`auth/schemas.py:29-41`](../../backend/app/auth/schemas.py)), defensive parsing ([`preferences.py:23-66`](../../backend/app/memory/preferences.py)), per-job isolation ([`jobs/memory.py:111-115`](../../backend/app/jobs/memory.py)). 4 tests failed first; 31/31 after (`test_sessions_me`, `test_memory_jobs`, `test_memory_unit`, `test_memory`, `test_memory_commands`) |
| F-FILES-1 | The extractor sandbox hid PadhAI's paths (`/opt/aichat`, `/etc/aichat`), not NetSec's (`/opt/netsecai`, `/etc/netsecai`) | **FIXED (working tree)** ([`processing.py:49-54, 105-110`](../../backend/app/files/processing.py)). `test_files.py::test_the_sandbox_hides_this_deployment_and_its_secrets` failed first; 19/19 `test_files.py`. **Not verified on the host:** run `sandbox_probe.py` after the release |
| F-FILES-2 | The file worker paused on PadhAI's deploy marker, not NetSec's | **FIXED (working tree)**: `/run/netsecai-deploying`, written by [`update.sh:65-73`](../../deploy-netsec/server/update.sh); `files-sandbox.test.mjs` "it pauses while update.sh deploys" |
| F-DEPLOY-1 | The deploy's smoke passed `BETTER_AUTH_SECRET` to `openssl` as an argument | **FIXED (working tree)**: the secret goes through Python's environment ([`lib.sh:177-188`](../../deploy-netsec/server/lib.sh)); the old and new cookies are byte-identical (checked in WSL); `files-sandbox.test.mjs` 4/4 |
| F-AUTH-1 | Two parallel uses of one authenticator code could both pass (check, then record) | **FIXED (working tree)**: claimed before checking, under an advisory lock ([`guard.ts:57-69`](../../frontend/apps/app/src/lib/auth/guard.ts)); `config.test.ts` "an authenticator code works once" (4 tests, with a no-lock negative control); on real PostgreSQL 20 parallel claims gave exactly 1 pass; frontend 586/586 |
| F-BFF-1 | The BFF's public origin (CSRF check, redirects, the cookie's HTTPS decision) comes from `X-Forwarded-Host` and `X-Forwarded-Proto` ([`public-origin.ts:24-37`](../../frontend/apps/app/src/lib/server/public-origin.ts)) | OPEN, needs a live probe (`owner`): a benign request to the public hostname with `X-Forwarded-Host: example.invalid` on a route that redirects (for example `/api/session/expired`) shows whether the `Location` uses it. If it does, take the origin from `NEXT_PUBLIC_SITE_URL` instead |

## Other security work in the same pass

| Change | Evidence |
|---|---|
| Secret masking before every LLM, fallback and embeddings provider ([`redact.py`](../../backend/app/llm/redact.py), wired in `build_llm` and `build_embeddings`) | `test_redact.py` (222 cases), `test_redact_wiring.py` |
| SQL built from strings in `text()` became bound parameters (the lease interval in `processing.py`, the HNSW setting in `store.py`) | 71/71 in `test_files`, `test_rag_retrieval`, `test_rag_graph`, `test_chat`; Semgrep rule `netsec-sql-text-built-string` with a self-test |
| CI actions pinned by commit SHA, `persist-credentials: false`, Mailpit pinned by digest | zizmor: no findings. CI does not run (out of credit) |
| Dependency upgrades (certifi, dnspython, langgraph-sdk, Pillow 13) | pip-audit clean; a fresh `--require-hashes` venv and `pip check` pass; 212 backend tests pass (2 failures from in-progress profile work, both pass on a clean `HEAD` export); Pillow 13 smoke on avatars and the extractor |
| Frontend dependency audit fix: zod 3 → 4, `@t3-oss/env-nextjs` 0.13, turbo 2.9.18, overrides for postcss, rollup and lodash-es (`frontend/package.json`, `frontend/bun.lock`) | `bun audit`: 34 high at the gate's first run, none now; 8 moderate remain (OpenTelemetry instrumentation and `uuid` through `@sentry/nextjs` 8, `uuid` also through mermaid), listed by the gate without failing it. Sentry's build wrapper is applied only when `NEXT_PUBLIC_SENTRY_DSN` is set, and with no DSN nothing is sent. Deploys install with `bun install --frozen-lockfile --force` ([`update.sh:143`](../../deploy-netsec/server/update.sh)) so stale nested copies are dropped; `files-sandbox.test.mjs` "frontend dependencies" |
| Memory proposal approval reads consent with `MemoryPreferences.from_settings`, like the worker ([`proposals.py:60-72`](../../backend/app/memory/proposals.py)) | `test_memory_jobs.py::test_malformed_stored_consent_never_breaks_chat_or_other_users_jobs` approves a proposal over malformed stored consent (200, never a 500) |
| The PostgreSQL image pinned by digest in the repository and CI (A-28) | The digest was read read-only on the host with `docker inspect` ([Method](#method)) |
| The local security gate and its deploy receipt | `deploy-netsec/tests/security-gate.test.mjs`. First run: FAIL (Semgrep 26 findings, pip-audit 42 in 4 packages, `bun audit` 34 high). gitleaks found an old GitHub token in local `refs/agents/*` (never pushed): `owner` to confirm it is revoked and delete those refs |
| Graphify boundary (never reads secrets or excluded code) | `scripts/tests/graph.test.mjs` 6/6 (its first run caught a wrong recovery flag) |
| PadhAI's `deploy/` refuses to run from this repository | `deploy-netsec/tests/legacy-guard.test.mjs` |

## What this audit is not

- **Not a penetration test.** It is a read-only source review. The hunters and verifiers ran nothing; the failing
  tests and the race check above were run afterwards, when fixing. No probe or scan was sent to the public site;
  what ran on the production host is the read-only list in [Method](#method), and nothing in production was
  changed.
- **Not exhaustive.** Code being adapted at the time, and `redact.py`, were excluded; some areas were covered by grep
  only (listed in [Coverage](#coverage)). New code since then is not covered.
- **Not a review of dependencies.** Better Auth, Next.js, Pillow, the parsers and the other libraries were trusted,
  and read only where a candidate depended on them. No CVE lookup was done (the gate's pip-audit and `bun audit`
  cover published advisories).
- **Not a review of production.** No env file or secret value was printed (the A-22 probe in [Method](#method)
  printed a length only); other host facts (Docker version, local accounts, Cloudflare settings, which switches are
  on) were not observed, and are listed as needing validation where they matter.
- **One verifier per finding.** The three-vote rule applies to high and critical, and none reached that level.
  Severity is the verifier's judgement.
- **Not a statement that NetSec AI is secure.** It lists what was found, what was fixed, and what is left. Fixes
  marked "working tree" are not in production until they are committed, pass `bun run security`, and are deployed
  ([production-security-checklist.md](production-security-checklist.md)).
