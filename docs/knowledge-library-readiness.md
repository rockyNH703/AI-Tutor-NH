# Knowledge Library readiness ledger

This ledger is the acceptance record for the CCNA-first PDF Knowledge Library: what the repository proves, what
is still open, and which trust boundary each control protects. It does **not** authorize scanning `F:\CCNA`,
moving local books, sending book text to a provider, or deploying. Last verified: 2026-11-04 (release after
`8bfe95b`).

## State in production (2026-11-04)

- The schema is live: migrations 0023 (`0023_knowledge_library`) and 0024 (`0024_knowledge_lifecycle`) are
  applied. 0024 lets accounts that touched the library be deleted (actor columns `ON DELETE SET NULL`) and keeps the
  pre-0023 `ingest_documents.py --global` insert working.
- **No book has been imported:** 0 sources, 0 files, 0 revisions.
- `netsecai-knowledge.timer` is **enabled**: `deploy-netsec/server/install-units.sh` enables it on every deploy.
  With nothing queued, a run claims nothing.
- Hybrid retrieval (`RAG_HYBRID`) is off.

## Operator procedure

1. **Grant** a verified account the administrator capability, on the host:
   `systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/netsecai/api.env -p WorkingDirectory=/opt/netsecai/app/backend /opt/netsecai/app/backend/.venv/bin/python scripts/knowledge_admin.py grant <email> --reason "<why>"`.
2. **Create a batch** as that administrator in the web app (`POST /v1/knowledge/imports`). The batch token is
   shown once.
3. **Import** from Windows through an SSH port-forward to the API's loopback port. The upload routes are not
   proxied by the web app. Run `ssh -N -L 4400:127.0.0.1:4400 root@10.1.50.94`, then
   `import_windows_folder.py --root <folder> --api-base-url http://127.0.0.1:4400 --token-env …` (usage is in
   the script's docstring).
4. **Processing:** the worker extracts each PDF in the sandbox, chunks and embeds it, and stops at
   `ready_to_publish`, or at `needs_review` when the extractor warned (for example, OCR was used).
5. **Review and publish** with the same `systemd-run` prefix:
   - `scripts/knowledge_admin.py revisions [--status needs_review]` lists ids, page and chunk counts and
     warnings, never text;
   - `approve <revision-id> --as <admin email>` accepts a revision that needs review;
   - `publish <revision-id> --as <admin email>` copies it into `document_chunks` in one transaction, and the
     previous published revision becomes `retired`;
   - `reject <revision-id> --as <admin email> --reason …` cancels it.

## Decision record

| Area | Decision | Status |
|---|---|---|
| Operator access | `knowledge_admin_grants`, granted only by the host CLI; a grant needs a verified address | Implemented, tested |
| Source visibility | `owner_only`, `selected`, `global`; selected and global need approved distribution rights | Implemented in schema (CHECK) and in retrieval SQL |
| Original storage | PDFs in PostgreSQL as 1 MiB `document_file_blobs` rows, so they are in every nightly dump | Implemented. Uploads stop at the File Library's ceiling (3 GiB of this app's tables, or under 5 GB free disk). Measured 2026-11-04: the production disk is 48 GB with 11 GB free, **shared with PadhAI**, and the database is 13 MB. With 7 days of dumps, a practical library is about 1-2 GB of PDFs. The backup refuses to dump when free space is under 2× the database size. Production WAL and vacuum impact: NOT MEASURED |
| Import | Short-lived batch token stored as SHA-256 hash; sequential offsets; declared SHA-256; finalize verification; the token is checked before the request body | Implemented, tested |
| Extraction sandbox | The File Library's systemd sandbox (no network, 512 MB, one CPU) with `RuntimeMaxSec=1200` for a book | Implemented. A 400-page digital PDF took 2.0 s at 46 MB in WSL. OCR-heavy books: NOT MEASURED (Tesseract is not installed in WSL) |
| Extraction structure | PDFium text, Tesseract OCR (`eng`) for image-only pages | **Open:** one paragraph per page; no section path, printed page label or bbox. Docling is not installed or approved |
| Chunking | 1,400 characters, 200 overlap: five passages with their headings fit `RAG_MAX_CONTEXT_CHARS` (8,000) | Implemented, tested. The processing signature records this chunker, not `RAG_CHUNK_SIZE` |
| Embedding | The application's `build_embeddings` provider (masking applies). A provider hiccup (`embedding_failed`, `embedding_timeout`) is retried after 1, 2 and 4 s plus jitter, without extracting again. The claim is re-checked before each attempt | Implemented, tested |
| Model change | A new embedding model or chunker queues each file again: one revision per file and signature, never while another revision of the file is in progress | Implemented, tested. The new revision still needs publishing; until then the old chunks are not searched, because they are under the old model |
| Publication | Staged chunks copied atomically into `document_chunks` by the operator CLI | Implemented, tested |
| PDF viewing | Administrators only, and only books of sources they can see (owned, global or granted); one Range; safe headers | Implemented, tested |
| Citations | Revision, stable chunk, page and a 500-character excerpt kept in message metadata | **Open:** the excerpt outlives a rights revocation; it needs the owner's decision (F12.10) |
| Recovery | Nightly dump; restore into an empty schema (runbook in `deploy-netsec/backup.sh`) | Runbook corrected and rehearsed (see the evidence table). Off-host encrypted copy: BLOCKED (no destination or key) |

