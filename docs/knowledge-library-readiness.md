# Knowledge Library readiness ledger

This ledger is the acceptance record for the CCNA-first PDF Knowledge Library. It documents what the repository
proves, what remains gated, and which trust boundary each control protects. It does **not** authorize scanning
`F:\CCNA`, moving local books, sending book text to a provider, enabling a production worker, or deploying.

## Current decision record

| Area | Decision | Evidence / status |
|---|---|---|
| Operator access | PostgreSQL-backed `knowledge_admin_grants`; guarded grant/revoke command | Implemented; focused WSL PostgreSQL integration passed before final import regressions; final rerun is currently blocked by the stopped local WSL service |
| Source visibility | `owner_only`, `selected`, `global`; selected/global require approved distribution rights | Implemented in schema and retrieval source resolution |
| Original storage | PostgreSQL 1 MiB `document_file_blobs` rows | Implemented; local 50 MiB spike passed first-response target; production WAL/vacuum/backup evidence pending |
| Import | Short-lived hashed batch token, sequential offsets, declared SHA-256, finalize verification | Implemented API and Windows importer; client inventory/containment/resume/secret-safety tests pass |
| Processing identity | source ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ document edition ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ immutable file ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ revision ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ stable revision chunk | Implemented additive schema; migration execution pending WSL test database |
| Extraction contract | NDJSON v2, 64 MiB aggregate, 1 MiB line, 256 KiB content, page provenance, strict keys/digests | Implemented and database-free tests pass |
| Parser | PDFium/Tesseract is the active bounded parser; Docling remains an optional gated enhancement | Digital and scanned synthetic PDFs pass locally (scanned fixture: 1 OCR page, 115 characters); production install and sandbox checks already provision Tesseract eng+hin |
| Publication | Revision staging copied atomically into live `document_chunks` | Implemented staging/publication foundation; deterministic unit tests pass; production activation remains gated |
| PDF viewing | Admin-only PDF stream; single/open/suffix ranges and safe headers; BFF response streams | Implemented foundation; backend integration test pending |
| Citations | Historical revision/stable chunk/page/section/excerpt fields survive message metadata | Implemented projection and database-free test passes |
| Rights and egress | Possession is not distribution permission; processing reuses the application File Library embedding provider | Existing `build_embeddings`/redaction/model-dimension controls are reused; operator authorization in this session approves this provider for the owner-only CCNA import |
| Recovery | 24-hour RPO plus backup after completed batches; 8-hour RTO target | Worker stale-requeue foundation implemented; post-batch production backup, encrypted off-host destination/key, and measured 8-hour restore remain BLOCKED external operations |
| Production | Dedicated knowledge worker lane, concurrency one | Implemented with durable claim/lease/retry/stale-recovery logic and installed-but-disabled systemd timer; enabling requires explicit production authorization |

## Synthetic fixture coverage

The generator at `backend/tests/fixtures/knowledge/generate.py` creates original CC0 test PDFs and a SHA-256
manifest. It covers:

- digital text, two columns, tables, figures/captions and IOS-like configuration;
- physical versus printed page labels;
- an untrusted prompt-injection string inside reference content;
- scanned/image-only OCR input;
- same-subject, different-edition inputs with different byte hashes.

No real vendor book, certification guide, or `F:\CCNA` file is included.

## Acceptance evidence

