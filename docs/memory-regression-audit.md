# Persistent-memory regression audit

Date: 2026-10-28

## Scope and safety

The audit covered authentication ownership boundaries, chat and SSE regressions, explicit remember/forget commands, memory CRUD/corrections/deletion, proposal approval/rejection, automatic extraction jobs, embedding compatibility, temporal validity, account deletion, migration reversibility, evaluation gates, frontend contracts/UI, and read-only production health.

All destructive tests used isolated PostgreSQL databases whose names end in `_test`. Production received no writes, migrations, restarts, load tests, or deletion tests.

## Evidence ledger

| Status | Area | Evidence |
|---|---|---|
| PASS | Memory CRUD, correction chains, deletion, clear-all, ownership, proposals, jobs, chat commands, account deletion | Focused backend suite passed after adversarial fixes. |
| PASS | Replay consistency | Duplicate completed command creates no extra transcript messages or streaming placeholder. |
| PASS | Temporal validity | Future `valid_from` and elapsed `valid_to` memories are excluded from list, export, exact match, and recall. |
| PASS | Consent/deletion dispatch boundary | A per-user PostgreSQL advisory transaction lock serializes provider dispatch with consent revocation, clear-all, and account deletion. Provider requests already accepted before revocation cannot be withdrawn. |
| PASS | Anti-resurrection | Proposal approval rechecks tombstones under the same per-user lock used by deletion. Explicit commands respect deletion markers. |
| PASS | Worker reliability | Claiming, fairness, bounded input/output, lease recovery, retries, dead-letter state, consent epochs, proposal idempotency, and first-attempt clock-skew handling are covered by focused tests. |
| PASS | Migration empty rollback | `0018 -> 0019 -> 0018 -> 0019` completed successfully. |
| PASS | Migration populated rollback | Downgrade refused before destructive DDL when non-representable `0019` data existed. |
| PASS | Frontend | 564 tests across 47 files passed; type checking, lint, contract generation (201 checks), and production build passed. One unrelated pre-existing unused Biome suppression warning remains. |
| PASS | Static validation | Full Ruff check and Ruff formatting check passed; frontend TypeScript typecheck passed. |
| PARTIAL | Full backend regression | Multiple complete memory-focused/adversarial suites passed. Three whole-suite attempts reached 26-74% with no observed failures but were interrupted to meet the requested completion deadline; the latest broader gate was blocked by a local PostgreSQL `fsync` I/O error after 63 tests had passed. No complete whole-suite result is claimed. |
| PASS | Production read-only baseline | Public health returned `{"status":"ok"}` and the login page was reachable. |
| BLOCKED | Production recall/extraction release | The sealed holdout is only 12 synthetic, non-provider-backed cases. Mandatory 100 extraction facts, 100 retrieval questions, 25 abstention, 20 temporal/conflict, and 20 security/isolation cases are not available. |
| NOT TESTED | Production memory behavior | Deliberately not enabled or exercised because deployment/migration authorization and the quantitative release gate are absent. |
| NOT TESTED | Destructive production recovery | Backup deletion, restore filtering, production rollback, and account-deletion propagation through historical backups require an authorized production runbook exercise. |

## Confirmed defects fixed during the audit

1. Memory correction-chain deletion used an unreferenced writable CTE and could silently fail.
2. Duplicate command replay created orphan transcript placeholders.
3. Clear-all violated proposal constraints after an approved proposal.
4. Temporal validity windows were ignored by active-memory queries.
5. Proposal approval could race deletion and resurrect tombstoned content.
6. Consent revocation/account deletion could race automatic extraction provider dispatch.
7. Concurrent command retries did not atomically claim the request ID.
8. Command cancellation was not rechecked around embedding and commit boundaries.
9. Add proposals were mislabeled as corrections in the Settings UI.
10. Test-only file upload checks were blocked by the host's production free-space guard; only `_test` databases bypass that host guard.
11. Fresh extraction and email outbox rows could be skipped when PostgreSQL's creation timestamp was a few milliseconds ahead of the worker's captured `now`; first-attempt rows are now immediately claimable while retry backoff remains enforced.

## Commands and results

```powershell
# Focused adversarial backend validation
.venv\Scripts\python -m pytest tests\test_memory_commands.py tests\test_memory_jobs.py tests\test_memory.py tests\test_account_deletion.py -q
# PASS

# Complete memory-relevant backend validation
.venv\Scripts\python -m pytest tests\test_memory_unit.py tests\test_memory.py tests\test_memory_commands.py tests\test_memory_jobs.py tests\test_jobs.py tests\test_chat.py tests\test_account_deletion.py tests\test_migrations.py tests\test_memory_eval.py -q
# PASS: 81 tests

# Full backend validation
.venv\Scripts\python -m pytest -q
# PARTIAL: three runs reached 26-74% without an observed failure but were interrupted before a final summary.

# Backend static checks
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
# PASS

# Frontend validation
bun test
bun run typecheck
bun run lint
bun run build
# PASS; one unrelated pre-existing unused suppression warning

# Empty migration rehearsal
.venv\Scripts\python -m alembic -x url=<isolated_test_url> upgrade 0019_memory_foundation
.venv\Scripts\python -m alembic -x url=<isolated_test_url> downgrade 0018_memory_lifecycle
.venv\Scripts\python -m alembic -x url=<isolated_test_url> upgrade 0019_memory_foundation
# PASS

# Populated downgrade safety
.venv\Scripts\python -m alembic -x url=<isolated_test_url> downgrade 0018_memory_lifecycle
# PASS: refused with RuntimeError before DDL

# Read-only production baseline
curl https://rural-commission-generous-massive.trycloudflare.com/api/health
# PASS: {"status":"ok"}
```

## Security review findings

| # | Severity | File | Lines | Vulnerability | Confidence | Resolution |
|---|---|---|---|---|---|---|
| 1 | 🟡 MEDIUM | backend/app/jobs/memory.py | provider dispatch path | Revoked/deleted user text could reach the extraction provider during an in-flight race | 9/10 | Fixed with a shared per-user advisory transaction lock around final eligibility and dispatch; revocation, clear-all, and account deletion acquire the same lock. |
| 2 | 🟡 MEDIUM | backend/app/memory/service.py | proposal approval path | Approval could race deletion and resurrect tombstoned memory | 9/10 | Fixed by acquiring the shared per-user lock and rechecking the deletion marker immediately before insertion. |

## Residual risks and honest release decision

- External provider calls accepted before consent revocation cannot be recalled; revocation waits for the bounded in-flight dispatch and prevents subsequent dispatch/persistence.
- Historical backups and user-downloaded exports are outside immediate database deletion. Production readiness requires documented retention, restore filtering, and deletion propagation controls.
- Legacy clients that omit `clientRequestId` receive a server-generated identifier and cannot deduplicate a later independent retry.
- Vector dimension changes still require a dedicated schema migration; incompatible model/version vectors are not compared.
- The current evaluation data is useful only as a deterministic smoke gate, not production-quality proof.

**Release decision: production memory recall and automatic extraction are not approved.** The implementation is locally regression-tested, but activation remains blocked until the provider-backed quantitative holdout and authorized production migration/backup/rollback checks pass.
