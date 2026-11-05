# Knowledge Library readiness ledger

This ledger is the acceptance record for the CCNA-first PDF Knowledge Library: what the repository proves, what
is still open, and which trust boundary each control protects. It does **not** authorize scanning `F:\CCNA`,
moving local books, sending book text to a provider, or deploying. Last verified: 2026-11-05 (working tree after
`a1c1501`, not deployed; the production state below is still 2026-11-04's).

## State in production (2026-11-04)

- The schema is live: migrations 0023 (`0023_knowledge_library`) and 0024 (`0024_knowledge_lifecycle`) are
  applied. 0024 lets accounts that touched the library be deleted (actor columns `ON DELETE SET NULL`) and keeps the
  pre-0023 `ingest_documents.py --global` insert working.
- **No book has been imported:** 0 sources, 0 files, 0 revisions.
- `netsecai-knowledge.timer` is **enabled**: `deploy-netsec/server/install-units.sh` enables it on every deploy,
  unless the owner's off switch exists: `touch /etc/netsecai/knowledge.disabled` keeps it disabled and stopped
  through every later deploy (remove the file, then deploy or `systemctl enable --now netsecai-knowledge.timer`,
  to switch it on again).
  With nothing queued, a run claims nothing.
- Hybrid retrieval (`RAG_HYBRID`) is off.

## Operator procedure

Every host command below uses the prefix
`systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/netsecai/api.env -p WorkingDirectory=/opt/netsecai/app/backend /opt/netsecai/app/backend/.venv/bin/python scripts/knowledge_admin.py`
(written `knowledge_admin.py` below). Each prints ids and counts, never book text. Each acts as one of two
principals:
- **The shared library (CCNA, CCNP and every book all users read):** `--operator`, the host operator (root on
  this host, recorded by name like administrator grants). Its sources are global, belong to no user, and carry
  their distribution-rights basis.
  - Shared-library commands, in order: `course ccna --name "CCNA 200-301" --level 1 --operator`;
    `course ccnp --name "CCNP Enterprise" --level 2 --builds-on ccna --operator`;
    `source "<name>" --operator --course ccna --reason "<rights basis>"`;
    `egress <source-id> --kind embed --reason "<basis>" --operator`;
    `import <source-id> --operator --pdf /path/a.pdf --pdf /path/b.pdf` (PDFs copied to the host; the import API's
    checks; one stored copy per source);
    then the worker processes them, and `approve`/`publish <revision-id> --operator`.
  - The operator never reaches a user's private source.
- **A user's private library:** `--as <admin email>`, an active administrator, on the owner-only sources that
  administrator owns, imported through a batch token and `import_windows_folder.py` (steps 1–5 below). An
  administrator never reaches the shared library.

0. **Scan the folders first, read-only, on the Windows PC (needs the owner's authorization):**
   `python scripts/scan_knowledge_folder.py --root ccna=F:\CCNA --root ccnp=F:\CCNP --out C:\scan\books.json`.
   - It reports, per PDF: SHA-256, pages, scanned pages (OCR needed), outline, page labels, ISBN, edition, volume
     and exam code.
   - It lists the copy, edition and volume pairs.
   - It hashes every file again and reports `unchanged=true` (anything else is a failure). It writes nothing beside
     the books and prints no book text.
1. **Grant** a verified account the administrator capability: `knowledge_admin.py grant <email> --reason "<why>"`.
2. **Courses and sources (the CCNA/CCNP boundary):**
   - `course ccna --name CCNA --level 1 --operator`, then `course ccnp --name CCNP --level 2 --builds-on ccna
     --operator` (courses scope every user's retrieval, so only the operator sets them);
   - `source <name>` makes an owner-only source; `assign <source-id> --course ccna|ccnp|none`.
   - A turn searches only its course (from the Engineering Profile goal, or a course the question names) plus
     general sources.
   - A CCNP turn draws on CCNA, labelled "foundation", only when its own course has too little.
3. **Egress approval (owner decision J2):** `egress <source-id> --kind embed --reason "<basis>"`.
   - Without it, nothing of that source is queued.
   - Withdrawing it (`--withdraw`) stops a book mid-run before its next provider call.
   - `--kind extract` is reserved for model-assisted concept extraction (not built; separate approval).
4. **Create a batch** in the web app (`POST /v1/knowledge/imports`) or with `batch <source-id>`. The token is shown
   once.
5. **Import** from Windows through an SSH port-forward to the API's loopback port. The upload routes are not
   proxied by the web app. Run `ssh -N -L 4400:127.0.0.1:4400 root@10.1.50.94`, then
   `import_windows_folder.py --root <folder> --api-base-url http://127.0.0.1:4400 --token-env …` (usage is in the
   script's docstring).
6. **Processing** (the worker, one book at a time):
   - **Where it runs:** in `netsecai-knowledge.slice` (CPU and IO weight 20 against 100, memory capped at 1200 MB).
   - **When it starts a book:** only when memory, load and live chat streams leave room (`app/knowledge/admission.py`).
     OCR uses as many processes as fit, at most 2 on the 4-CPU host.
   - **What it does:** reads the PDF in the sandbox (outline, page labels, OCR), stores each distinct passage once,
     and embeds only passages with no vector yet.
   - **Duplicate check:** compares the book's pages with every other book in the library.
   - **Where it stops:** at `ready_to_publish`; or at `needs_review` when the extractor warned (OCR, low-confidence
     pages, no outline) or a duplicate review is pending.
   - **Logging:** each tick logs one `knowledge.worker` JSON line (queued, admission, claimed, completed or failed).
7. **Duplicates:** `duplicates [--status pending|all]` lists copies, renamed copies, editions, volumes and combined
   books found across sources, courses and batches.
   - `decide <review-id> --decision keep_separate|reuse|reject` records the decision.
   - A pending review blocks publication; `reuse` and `reject` keep the copy unpublished; nothing is ever deleted
     automatically.
8. **Review and publish:**
   - `revisions --as <admin>|--operator [--status needs_review]` lists that principal's revisions only: ids, source,
     pages, chunk and passage counts and warnings;
   - `approve <revision-id>` accepts a revision that needs review;
   - `publish <revision-id>` makes its passages live in its source in one transaction, under a lock that serializes
     publications. It re-checks rights, vectors and duplicates against everything committed, and retires the
     previous revision.
   - If another copy appeared meanwhile, the revision is **held**: it goes back to `needs_review`, the new review is
     kept, and the command exits 1;
   - `reject <revision-id> --reason …` cancels it.
9. **Withdraw:** `delete-source <source-id> --reason …` removes a source, its books and its revisions.
   - It purges every passage no other book carries, with its vectors and concept rows.
   - Citations of its books in stored replies keep their reference but stop showing the excerpt (read time).

## Decision record

| Area | Decision | Status |
|---|---|---|
| Operator access | `knowledge_admin_grants`, granted only by the host CLI; a grant needs a verified address | Implemented, tested |
| Source visibility | `owner_only`, `selected`, `global`; selected and global need approved distribution rights | Implemented in schema (CHECK) and in retrieval SQL |
| Original storage | PDFs in PostgreSQL as 1 MiB `document_file_blobs` rows, so they are in every nightly dump | Implemented. Uploads stop at the File Library's ceiling (3 GiB of this app's tables, or under 5 GB free disk). Measured 2026-11-04: the production disk is 48 GB with 11 GB free, **shared with PadhAI**, and the database is 13 MB. With 7 days of dumps, a practical library is about 1-2 GB of PDFs. The backup refuses to dump when free space is under 2× the database size. Production WAL and vacuum impact: NOT MEASURED |
| Import | Short-lived batch token stored as SHA-256 hash; sequential offsets; declared SHA-256; finalize verification; the token is checked before the request body | Implemented, tested |
| Extraction sandbox | The File Library's systemd sandbox (no network), with what a book needs: `RuntimeMaxSec=3600`, plus `CPUQuota` and `MemoryMax` (384 MB + 256 MB per OCR process) sized to the admitted OCR processes. It runs in `netsecai-knowledge.slice`. The worker unit's `TimeoutStartSec=7200` lets an hour of OCR finish | Implemented, tested. A 400-page digital PDF took 2.0 s at 46 MB in WSL (v2 reader). OCR-heavy books on the host: NOT MEASURED |
| Extraction structure | Book reader v3 (`app/knowledge/extract.py`, protocol v3): PDF outline into section paths, a numbered-heading fallback when there is no outline, printed page labels, running heads and feet removed, OCR on every scanned page in parallel processes with per-page confidence (low confidence warns), and book metadata (ISBN-13 with checksum, edition, volume, exam codes, year) | Implemented, tested on synthetic PDFs. Up to 3,000 pages and 100 MB. Tables are kept whole, not rebuilt into rows; bbox is not produced (no layout engine): INCOMPLETE by decision. Docling is not installed or approved |
| Canonical passages (0025) | A passage is stored and embedded once (normalized-text key), wherever it appears. Occurrences record its book, revision, pages and section. A passage is live in a source while a published revision of that source carries it. Access comes from sources; identity carries no rights | Implemented, tested (`tests/test_knowledge_passages.py`) |
| Duplicates across the library | Page fingerprints for every file. Exact bytes, the same content, the same edition, volume (subset/superset), probable copy, other edition and related books are classified across sources, courses and batches. A pending review blocks publication and is re-checked under the publish lock | Implemented, tested. Evidence for an operator only: nothing is deleted by a classification |
| Course boundaries | `knowledge_courses` with `builds_on`; each source has a course or is general. Retrieval filters by the turn's course; the foundation course fills in, labelled, only when primary evidence is short | Implemented, tested |
| Concept graph | Deterministic layer (`app/knowledge/concepts.py`): a versioned vocabulary of about 160 networking concepts with aliases. It also learns acronyms books define and stores value and relation facts, each with its verbatim evidence span. It feeds a concept leg in hybrid retrieval | Implemented. Gold set (110 synthetic sentences, 124 facts): precision 0.977, recall 0.694 (`scripts/eval_concepts.py`). The gold set is below the planned 150 facts. Model-assisted extraction: NOT BUILT (needs extraction egress and a budget, J3). Multi-hop expansion and conflict notes in the prompt: NOT BUILT |
| Scheduling | `netsecai-knowledge.slice`, admission by memory, load and live streams, OCR processes sized to what is free, a 60 s lease heartbeat, SIGTERM recorded as retryable | Implemented, tested (unit and deploy tests). Throughput on the host: NOT MEASURED |
| Chunking | 1,400 characters, 200 overlap: five passages with their headings fit `RAG_MAX_CONTEXT_CHARS` (8,000) | Implemented, tested. The processing signature records this chunker, not `RAG_CHUNK_SIZE` |
| Embedding | The application's `build_embeddings` provider (masking applies). A provider hiccup (`embedding_failed`, `embedding_timeout`) is retried after 1, 2 and 4 s plus jitter, without extracting again. The claim is re-checked before each attempt | Implemented, tested |
| Model change | A new embedding model or chunker queues each file again: one revision per file and signature, never while another revision of the file is in progress | Implemented, tested. The new revision still needs publishing; until then the old chunks are not searched, because they are under the old model |
| Publication | `passages.publish` by the operator CLI, in one transaction under an advisory lock. Rights, vectors and duplicate reviews are re-checked, and passages become live in the source. The previous revision retires, and liveness it alone carried is dropped | Implemented, tested. `document_chunks` keeps serving `ingest_documents.py` content; retrieval reads both |
| Lexical search | Any informative word of the question (OR of bound `plainto_tsquery`, question words dropped). A lexical- or concept-only candidate needs the cosine floor, measured exactly, or an exact engineering identifier from the question | Implemented, tested |
| PDF viewing | Administrators only, and only books of sources they can see (owned, global or granted); one Range; safe headers | Implemented, tested |
| Citations | Revision, stable chunk, page and a 500-character excerpt kept in message metadata. At read time, a citation of a deleted book, or of a source whose rights were revoked, loses its excerpt and is marked `withdrawn`; the stored reply is unchanged | Implemented, tested. Whether to keep, shorten or drop excerpts at all is still the owner's decision (F12.10) |
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
| Strict NDJSON v2 and v3: unknown versions and keys, bad sequence, page or digest, truncation, and book metadata on v2 or over 16 KB are refused | `tests/test_knowledge_protocol.py` | PASS (collected since `7263cf3`; before that the file never ran) |
| A passage in 3 books of 2 sources is stored and embedded once (3 occurrences). 12 books staged in parallel converge on one row each. A second book embeds only text the library lacks | `tests/test_knowledge_passages.py` | PASS (WSL PostgreSQL 16, 2026-11-05; RED on `a1c1501`: the module does not exist) |
| A renamed copy in another source is held for review (the review is kept), published only after a decision. A `reject` keeps it unpublished. A new revision retires the old one and withdraws text it dropped. Revoked rights refuse publication. Deleting a source purges only text no other book carries | `tests/test_knowledge_passages.py` | PASS (WSL PostgreSQL 16, 2026-11-05) |
| Retrieval returns a live passage with its book, page and section only to who may read it. A staged book is not searchable. A revoked source disappears at once. A course turn never sees the other course; CCNA fills in as foundation only when CCNP is short; a course named in the question wins | `tests/test_knowledge_passages.py` | PASS (WSL PostgreSQL 16, 2026-11-05) |
| A cited excerpt is withheld once its source is revoked or its book deleted; the stored reply keeps it | `tests/test_knowledge_passages.py` | PASS (WSL PostgreSQL 16, 2026-11-05) |
| Nothing is queued or embedded without the owner's embed-egress approval. Withdrawing it stops the next provider call | `tests/test_knowledge_passages.py` | PASS (WSL PostgreSQL 16, 2026-11-05) |
| Concept layer: gold-set precision ≥ 0.95 with verbatim evidence. Indexing is idempotent; acronyms books define are learned. The concept leg finds what dense ranking missed; keyword noise below the cosine floor is turned away; an exact identifier is admitted | `tests/test_knowledge_concepts.py`, `scripts/eval_concepts.py` | PASS: precision 0.977, recall 0.694 on 124 gold facts (WSL PostgreSQL 16, 2026-11-05) |
| Admission by memory, load and live streams; OCR processes sized to what is free; extractors in `netsecai-knowledge.slice`; the worker unit outlives an hour of OCR | `tests/test_knowledge_admission.py`, `deploy-netsec/tests/workers-deploy.test.mjs` | PASS |
| The read-only scanner finds copies and volumes and reads ISBN, edition and exam metadata. It prints no text, refuses to write beside the books and proves nothing changed | `tests/test_scan_knowledge_folder.py` (synthetic PDFs) | PASS. Not run on `F:\` (needs authorization) |
| Question received to first model request is recorded per reply (internal) and reported as p50/p95/p99 | `tests/test_chat_lifecycle.py`, `scripts/pre_llm_report.py` | PASS (fake model). The live measurement against the 1,000 ms budget: NOT RUN (needs a deploy) |
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