| Invariant | Reproducible check | Current result |
|---|---|---|
| Strict extraction rejects unknown versions/keys, bad sequence/page/digest and truncation | `backend/tests/knowledge_protocol_unit.py` | PASS on Windows Python 3.12 |
| Citation metadata retains immutable revision/page identity without URI/score leakage | `backend/tests/knowledge_citations_unittest.py` | PASS on Windows Python 3.12 |
| Range parser accepts one bounded range and rejects multiple/invalid ranges with 416 metadata | `backend/tests/knowledge_range_unittest.py` | PASS on Windows Python 3.12 |
| Synthetic fixtures regenerate with matching manifest digests | `backend/tests/knowledge_fixtures_unittest.py` | PASS on Windows Python 3.12 |
| Windows importer inventory, containment, resume/finalize and secret-safe errors | ackend/tests/import_windows_folder_unittest.py | PASS on Windows Python 3.12 |
| Worker claim, stale recovery, retry and fencing state transitions | ackend/tests/test_knowledge_worker.py | Static checks PASS; PostgreSQL execution pending restoration of local WSL service |
| BFF streams file responses and forwards Range/If-Range while filtering response headers | `frontend/apps/app/src/lib/server/proxy-core.test.ts` | PASS, 43 tests |
| Models, migration, access controls, import API, worker and retrieval work on PostgreSQL/pgvector | focused backend pytest | PASS: 44 tests in WSL PostgreSQL/pgvector after WSL recovery |
| Migration upgrade/downgrade and model parity | `test_migrations.py`, `test_foundation.py` | PASS in prior fresh WSL PostgreSQL run |
| PostgreSQL blob first-response p95 ÃƒÂ¢Ã¢â‚¬Â°Ã‚Â¤500 ms at concurrency 1/3 | `benchmark_knowledge_storage.py`, local WSL PostgreSQL 16, 50 MiB synthetic file | PASS: 32.21 ms / 66.07 ms; write 18.72 MiB/s. Production WAL/vacuum/backup impact remains PENDING |
| Current parser baseline on synthetic PDFs | `evaluate_knowledge_parser.py` | PASS: digital 2/1/1 pages (538/57/95 chars); scanned OCR 1 page, 115 chars, 1 OCR page |
| Docling version/model hashes/licenses/RSS/CPU/scratch/sandbox compatibility acceptable | parser gate | BLOCKED; Docling not installed or approved |
| Real-book embedding provider approval | owner authorization and existing application embedding configuration | APPROVED for this owner-only CCNA import; the worker reuses the same `build_embeddings` provider as chat File Library and never reads a root credential file |
| Off-host encrypted backup and restore within 8 hours | recovery drill | BLOCKED on destination/key and authorized evidence |

## Trust boundaries and failure policy

- **Browser/BFF/API:** the BFF allowlist remains narrow; only safe file response headers cross back to the browser.
- **Importer/API:** batch secrets are random, stored only as SHA-256, scoped to one source and expire after 12 hours.
- **User/source:** retrieval resolves visibility and grants in PostgreSQL; caller-provided source names only narrow.
- **PDF/parser:** extraction output is untrusted and must pass the strict NDJSON parser before persistence.
- **Knowledge/model provider:** content egress is separate from secret masking and remains blocked without approval.
- **Revision/live index:** incomplete revisions never enter `document_chunks`; publication must be one transaction.
- **Citation/current revision:** a stored citation must never be silently rebound to a newer revision.
- **Backup/restore:** an archive is not recovery evidence until a restore drill succeeds.

Any failed digest, protocol, rights, source grant, quality, storage, backup or provider-policy gate fails closed and
requires operator review. Similarity or visual duplicate signals never authorize deletion; quarantine and later purge
remain separate owner-approved operations.

## Final local validation status

- Backend Ruff, format and compile checks: **PASS**.
- Database-free Knowledge Library tests: **PASS** (16 tests).
- Import protocol regressions were added for exact non-final chunk sizing, same-batch resume, active-batch conflict, expired replacement, ready-file reuse, idempotent finalize and cumulative batch limits.
- Frontend typecheck, lint, unit tests and production build: **PASS**; focused BFF streaming/Range/concurrency tests: **PASS** (43 tests).
- Contract generation/tests: **PASS** (216 tests).
- Deploy namespace/safety tests and Graphify tests passed before the local WSL service failure (58 and 6 tests respectively). A later rerun timed out only because WSL commands were no longer starting.
- Final independent code review: **PASS**, no significant unresolved issue after fixes.
- Final PostgreSQL-focused regressions: **PASS** (44 tests) after safely restarting WSL; migration/model parity/import/worker/retrieval all passed.
- Final security gate on the dirty worktree: **PASS check-only**. Semgrep 0 findings, released-history/tree gitleaks 0 findings, pip-audit 0 vulnerabilities, Bun audit and zizmor PASS. A clean committed tree still needs a second run to write the deploy receipt.
- Commit/push/deploy: **NOT PERFORMED**. Production mutation was not authorized.