## Synthetic fixture coverage

The generator at `backend/tests/fixtures/knowledge/generate.py` creates original CC0 test PDFs and a SHA-256
manifest. They cover:
- digital text, two columns, tables, figures and captions, and IOS-like configuration;
- physical versus printed page labels;
- an untrusted prompt-injection string inside reference content;
- scanned (image-only) OCR input;
- same-subject, different-edition inputs.

No real vendor book, certification guide or `F:\CCNA` file is included.

## Acceptance evidence

| Invariant | Check | Result |
|---|---|---|
| Strict NDJSON v2: unknown versions and keys, bad sequence, page or digest, and truncation are refused | `tests/test_knowledge_protocol.py` | PASS (collected since `7263cf3`; before that the file never ran) |
| Citations keep revision and page identity without URI or score | `tests/test_knowledge_citations.py` | PASS |
| One bounded Range; multiple or invalid ranges get 416 | `tests/test_knowledge_range.py` | PASS |
| Fixtures regenerate with matching digests | `tests/test_knowledge_fixtures.py` | PASS |
| Importer inventory, containment, resume and finalize, secret-safe errors | `tests/test_import_windows_folder.py` | PASS |
| Worker claim, stale recovery, retry and fencing | `tests/test_knowledge_worker.py` | PASS (WSL PostgreSQL 16) |
| Import API: scoped tokens, resumable chunks, limits, duplicates, auth before body | `tests/test_knowledge_import.py`, `tests/test_route_auth.py` | PASS (WSL PostgreSQL 16) |
| Sandbox runtime, embedding retries, re-queue, CLI publication, capacity, PDF access | `tests/test_knowledge_lifecycle.py` | PASS (WSL PostgreSQL 16) |
| Account deletion with library rows; 0024 upgrade, downgrade and legacy global insert | `tests/test_account_deletion.py`, `tests/test_migrations.py` | PASS (WSL PostgreSQL 16) |
| BFF streams PDF responses and filters headers | `frontend/apps/app/src/lib/server/proxy-core.test.ts` | PASS |
| Blob first response p95 ≤ 500 ms at concurrency 1/3 | `benchmark_knowledge_storage.py`, WSL, 50 MiB synthetic file | PASS: 32.21 ms / 66.07 ms (Codex's run; not on production) |
| Restore of an older dump after a deploy | WSL PostgreSQL 16, 2026-11-04: dumps made with `backup.sh`'s flags, the database then migrated to 0024 | PASS with the new runbook: a 0023 dump restored into an emptied schema in one transaction, then migrated to 0024 with every row. `restore-drill.sh` PASS on the same dump. The old `--clean --if-exists` command, given a 0022 dump: 14 errors, version 0022 recorded over 0023/0024 tables (a half-restore). Not run on a production dump or the host |
| Docling version, licences and sandbox fit | parser gate | BLOCKED: not installed or approved |
| A real CCNA book end to end (import → publish → cited answer) | live | NOT RUN: no book imported |

## Trust boundaries and failure policy

- **Browser/BFF/API:** the BFF allowlist stays narrow; only safe file response headers reach the browser.
- **Importer/API:** batch secrets are random, stored only as SHA-256, scoped to one source, and expire after 12
  hours. The upload routes are reachable only on the host's loopback.
- **User/source:** retrieval resolves visibility and grants in PostgreSQL; caller-provided source names only
  narrow it.
- **PDF/parser:** extraction output is untrusted and must pass the strict NDJSON parser before it is stored.
- **Knowledge/model provider:** book text leaves only through `build_embeddings`, and only for a claim the worker
  still holds.
- **Revision/live index:** incomplete revisions never enter `document_chunks`; publication is one transaction.
- **Citation/current revision:** a stored citation is never silently rebound to a newer revision.
- **Backup/restore:** an archive is not recovery evidence until a restore drill succeeds.

Any failed digest, protocol, rights, source grant, quality, storage, backup or provider-policy gate fails closed and
needs an operator. Similarity or visual duplicate signals never authorize deletion; quarantine and later purge are
separate owner-approved operations.
