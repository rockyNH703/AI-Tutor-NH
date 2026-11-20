# NHprep video courses

> Written 2026-11-20 against the `nhprep` branch at `3a2548d` (worktree `C:\Users\test\netsec-nhprep`), from the
> code, the approved plan (revision 4 plus Revision 5, the video playback change) and the handoff
> `C:\Users\test\netsec-handoffs\nhprep.md`. **Not released:** production is BLOCKED by D4 (disk) and needs the
> owner's explicit authorization and an agreed release window. Labels: IMPLEMENTED, TESTED (with where), NOT TESTED,
> BLOCKED.

NHprep (nhprep.com, NetworkersHome's learning site) has video courses: lessons with a video, notes, chapters with
timestamps, a cheat sheet, flashcards and interview questions. NetSec AI copies that catalogue (never the video
files) into its own tables, and the chat answers a request such as "show me the OSPF areas video" or "flashcards on
BGP" (optionally naming a trainer or a day) with a **lesson card**: an in-chat player, the lesson's facts, its sections, previous and
next, other matching lessons, and the study aids asked for. No model is called for such a turn.

Quizzes are not part of this feature: NHprep's quiz questions go into the certification question bank, which serves
them ([question_bank.md](question_bank.md)). The exporter only collects them.

## Contents

- [Status](#status)
- [Who it is for](#who-it-is-for)
- [Architecture](#architecture)
- [Access basis](#access-basis)
- [Export v1](#export-v1)
- [Schema (0027)](#schema-0027)
- [Import](#import)
- [Retrieval](#retrieval)
- [Chat cards and the API](#chat-cards-and-the-api)
- [Playback](#playback)
- [Privacy and security](#privacy-and-security)
- [QA and tests](#qa-and-tests)
- [The worktree workflow](#the-worktree-workflow)
- [Runbooks](#runbooks)
- [Extension points](#extension-points)

## Status

| Part | State |
|---|---|
| Exporter (PC) | IMPLEMENTED. Full export run on 2026-11-19 (bundle `2026-11-19T08-17-18-846Z`, about 23 MB): 59 courses, 914 lessons (914 video ids, all `nhprep-mp4`; study aids for 861), 1,665 keyed quiz questions of 1,843 (6 categories are empty on NHprep itself), 4,226 paced requests, at most 1 a second, 241 accepted quiz attempts. Unit tests: 14 pass (`bun test scripts/nhprep-export`, 2026-11-20) |
| Schema 0027, importer | IMPLEMENTED; TESTED in WSL (focused suites). `test_migrations.py` for 0027 is not recorded as run in the branch's commits |
| Finder and cue parse | IMPLEMENTED; measured on the **development** split only (below). The held-out split (gate G4) does not exist yet: NOT TESTED |
| API, chat card turn | IMPLEMENTED; TESTED in WSL (focused suites) |
| Web app cards and player | IMPLEMENTED; typecheck, lint, unit tests and build pass; headed NHprep e2e 14/14 with the API stubbed in the browser and a stub MP4 (commit `c7bc819`) |
| Live playback from `videos.nhprep.com` | **BLOCKED**: no video signer exists until the company chooses option A or B ([Playback](#playback)). NOT TESTED |
| Dense leg with real embeddings | NOT TESTED (fake vectors only so far) |
| Host finder latency (`eval_nhprep.py --latency`) | NOT TESTED |
| Production | **BLOCKED** by D4 (disk) and the release window (D10); needs the owner's authorization. Not deployed |

## Who it is for

Only signed-in users with a **verified** email whose domain is in `NHPREP_ALLOWED_EMAIL_DOMAINS` (the shared rule
`app/access/students.py` `is_nh_student`: ASCII domain, exact, case-folded; an empty list means nobody), and then
only as `NHPREP_MODE` allows (`app/nhprep/access.py` `nhprep_allows`):

| `NHPREP_MODE` | Who gets NHprep |
|---|---|
| `off` (default) | Nobody: no card turn, every `/v1/nhprep` route is 404, stored cards are hidden |
| `qa` | NetworkersHome students whose address is in `NHPREP_QA_EMAILS` (production refuses `qa` without a list) |
| `on` | Every NetworkersHome student |

The rule is decided per request from the account as it is now: an address that changes domain loses the cards at
once. `build_chat` reads the mode when the API starts, so a mode change is a restart.

## Architecture

```
PC (owner's Windows machine)                     WSL or the host                      Browser
  exporter (Playwright, headed Edge, paced)  ->   nhprep_import.py  -> nhprep_* tables
  -> C:\Users\test\nhprep-data\export\<run>\       (dry run by default)     |
     export.jsonl + manifest.json                                           v
                                                  chat turn: intent.parse -> search.answer -> card (ids only)
                                                  /v1/nhprep: lessons, items, playback   <- BFF <- lesson card
                                                                                           native <video> <- videos.nhprep.com
```

| Step | Code |
|---|---|
| Export (PC only) | `frontend/apps/app/scripts/nhprep-export/` (`session.ts` the gated browser, `gate.ts` pacing, `export.ts`, `parse.ts`, `paths.ts`, `credentials.ts`; `recon.ts` was the L1 tool) |
| Validate, normalise, import | `backend/app/nhprep/{validate,normalize,importer,repository,embedding,vectors}.py`; `backend/scripts/nhprep_import.py`, `nhprep_embed_offline.py` |
| Cue parse and finder | `app/nhprep/intent.py`, `search.py`, `tuning.json`, `types.py` (the contract between them and the chat) |
| Chat turn | `app/nhprep/chat.py`, called from `app/chat/service.py` `_app_turn` after the question bank's parse |
| API | `app/nhprep/router.py`, `service.py`, `schemas.py`, `playback.py` |
| Web app | `frontend/apps/app/src/features/nhprep/` (slot, panel, player, restricted Markdown), `src/lib/api/nhprep.ts`; details in `frontend/CLAUDE.md` |

NHprep text never enters the shared knowledge store (`document_chunks`): global sources reach every user's prompt.
NHprep has its own index, `nhprep_search_chunks`, read only by `app/nhprep`. `tests/test_architecture_guards.py`
fails if `app/nhprep` imports the chat, a model, the shared knowledge store, LangChain, OpenAI or httpx, if its
scripts import the shared store's writers, or if another module reaches into the package except through its
public modules (`chat` for the chat, `access` for the conversations router, `router`/`chat`/`playback` for
`main.py`).

## Access basis

- **Owner-authorised** (decision Q1): the export uses NetworkersHome's own company account on nhprep.com, which
  NetworkersHome operates.
- **Sign-in is Google** (the company account has no password on nhprep.com; owner decision 2026-11-19). The
  exporter reads the account from the file named by `NHPREP_CREDENTIALS_FILE` (an absolute path, read at run
  time, never printed, logged or put in a process argument) and types it into Google's own page in a headed
  window; a 2-step check is left to the owner in that window. The key files `NHprep.txt` and
  `nhprep_google_signin.txt` in the repository root are git-ignored and never opened by tools.
- **robots.txt** on nhprep.com disallows `/api/` and `/dashboard/`. That is overridden on the owner's authority,
  and the override is recorded with every import: `nhprep_sync_runs.access_basis` defaults to
  `owner-authorised; NetworkersHome operates nhprep.com; robots.txt disallow overridden` (`importer.ACCESS_BASIS`).
- **Writes on NHprep** (decision Q8): only quiz submits, which are the only way to get answer keys. Each accepted
  submit is one attempt record on the account, counted in a ledger that outlives runs. The owner set no attempt
  cap; each category stops by the stop rule (below). No other write is allowed: the gate aborts any other
  writing request.

## Export v1

Frozen 2026-11-19 after the signed-in recon. One run folder outside every git working tree
(`C:\Users\test\nhprep-data\export\<UTC time>\`; `paths.ts` refuses anything else) holds:

- `export.jsonl`: one JSON object a line, each with `type` and `schema: 1`.
- `manifest.json`: `{schema: 1, exporterVersion, startedAt, finishedAt, complete: {lessons, quiz}, sha256 (of
  export.jsonl), counts, coverage, ledger: {quizAttempts}, gapMs, requests, maxRequestsPerSecond, …}`. The importer
  treats a bundle as complete when `complete` is `true` or `complete.lessons` is `true`.
- `run.log` (paths, statuses, parameter names and JSON shapes only), `failed.json` and `failed-quiz.json` (what
  still failed after the retry rounds), `fatal.txt` if the run stopped.

Records:

| `type` | Fields |
|---|---|
| `course` | `slug`, `title`, `description`, `trainerHints {titleParens, aboutBy, slugToken}` (raw; resolved by NetSec's alias table), `lessonCount` |
| `lesson` | `external_id` (NHprep's lesson uuid, the one in `/api/lesson/{id}/artifacts`; also the bank's `lesson_ref`), `course` (slug), `slug`, `title`, `position` ("Lesson N of M"), `day` (from a `day-N` slug, or null), `durationSeconds`, `video` (`{provider: "nhprep-mp4", videoId: uuid}` or null; never a URL, `exp` or `sig`), `aids`, and the additive `lessonType` (always null today: the course page does not show it; the importer accepts null or `video`) |
| `quiz_question` | `category` (slug), `categoryName`, `id`, `format: "single"`, `question`, `options` (as served, order kept), `correctOption` and `correctIndex` (0-3), `explanation`, `difficulty: null`, `lessonExternalId: null` (quizzes are per category). Read by the bank's converter; the importer only counts them |
| `quiz_coverage` | `category`, `attempts`, `seen` (distinct ids), `reported` (NHprep's `totalAvailable`) |

`aids` is NHprep's artifacts JSON as served (`schemaVersion`, `language`, `generatedAt`, `status`, and per kind
`notes | chapters | cheatsheet | flashcards | interviewQuestions` → `{availability, reason, items[]}`). Items carry
`id` plus: notes `title, markdown`; chapters `title, start, end` (seconds); cheat sheet `mode, command, platform,
description`; flashcards `front, back`; interview questions `question, modelAnswer, scenario`. `sourceRefs` is
dropped.

The latest keyed bundle is kept. The bank reads it and never writes there.

## Schema (0027)

`backend/alembic/versions/0027_nhprep_video_courses.py` (after `0026_qbank_certification`). Additive: new tables
only; `pg_trgm` is created `IF NOT EXISTS` and never dropped (the bank uses it too). The downgrade refuses while any
import record, course or trainer exists. No table references `users`, so account deletion is unaffected
(`test_nhprep_schema.py::test_no_nhprep_table_holds_user_data`).

| Table | Holds |
|---|---|
| `nhprep_sync_runs` | One row per `--apply` or `--purge` (a dry run records nothing): kind, status (`running`, `succeeded`, `failed`, `interrupted`), operator, `complete`, manifest sha256, exporter and schema version, embedding model, access and egress basis, purge reason, a counts-only `report`. Never deleted: the audit |
| `nhprep_courses` | `external_id` (unique), slug, title, description, `track` (from an explicit map, else null), `content_hash`, `removed_at` |
| `nhprep_trainers`, `nhprep_course_trainers` | Trainers resolved only through the alias table (`normalize.TRAINER_ALIASES`; never "Admin"), linked to courses with their provenance |
| `nhprep_lessons` | `external_id` (unique), course, position, `source_day`, title, slug, duration, `video_provider` (`nhprep-mp4`, or a dormant `bunny`), `video_id`, `video_library_id` (Bunny only), the generated `video_embed_url` (a Bunny URL or null; nothing can write it), `availability` per kind, `content_hash`, `removed_at` |
| `nhprep_lesson_items` | One study aid: kind (`note`, `chapter`, `flashcard`, `cheat_sheet`, `interview_question`, `other`), `item_key`, position, title, `body_md`, `start_seconds` (chapters only, 0-86400), `data`, `content_hash` |
| `nhprep_search_chunks` | Search text derived from a lesson (`lesson`: course, trainer, lesson number, day, de-slugged alias, chapter outline) or from its items, with `vector(1536)` (HNSW, cosine), a generated english `content_tsv` (title A, content B) and `embedding_model`. Unique per (lesson, content hash) |

There is no signed URL, `exp`, `sig` or token column anywhere. More: [data-model.md](data-model.md).

## Import

`backend/scripts/nhprep_import.py` (run from `backend/`; uses `DATABASE_URL` and the embedding settings from the
environment):

```sh
python scripts/nhprep_import.py <run folder | export.jsonl | ->        # dry run: no writes, no provider calls
    [--sha256 HEX]                    # required for stdin (-), which is never complete (no removals)
    [--apply] [--operator NAME (--backup <dump file> | --backup-waiver "<reason>")]
    [--access-basis "..."] [--egress-basis "..."]   # egress: required when any text will be embedded
    [--vectors vectors.jsonl]         # precomputed vectors: the run's only embedder
    [--emit-texts texts.jsonl]        # dry run only: every distinct search text, for the offline embedder
    [--allow-removals N]              # needed when a complete export removes a whole course or > 5% of lessons
python scripts/nhprep_import.py --purge --reason "<why>" [--apply] [--operator NAME ...]
```

Exit codes: 0 done, 1 refused or invalid, 3 another import or purge holds the lock. Output is counts and reason
codes, never NHprep text.

Safety:
- **Dry run by default.** A dry run reads the database and takes the lock, but writes nothing, records nothing and
  calls no provider. It prints the plan: counts, changes, texts to embed, removals and the projected size.
- **Integrity.** The bytes must match the manifest's sha256 (and `--sha256` when given); the manifest's counts must
  match the stream. A record that fails validation is a counted reject (a code, never its text). A video with a
  `url`, `exp`, `sig` or any other extra field refuses the lesson.
- **Lock.** `pg_try_advisory_lock(7_216_094_260)` on its own AUTOCOMMIT connection: a second run refuses (exit 3)
  rather than waits. The lock is pinged before every write and every embedding batch; a lost lock stops the run,
  which is recorded `failed`. Under the lock, a stale `running` row becomes `interrupted`.
- **Content hashes.** A course, lesson, item or chunk is written only when its hash changed. An unchanged export
  writes 0 rows and embeds 0 texts; an interrupted run resumes where it stopped.
- **Removals.** Only a complete export removes (it sets `removed_at`, last, after everything else is written).
  Removing any whole course, or more than 5% of the active lessons, needs `--allow-removals N`. A stream (`-`)
  never removes.
- **Egress.** Texts go to the embeddings provider only through `build_embeddings` (secrets masked) and only with
  `--egress-basis`, which is recorded. `EMBEDDING_PROVIDER=fake` is refused outside a `*_test` database.
- **Capacity.** Before the run and before every write and batch: the files capacity check (free disk ≥ 5 GB and the
  `public` schema under its 4 GiB ceiling) and NHprep's own reserve (512 MiB left for students' uploads); the
  projection also needs free disk ≥ 5 GB + 2 × projected + 0.5 GB. Refusal code `capacity`.
- **Normalisation** (counted): invisible, bidi and control characters stripped; fence info strings mapped to an
  allowlist (a ```` ```video ```` or ```` ```quiz ```` fence becomes plain text code); every line starting with
  `::` escaped; every nhprep.com URL (any subdomain, also URL-shaped text and reference definitions) made plain
  text.
- **Embeddings.** Sequential batches of `EMBEDDING_BATCH_SIZE`, retried after 2, 5 and 15 s on transient errors; a
  vector the same model already made for the same text is reused. `--vectors` (format `nhprep-vectors/1`: a header
  line, then `{k: sha256 of the text, v: base64 float32 × 1536}`) replaces the provider; a text missing from the
  file refuses the run before it writes.
- **Purge** deletes the whole catalogue with `DELETE` (the runtime role has no `TRUNCATE`); `nhprep_sync_runs`
  stays, with the purge recorded in it.

## Retrieval

**The dispatcher** (`_app_turn`): a typed message with no attachments, not a voice turn and not a command. The
question bank's strict parse runs first (when `QUESTION_BANK_TURN` allows it); only if the bank does not take the
message does NHprep's cue parse run; if NHprep finds nothing, the normal model turn runs. A regeneration of an
NHprep card runs the finder again.

| Request | Goes to |
|---|---|
| "interview questions on BGP", "flashcards on OSPF", "show me the NAT video" | NHprep |
| "questions from this video", "quiz me on OSPF", test, practice, exam, MCQ wording | the bank (NHprep's parse refuses this wording itself, and anything the bank's parser claims) |
| "explain OSPF areas", "QoS for voice and video" | the model (no cue, or a cue word used in another sense) |

**The cue parse** (`intent.parse`, pure): strong cues (flashcards, cheat sheet, interview questions, "nhprep") are
enough; lesson words (video, lecture, lesson, notes, sections) need a request frame ("show me", "is there", "which
video…"); weak words (class, session, recording) need a frame or another cue. It reads a trainer (alias table or an
honorific), a course hint, "day N" (the upload's day, not the position), and the views asked for. A message over
300 characters is not parsed. It may read the latest earlier user message for "that video".

**The finder** (`search.answer`): one deadline, `NHPREP_FIND_TIMEOUT_S` (1.0 s), over everything. A timeout or
an error is a status, never an exception.
- The query embedding runs first (masked, at most 60% of the deadline; skipped with a reason when embeddings are
  not configured), before any transaction opens.
- Then, in one transaction: settings (`statement_timeout`, the trigram threshold, `hnsw.ef_search`), a trainer
  filter (only through `nhprep_trainers`: an unknown trainer is `none`, never a guess) and course hints, then the
  legs, all static SQL with bound parameters:
  - **dense**: the top 200 chunks on the HNSW index, best cosine per lesson;
  - **lexical**: `websearch_to_tsquery('english')` in OR form, `ts_rank_cd`, all-terms and per-term coverage, over
    lesson headers plus the asked-for aid kinds;
  - **title**: `pg_trgm` word similarity both ways on lesson titles and chapter titles;
  - **day**: `source_day` within the named trainer's or course's lessons.
- Weighted reciprocal-rank fusion; weights and thresholds are data in `tuning.json` (`rrf_k` 60; dense 1.0,
  lexical 1.0, title 1.5, day 3.0, course hint 0.5).
- **Confidence:** an exact title is `confident`, unless different trainers teach it (`ambiguous`); otherwise
  evidence (cosine ≥ 0.55, a title or chapter word similarity ≥ 0.75, or every term in one chunk) with full term
  coverage and a clear lead over the next distinct lesson (a fused margin ≥ 0.10, or more kinds of evidence) is
  `confident`, evidence without them `ambiguous`, a near title (one
  misspelt word) `ambiguous`, and anything else `none`. Up to 5 alternatives, deduped by video id.

**Development metrics** (commit `d8819930`; 483 cases in `backend/evals/nhprep/dev.jsonl`, the full export imported
into WSL, title and lexical legs only, fake vectors): R@5 0.926, MRR 0.889, trainer precision 1.0, intent accuracy
1.0 with 0 false triggers on 69 ordinary-chat cases; conceptual queries R@5 0.273, which needs the real dense leg.
**These are tuning numbers, not gate evidence:** gate G4 needs a frozen held-out split (≥ 20 cases in each of 13
categories, ≥ 50 negatives, ≥ 150 intent cases), the real dense leg and a host latency run. None exists yet.

```sh
DATABASE_URL=postgresql+asyncpg://aichat:aichat@127.0.0.1:5432/netsec_nhprep_eval_test \
  python scripts/eval_nhprep.py [--dense off|on] [--vectors vectors.jsonl] [--json]
python scripts/eval_nhprep.py --latency [--dense on] < messages.txt    # one message per line; on the host
```

## Chat cards and the API

A card turn's reply is a fixed line (`chat.COPY`: "Here is the NHprep lesson that matches your request." or
"Several NHprep lessons match your request. …"), sent as one token, with no model call and no NHprep text. The
card is `metadata.nhprep`, ids only, every key always present:

```
{version: 1, views: ("video"|"chapters"|"notes"|"cheat_sheet"|"flashcards"|"interview")[],
 status: "confident"|"ambiguous", primaryLessonId, alternativeLessonIds (≤ 5), matchedItemId | null}
```

pinned by `backend/tests/fixtures/nhprep/cards.json` (which the web app's tests read too). Why it was chosen
(status, counts, per-leg ms, reason codes) is the internal `metadata.nhprepTrace`, never sent to a client.

**Hiding:** `metadata.nhprep` reaches a reader only while NHprep is for them (`nhprep_allows`). For a reader who is
not a NetworkersHome student, an NHprep reply is the neutral line "This reply isn't available." with no metadata
(the shared rule in `app/conversations/router.py`). A student with `NHPREP_MODE=off` sees the fixed line without
the card.

**The API** (`/v1/nhprep`, always mounted; shapes in [api-contract.md](api-contract.md#nhprep-video-courses), rows
in [api-reference.md](api-reference.md#nhprep)): `GET /lessons?ids=` (1-10), `GET /lessons/{lesson_id}`,
`GET /lessons/{lesson_id}/items?kind=`, `POST /lessons/{lesson_id}/playback`. For anyone NHprep is not for, every
route is 404 before any id, query or body is validated, and takes no rate-limit slot. Limits per user: 600 requests a
minute (`NHPREP_REQUESTS_PER_MINUTE`), and playbacks also 20 a minute (`NHPREP_PLAYBACKS_PER_MINUTE`). No GET
carries an NHprep id, page URL or video link.

**The web app** draws the card in a lazy slot under the fixed line: player, course and trainer, "Jump to" sections,
previous and next, "Other matching videos" ("Also taught by" for the same title), and the asked-for notes, cheat
sheet, flashcards (the first 100) and interview questions. Imported Markdown renders through `NhprepMarkdown`: no
tutor syntax or widgets, raw HTML as text, images never loaded, nhprep.com links shown as text. Details:
`frontend/CLAUDE.md`, "NHprep lesson cards".

## Playback

NHprep's videos are progressive MP4 files on `https://videos.nhprep.com/{videoId}.mp4`, playable only with NHprep's
own short-lived signature (`exp`, `sig`; about 6 hours; without them the host answers 410). The L1 recon found no
DRM and no Bunny Stream for the exported lessons (914 of 914 are `nhprep-mp4`). NetSec stores only the video id;
how it gets a signature is **the company's choice, not made yet** (Revision 5, 2026-11-20):

- **(A)** NHprep's signing secret and its exact recipe, which the owner writes into `/etc/netsecai/api.env`; or
- **(B)** a server-to-server signing endpoint on NHprep.

What is built (`app/nhprep/playback.py`):
- A `VideoSigner` protocol, `sign(video_id) -> SignedVideo(src, expires_at)`, built in `create_app` and injectable
  (tests pass a fake signer, as they pass a fake model).
- `NHPREP_PLAYBACK` `off | secret | endpoint` (default `off`). `build_signer` returns no signer for `off` and
  raises for the other two, and production's `validate_for_env` refuses anything but `off`: nothing can be switched
  on before its signer exists.
- `POST /v1/nhprep/lessons/{lesson_id}/playback` answers `{status: "ready", src, expiresIn}` or
  `{status: "unavailable", reason: "not_configured" | "no_video" | "signing_failed"}`, with
  `Cache-Control: no-store`. The signer has 5 s. Before `src` leaves, the server checks it is exactly
  `https://videos.nhprep.com/{videoId}.mp4?…` (printable ASCII, ≤ 2048 characters, no space, `#`, `\`, quotes or
  angle brackets); anything else, a failed signer or an expired link is `signing_failed`, never a 500. The link is
  never logged or stored; the log line has the lesson id, the reason code, `expires_in` and the latency.
- The web app's player: a native `<video controls preload="none" playsInline controlsList="nodownload">`, no iframe
  and no player library. The POST happens only on Play. The client checks the origin and the `/{uuid}.mp4` path
  before setting `src`. Jump to sets `currentTime`. A player error (an expired link answers 410) gets one fresh
  link, resumed where it was; a second error shows "The video stopped or couldn't load. Reload it to try again."
  and **Reload video**.

**Until the company answers:** `NHPREP_PLAYBACK=off`, so every lesson's `video` is `not_configured` and the card
says "Video isn't available in NetSec AI yet." from the start. Sections (as a list), notes, flashcards, cheat sheet
and interview questions still show. When the answer comes, the signer is about 20 lines plus a pinned test vector;
option (B) also needs one recorded httpx exception for the signer module in `test_architecture_guards.py`, and the
refusal in `validate_for_env` is lifted for that mode.

**Tested with a fake signer** (pytest `test_nhprep_playback.py`; headed e2e `video.nhprep.ts` with the API stubbed
in the browser and a 4-second MP4 fixture: play, Jump to, the 410 path with one new link and resume, the second
failure and Reload, not configured, a foreign link never loaded, rate limited). **Live playback against
`videos.nhprep.com`: NOT TESTED.**

## Privacy and security

- **Never stored** (database, exports, metadata, client storage, logs, URLs): a signed video URL, `exp`, `sig`,
  NHprep tokens, cookies or CSRF values. The schema has no column for them, the importer refuses a video with a URL
  field, the exporter writes video ids only, and the e2e checks the page URL, `localStorage` and `sessionStorage`.
- **Logs** (`nhprep.intent`, `nhprep.finder`, `nhprep.playback`, `nhprep.import …`): ids, counts, per-leg ms and
  reason codes; never a student's words, NHprep text or a link (`test_nhprep_chat.py::test_no_nhprep_log_line_holds_the_students_words`,
  `test_nhprep_playback.py::test_a_ready_link_is_sent_uncached_and_never_logged`). The exporter's `run.log` holds
  paths, statuses, query parameter names and JSON shapes; values like `token=`, `sig=` or `code=` are redacted.
- **CSP:** `media-src 'self' https://videos.nhprep.com` (before it there was no `media-src`, so media fell back to
  `default-src 'self'`). No frame host was added. `e2e/security.e2e.ts` and `video.nhprep.ts` assert it.
- **Masking:** the finder's query embedding and the import's embeddings go through `build_embeddings`
  (`RedactingEmbeddings`). One masked query embedding per NHprep request is allowed (owner decision Q9).
- **The model never sees NHprep text** (no prompt, no `content`, no shared store), and an NHprep reply's text is a
  fixed line.
- Trust boundaries B11-B13 and the residual risks: [security/THREAT_MODEL.md](security/THREAT_MODEL.md).

## QA and tests

| Where | Files |
|---|---|
| Backend (WSL pytest) | `tests/test_nhprep_access.py`, `test_nhprep_api.py`, `test_nhprep_chat.py`, `test_nhprep_import.py`, `test_nhprep_intent.py`, `test_nhprep_playback.py`, `test_nhprep_schema.py`, `test_nhprep_search.py`; NHprep cases in `test_architecture_guards.py`, `test_authorization.py`, `test_migrations.py` (0027); fixtures `tests/fixtures/nhprep/` |
| Web app (`bun test`) | `src/features/nhprep/nhprep-markdown.test.tsx`, `nhprep-panel.test.tsx`, `src/lib/api/nhprep.test.ts`; NHprep cases in `routes.test.ts`, `copy-vocabulary.test.ts` |
| Exporter (`bun test`) | `scripts/nhprep-export/nhprep-export.test.ts` (not in the app's `bun run test`, which runs `./src` only) |
| End to end (headed) | `e2e/nhprep/stack.nhprep.ts`, `cards.nhprep.ts`, `video.nhprep.ts`; `e2e/security.e2e.ts` (`media-src`) |
| Eval | `scripts/eval_nhprep.py`, `evals/nhprep/dev.jsonl` |

**Backend in WSL.** As in [testing.md](testing.md#backend-tests-in-wsl), with this worktree as the source and an
NHprep database per lane (`netsec_nhprep_{l2,l3,l4,l6}_test`; `_int_test` for `test_migrations.py` and the full
suite, which also use the shared `aichat_migrations_test`, so book it). From Git Bash, snapshot a commit (or, for
work in progress, tar the working tree without `.venv`, caches and `.env*`):

```sh
git -C C:/Users/test/netsec-nhprep archive --format=tar <sha> backend docs/openapi.json \
  | wsl -e bash -c 'D=$HOME/netsec-claude/nhprep/l4-<sha12>; rm -rf "$D"; mkdir -p "$D"; tar -xf - -C "$D"'
# then, in WSL, from that backend/ with a venv built from requirements.lock:
TEST_DATABASE_URL=postgresql+asyncpg://aichat:aichat@127.0.0.1:5432/netsec_nhprep_l4_test \
  .venv/bin/python -m pytest -o addopts="" -ra tests/test_nhprep*.py tests/test_architecture_guards.py \
  tests/test_authorization.py tests/test_route_auth.py tests/test_account_deletion.py tests/test_foundation.py
```

At most 3 suites at once across every session; check exit codes separately from pipes.

**The exporter's tests**, from `frontend/apps/app`: `bun test scripts/nhprep-export`.

**The NHprep e2e suite** (`playwright.nhprep.config.ts`, headed Chromium, `workers: 1`): ports 3220 (app), 3221
(preview) and 4220 (API), build `.next-nhprep-e2e`, database `netsec_nhprep_e2e_test` in WSL, evidence in
`C:\Users\test\nhprep-data\e2e-evidence\runs\<time>\`. Bun cannot drive Playwright's browsers on Windows, so it runs
under Node:

1. **Database** (once): in WSL, create `netsec_nhprep_e2e_test` owned by `aichat` with the extensions `vector`,
   `citext`, `pgcrypto` and `pg_trgm`.
2. **Migrate** from `backend/` on Windows: `PYTHONUTF8=1 .venv/Scripts/python.exe -m alembic -x
   url=postgresql+asyncpg://aichat:aichat@127.0.0.1:5432/netsec_nhprep_e2e_test upgrade head`.
3. **Backend snapshot** (the API runs a copy outside the repository; exit codes 0-7 are success):
   `robocopy C:\Users\test\netsec-nhprep\backend C:\Users\test\nhprep-data\e2e-evidence\stack\backend-snapshot /MIR
   /XD .venv __pycache__ .pytest_cache .ruff_cache /XF .env*`.
4. **Run** from `frontend/apps/app`: `node ../../node_modules/@playwright/test/cli.js test -c
   playwright.nhprep.config.ts`. Save `tsconfig.json` before a building run and put its bytes back if `next build`
   changed it.

The API runs with `NHPREP_MODE=on`, students on `nh.example.com`, the fake model and fake embeddings. The specs draw
cards from the pinned fixture with `/api/v1/nhprep` and `videos.nhprep.com` stubbed in the browser: nothing is
imported, and the finder is not exercised end to end in a browser.

## The worktree workflow

The owner allowed one exception to "no worktrees" for this feature only:
- Worktree `C:\Users\test\netsec-nhprep`, local branch `nhprep` (no upstream), created from `origin/main`. The
  branch is **never pushed as a branch**; the release pushes `HEAD:main` from the worktree in an agreed window.
- The main checkout (`C:\Users\test\Desktop\AI- Tutor NH`) is not touched, and its gate is never run from this
  work (a run there would overwrite a peer's receipt). Never `git stash` or `git config` in the worktree.
- Reserved names: advisory lock `7_216_094_260`; e2e ports 3220/3221/4220; WSL databases
  `netsec_nhprep_{l2,l3,l4,l6,int,eval,e2e}_test`; WSL folder `~/netsec-claude/nhprep/`; export data
  `C:\Users\test\nhprep-data\`.
- **Release (R1)**, only with the owner's authorization, D4 resolved and the window agreed with the bank's release:
  rebase onto `origin/main` (renumber the migration if needed), `bun run contract`, the checks, `bun run security`
  in the worktree (its receipt), `git -C C:/Users/test/netsec-nhprep push origin HEAD:main`, then from the worktree
  `NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run deploy:netsec` (ends `Status: MATCH`). Peers pull with `--ff-only`
  later. NHprep stays `off` after R1; the import and `qa` are R2.

## Runbooks

Every production step needs the owner's explicit authorization. Nothing here has been run on the host: NOT TESTED.

### Export (PC)

From `frontend/apps/app`, in Git Bash, with free disk for the browser and about 25 MB a bundle:

```sh
bun build scripts/nhprep-export/export.ts --target=node --external @playwright/test \
  --outfile node_modules/.cache/nhprep-export/export.mjs
NHPREP_CREDENTIALS_FILE=<absolute path> node node_modules/.cache/nhprep-export/export.mjs --lessons --quiz
```

- **Options:** `--courses a,b`, `--categories a,b`, `--max-lessons N` (a partial export is never complete),
  `--max-attempts N` (optional; the owner set no cap), `--gap-ms N` (default 1200, never below 500),
  `--retry-rounds N` (2), `--retry-wait-s N` (120), `--submit-gap-s N` (20), `--resume <run folder>`.
  `NHPREP_EXPORT_DIR` changes the output root (it must be absolute and outside every git working tree);
  `NHPREP_BROWSER` the browser channel (default `msedge`).
- **Pacing:** one gate spaces every nhprep.com request at least `--gap-ms` apart (documents, scripts, JSON, sign-in,
  submits). Offsite hosts, images, media, fonts, prefetches, service workers and websockets are refused; a repeat
  of the player's `/api/video-url/{id}` is refused; NHprep's content-hashed static files are fetched once and then
  served from memory. A lesson costs about 7-8 requests and 8.5 s; the full export was 4,226 requests.
- **Rate limits:** quiz submits are spaced by `--submit-gap-s`; a 429 waits 90 s, doubling to 10 minutes, at most 6
  times, and does not count toward the stop rule. Other 429s are counted in the log.
- **Retries:** failed course pages, lessons and quiz categories are retried at the end of their phase after
  `--retry-wait-s`; what still fails is in `failed.json` / `failed-quiz.json`. `--resume <run folder>` retries
  exactly what is missing (a half-written last line is dropped first).
- **Quiz stop rule:** a category stops when seen = reported, or after 3 attempts with nothing new. The ledger
  (`<export root>\ledger.json`) counts accepted submits across runs.
- **Check:** `manifest.json` `complete.lessons` and `complete.quiz`, `maxRequestsPerSecond`, the counts and
  `failed*.json`. Keep the latest keyed bundle; the bank reads it.

### Offline embedding (host, production embedding key)

Allowed on the host only (decision Q4), with the owner's go-ahead for the quota, when no deploy runs. No database is
touched.

1. On the PC or in WSL, emit the texts with a dry run against a `*_test` database (it reads the database and takes
   the lock): `DATABASE_URL=<…_test> EMBEDDING_PROVIDER=fake python scripts/nhprep_import.py <run folder>
   --emit-texts C:/Users/test/nhprep-data/embed/<sha12>.in.jsonl`.
2. Ship the commit's `backend/app` and the script into a root-only tmpfs folder and run it there; texts go in on
   stdin and vectors come out on stdout; the folder is removed however the run ends:

```sh
WT=/c/Users/test/netsec-nhprep; H=root@10.1.50.94
SHA=$(git -C "$WT" rev-parse --verify HEAD); S=${SHA:0:12}; U=netsecai-nhprep-embed-$S-$(date -u +%H%M%S)
IN=/c/Users/test/nhprep-data/embed/$S.in.jsonl; OUT=/c/Users/test/nhprep-data/embed/$S.out.jsonl
ssh $H 'test ! -e /run/netsecai-deploying || { echo "a deploy is running"; exit 1; }
        grep -cE "^(LANGCHAIN_TRACING_V2|LANGSMITH_TRACING)=true" /etc/netsecai/api.env; findmnt -no FSTYPE /run'
git -C "$WT" archive --format=tar "$SHA" backend/app backend/scripts/nhprep_embed_offline.py \
  | ssh $H "set -e; umask 077; rm -rf /run/$U; mkdir /run/$U; tar -xf - -C /run/$U"
ssh -o ServerAliveInterval=30 $H "trap 'rm -rf /run/$U' EXIT; systemd-run --pipe --wait --collect --quiet --unit=$U \
  --slice=netsecai-knowledge.slice -p MemoryMax=512M -p MemorySwapMax=0 -p CPUQuota=50% -p TasksMax=32 -p RuntimeMaxSec=3600 \
  -p NoNewPrivileges=yes -p ProtectSystem=strict -p ProtectHome=yes -p PrivateTmp=yes \
  -p EnvironmentFile=/etc/netsecai/api.env -p WorkingDirectory=/run/$U/backend \
  --setenv=PYTHONPATH=/run/$U/backend --setenv=PYTHONDONTWRITEBYTECODE=1 \
  /opt/netsecai/app/backend/.venv/bin/python /run/$U/backend/scripts/nhprep_embed_offline.py" < "$IN" > "$OUT"; echo "exit $?"
ssh $H "ls -d /run/netsecai-nhprep-* 2>/dev/null | wc -l"     # 0
```

3. Expect 0 tracing lines and `tmpfs` before the run, `exit 0`, and a vectors file with one header line plus one
   line per distinct text. Then import in WSL with `--vectors "$OUT" --egress-basis <basis>`; a second run must
   report 0 changes and 0 embeddings.

### Host import (R2)

After R1 is live, with the owner's authorization, NHprep `off`, no deploy, no knowledge worker or extractor running.
The import holds the deploy lock (`/run/netsecai-deploy.lock`) for its whole run, so a deploy refuses meanwhile.
The bundle crosses only the SSH pipe (stdin), so it is never complete: this import never removes.

```sh
H=root@10.1.50.94; B=/c/Users/test/nhprep-data/export/<run>/export.jsonl
SUM=$(sha256sum "$B" | cut -c1-64); U=netsecai-nhprep-import-$(date -u +%Y%m%d%H%M%S)
NETSEC_DEPLOY_HOST=$H bun run status:netsec                           # MATCH
ssh $H 'test ! -e /run/netsecai-deploying || { echo "a deploy is running"; exit 1; }
        grep -E "^NHPREP_MODE=" /etc/netsecai/api.env || echo "NHPREP_MODE unset (off)"
        grep -cE "^(LANGCHAIN_TRACING_V2|LANGSMITH_TRACING)=true" /etc/netsecai/api.env
        df -Pm /opt/netsecai | tail -1
        systemctl is-active netsecai-knowledge.service; systemctl list-units --no-legend "netsecai-extract-*" | wc -l'
DUMP=$(ssh $H 'systemctl start netsecai-pg-backup.service && ls -1t /var/backups/netsecai/netsecai-*.dump | head -1') && echo "$DUMP"
imp() { ssh -o ServerAliveInterval=30 $H "touch /run/netsecai-deploy.lock && systemd-run --pipe --wait --collect --quiet --unit=$U-$1 \
  --slice=netsecai-knowledge.slice -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=100% -p TasksMax=64 -p Nice=10 \
  -p RuntimeMaxSec=10800 -p TimeoutStopSec=60 -p NoNewPrivileges=yes -p ProtectSystem=strict -p ProtectHome=yes -p PrivateTmp=yes \
  -p ReadWritePaths=/run/netsecai-deploy.lock -p EnvironmentFile=/etc/netsecai/api.env -p WorkingDirectory=/opt/netsecai/app/backend \
  --setenv=PYTHONDONTWRITEBYTECODE=1 /usr/bin/flock -n -E 75 /run/netsecai-deploy.lock \
  /opt/netsecai/app/backend/.venv/bin/python scripts/nhprep_import.py --operator <name> --sha256 $SUM ${*:2} -" < "$B"; echo "exit $?"; }
imp dry                                                               # validates and plans; writes nothing
imp apply --apply --backup "$DUMP" --egress-basis <basis-id>          # owner-authorized; no spaces in values
imp check                                                             # a second dry run: 0 changes, 0 texts to embed
NETSEC_DEPLOY_HOST=$H bun run status:netsec                           # still MATCH
```

- The import embeds on the host with the production key through `build_embeddings` (masked); `--egress-basis` is
  recorded. The helper passes its arguments through a remote shell unquoted, so values must have no spaces. Any
  non-zero exit (75: a deploy holds the lock) means stop and read the output.
- Watch during the run: `journalctl -u netsecai-api` for slow generations, `/ready`, and the API's p95. Emergency:
  `ssh $H systemctl stop <unit>`; the run is then `failed` or, on the next run, `interrupted`, and a rerun resumes.
- Afterwards: `ANALYZE` the `nhprep_*` tables as the migrator, run `eval_nhprep.py --latency` on the host, then the
  owner sets `NHPREP_MODE=qa` with `NHPREP_QA_EMAILS` in `api.env` and restarts the API.

### Rollback

1. `NHPREP_MODE=off` in `/etc/netsecai/api.env` (the owner edits it), then restart `netsecai-api`: no card turn,
   every route 404, stored cards hidden (TESTED in pytest: `test_build_chat_is_nothing_while_nhprep_is_off`,
   `test_mode_off_and_qa_narrow_who_nhprep_is_for`).
2. If the code must go too: `NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run rollback:netsec [-- <commit>]`. The database
   stays at 0027; an older release reports the schema `ahead`, which is ready. That an older release boots on a 0027
   database holding NHprep rows is NOT TESTED.

### Purge (owner approval only)

Deletes the whole catalogue (courses cascade to lessons, items, chunks and trainer links) with `DELETE`;
`nhprep_sync_runs` stays. Turn `NHPREP_MODE` off first. Then, under the deploy lock with the `imp` helper above
(the purge ignores stdin; values without spaces): a dump of the `nhprep_*` tables except the derived
`nhprep_search_chunks` (`pg_dump -Fc -t 'nhprep_*' -T nhprep_search_chunks`) or a recorded waiver, then
`imp purge-dry --purge --reason <reason-id> --backup <dump path>` (prints the counts), then the same with
`--apply`. NOT TESTED on the host.

### Key and secret rotation (Revision 5)

Once a signer exists: rotate the secret (option A) or the endpoint's credential (option B) on NHprep's side, the
owner writes the new value into `/etc/netsecai/api.env` (agents only check with `grep -c`), then restart
`netsecai-api`. Open players recover by themselves: their next error gets one fresh link; after that, **Reload
video**. A secret committed by mistake is rotated, not just removed from history.

### Clean-up (after the final release)

`git worktree remove` the worktree (unlock first if locked; in WSL use `rm` with `MSYS_NO_PATHCONV=1` for long
paths), `git worktree prune`, `git branch -d nhprep`; drop every `netsec_nhprep_*_test` database; remove
`~/netsec-claude/nhprep` in WSL and the e2e evidence folder. **Keep the latest keyed export bundle and its
manifest**; delete only the recon, e2e and embedding files.

## Extension points

- **A signer** for option A or B: one class with `sign()` in `playback.py`, `build_signer`, and the
  `validate_for_env` refusal for that mode (above).
- **More NHprep sections** (labs, other course types): new `type` records in a later export schema, new item kinds
  (the `kind` checks and `AID_KINDS`), and new views in `types.View`; the card's `version` stays 1 while the change
  is additive.
- **Tuning:** `tuning.json` weights and thresholds, re-measured with `eval_nhprep.py`; any change to `intent.py`
  needs the held-out split run again.
- **Trainers and tracks:** `normalize.TRAINER_ALIASES` and the course-to-track map, both owner-confirmable.
- **Captions:** none exist yet (a known accessibility gap); the player has room for a `<track>`.
- Not in phase 1: My Learning, labs, mock or AI interviews, NHprep text in normal answers, a search page, a
  scheduled sync.
