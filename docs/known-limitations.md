# Known limitations

> The [NetSec AI](#netsec-ai-2026-10-29) section was checked against the working tree on 2026-10-29 (the final
> review). The other sections were written for PadhAI and verified at commit `0e33ec0` (2026-10-26); they are not
> yet re-verified for NetSec ([README.md](README.md)). Production states live in the server's settings, not in git.

These are the gaps that are true today, each with where it shows. A limitation that was chosen on purpose links
to its reason in [decisions.md](decisions.md). Planned work is in [ROADMAP.md](../ROADMAP.md).

## Contents

- [NetSec AI (2026-10-29)](#netsec-ai-2026-10-29)
- [QA closure (2026-11-18)](#qa-closure-2026-11-18)
- [Interactive learning blocks (2026-11-18)](#interactive-learning-blocks-2026-11-18)
- [For students](#for-students)
- [Files](#files)
- [Production and operations](#production-and-operations)
- [Development and testing](#development-and-testing)
- [Platform](#platform)

## NetSec AI (2026-10-29)

From the final review of the working tree. Each is true of the code today; none is a security hole on its own.

**The product**

| Limitation | Where it shows |
|---|---|
| **Certification practice still scopes by the school class and subjects.** There is no certification question bank yet (`qbank/` holds PadhAI's NCERT content): practice topics need a class level (reason `no_class`), the plan needs a subject list, chat tests find the level only from a "Class N" grade or "class/grade/std N" in a message, and the practice requests put into a chat still say "Class N … Chapter N". Needs the owner's certification bank and scoping by the Engineering Profile | `backend/app/progress/practice.py:400-409, 713, 779-785`; `backend/app/qbank/chat_tests.py:61, 178-198`; `frontend/apps/app/src/features/my-learning/practice-picker.tsx:18-27` |
| **The lab session's "Exam track" picker lists school subjects** (Mathematics, Science, Sanskrit, …) from the academic catalog, and Lab Activity's "By exam track" names them the same way, until the certification catalog lands | `frontend/apps/app/src/features/study/study-session-panel.tsx:120-136`; `study-activity-view.tsx:222`; `backend/app/learning/catalog.py:119` (`SUBJECTS`) |
| **The academic fields (class, medium, subjects) remain in the API and the database**, hidden from Settings, and still choose the question bank's level and language, the practice plan's order and the dormant NCERT source's ranking | `backend/app/chat/service.py:1088` (`chat_tests.menu`); `backend/app/chat/assistant.py:196-206` (`retrieval_for`); `backend/app/progress/practice.py` |
| **The empty chat shows a fixed pool of engineering greetings.** The weekly LLM-refreshed pool (its prompt now written for engineers) is not shown: the web app no longer calls `GET /v1/greetings`, which is what starts a week's refresh | `frontend/apps/app/src/lib/greeting.ts` (`GREETINGS`); `backend/app/greetings/refresh.py` |
| **Eight answer preferences and the Skills & Certification Goals are saved in Settings but not given to the assistant.** Only language, explanation level, visual explanations, response length, answer mode and step by step reach it, with the Engineering Profile; pace, difficulty, real-world examples, tone, ask-before-answer, practice questions, mistake correction and key-concept highlighting sit under "Not used by NetSec AI right now" | `backend/app/learning/tutor.py:59` (`TEACHING_TEXT`), `:125` (`_about_lines`); `frontend/apps/app/src/features/settings/preferences-sections.tsx:67-92`; `goals-section.tsx` |
| **The frozen prompt does not teach the chart block, output blocks or math**, although the web app still renders them | `backend/app/chat/assistant.py:63-73`; `frontend/apps/app/src/features/markdown/rich-block.tsx:15` (chart), `message-content.tsx:77` (output), `:5-8` (KaTeX) |

**The web app, sign-in and deploys**

| Limitation | Where it shows |
|---|---|
| **Uploads are not capped per user in the BFF.** Each process allows 2 uploads at once, and a slow body holds a slot for up to 300 s before the body deadline answers 408, so one account can keep both slots busy | `frontend/apps/app/src/lib/server/proxy-core.ts:129-130, 202` |
| **`/api/*` is outside the middleware's matcher**, so its HTTP → HTTPS upgrade (308) does not apply to API routes. Cloudflare serves the site over HTTPS, and the `__Host-` cookies need HTTPS, so no session is sent over plain HTTP | `frontend/apps/app/src/middleware.ts:22-26, 48` |
| **The first deploy of the `__Host-netsecai.*` cookie names interrupts sign-ins in progress:** a two-step code or a Google redirect started before it fails, for up to 10 minutes (the two-step cookie's life). Existing sessions keep working (the session cookie's name is unchanged) | `frontend/apps/app/src/lib/auth/config.ts:213, 313` |
| **A rollback to any ancestor of GitHub `main` skips the security gate**, without a log line: the check is ancestry, not that the commit was ever released | `deploy-netsec/deploy.sh:93-99`; [rollback.md](rollback.md) |
| **A rollback runs the local `HEAD`'s `deploy-netsec/` scripts as root on the host**, with no gate, clean-tree or `HEAD` = `main` check for them | `deploy-netsec/deploy.sh:87-90, 117` |
| **Gate overrides are logged only in the local checkout's `.git`** (`netsec-security-gate.overrides`); the host's `HISTORY` does not record the gate's outcome | `deploy-netsec/deploy.sh:61`; `deploy-netsec/server/update.sh:285` |
| **The gate's receipt names a commit, but its scanners read the working tree**: a sparse-checkout or skip-worktree file can differ from that commit while `git status` stays clean (the history scan reads commits) | `scripts/security.sh:62-78` |
| **A deploy that changes the frontend's dependencies runs `bun install --force` in the live `node_modules`**: the running web app serves from a reinstalled tree during the build, until its restart; a failed install leaves it on a partly reinstalled one. The first deploy of the 2026-10-29 dependency changes takes this path | `deploy-netsec/server/update.sh:143` |

## QA closure (2026-11-18)

Verified on production (`f7727c5`, MATCH) and on the local production build. Evidence:
`C:\Users\test\Desktop\netsec-qa-evidence\2026-11-18\` on the owner's PC (not in git).

| Limitation | Where it shows |
|---|---|
| **NetSec's nightly backup is off (the owner's decision, 2026-11-18).** `netsecai-pg-backup.timer` is disabled; the newest dump is `netsecai-2026-11-18-031945.dump`. Every dump carries the book PDFs (`document_file_blobs`, 2.7 GB of a 3.3 GB database; that dump is 2.54 GB) and the gate wants twice the database free (6.6 GB). About 7.0 GB is free after the 2026-11-18 cleanup, so one more dump would leave less than the 5 GB that uploads and imports need (`MIN_FREE_DISK_BYTES`). A deploy that runs `install-units.sh` re-enables the timer: disable it again afterwards. `status:netsec` reports the disabled timer and the ageing dump as failures | `deploy-netsec/backup.sh` (the 2× gate), `app/files/service.py`; needs a larger VM disk or a backup-policy decision by the owner |
| **The citation tooltip does not always open on the first keyboard focus after a page load** (probe: 1 of 30 focus cycles; seen once live). The chip's accessible name and the Sources list under the reply always carry the source | `features/markdown/citation.tsx` (Radix Tooltip 1.1.8); `e2e/p2-headed-verification.e2e.ts` asserts the name and the focus ring instead |
| **Azure coverage is partial.** The Microsoft Learn training content was removed on 2026-11-18 (its Terms of Use limit it to personal, non-commercial use) and replaced with 44 CC BY 4.0 articles from MicrosoftDocs/azure-docs; availability zones, VMs, Monitor, Key Vault, AKS, Defender, Arc, Advisor, Service Health and Entra ID are not covered yet | `docs/certification-coverage.md` (azure row) |
| **The Fortinet library has no official exam blueprint.** Fortinet publishes NSE 4 exam objectives only behind its training login; the agent-written "program reference" was deleted on 2026-11-18 | Library on production; the owner can download the official exam description |
| **AWS SAA-C03 has no current-edition study book.** The library has the 2017 Official Study Guide (SAA-C01 era) and the official SAA-C03 exam guide (objectives only) | Library sources on production; needs a licensed current guide from the owner |
| **The 0.01 ms reduced-motion clamp is gone for transitions** (`transition: none`): it left hundreds of transitions reported as running in `document.getAnimations()`, so settle-waits never settled (the visual flakes). Animations keep the 0.01 ms clamp | `packages/ui/src/globals.css`; A/B evidence in the QA evidence folder |

## Interactive learning blocks (2026-11-18)

The chat's learning blocks (quizzes, mock exams, CLI and key-point questions, ordering, matching, flashcards,
diagrams, charts, videos) render in the web app. What they do not do yet:

| Limitation | Where it shows |
|---|---|
| **They are dormant in production.** Nothing teaches the model to write them (the system prompt is unchanged), and `QUESTION_BANK_TESTS` (on) turns a test the model writes into a note | `app/chat/assistant.py`; `app/qbank/chat_tests.py` |
| **A mock exam's answer keys are readable in the reply's raw text** (devtools, the network response). The UI, Copy, export and the source fallbacks mask them | `features/markdown/` (masking happens in the client) |
| **Key-point and CLI grading are proxies:** the author's accepted forms plus a keyword table. "Get feedback" asks the tutor | `backend/app/assessments/kinds.py` (server-side grading); "Get feedback" in `widgets/test/review.tsx` |
| **An unsubmitted practice answer is lost on a full page reload** | the block's client state |
| **Mermaid diagrams and charts print in the colours they were drawn with** (dark if drawn in dark mode) | `widgets/mermaid-diagram.tsx`, `widgets/chart-view.tsx` |
| **Pressing Play sends the user's IP address, our origin and the video to Google** (nothing loads from YouTube before Play). A video that won't embed shows YouTube's own error; "Open on YouTube" stays visible | `widgets/video.tsx` |
| **`@dnd-kit/react` is a 0.x release** (0.5.3). Ordering also works without dragging (Move up / Move down) | `frontend/apps/app/package.json` |
| **Narrower than a full exam engine:** one scenario per test, matching has no drag, flashcards have no shuffle | `features/markdown/widgets/` |

## For students

| Limitation | Where it shows |
|---|---|
| **Voice mode is English only.** It is offered only when the tutor's language is English or unset | `app/voice/`; OllaLink's speech has no Hindi |
| **Textbook search covers NCERT Classes 6–12 only**, and needs the separate NCERT Knowledge Service. Without the service, answers come without textbook sources | `app/rag/ncert.py` (`SERVED_CLASSES`) |
| **Tests in the chat exist only for what the question bank covers** | `app/qbank/chat_tests.py` |
| **The tutor can be wrong.** Its own fill-in-the-blank exercises are marked, but never counted as evidence of what a student knows | [CLAUDE.md](../CLAUDE.md), "Backend internals" |
| **If the model fails partway through a reply, that reply ends with an error.** The fallback model is used only before the first word | `app/llm/fallback.py`; [decisions.md](decisions.md#one-model-call-per-turn-and-the-fallback-only-before-the-first-token) |
| **Learning days follow India time** for every student | `app/progress/learning.py` (`Asia/Kolkata`) |
| **Dictation depends on the browser's speech recognition.** Without it, there is no dictation button | `features/chat/dictation.ts` |
| **On a phone, the year's activity calendar scrolls sideways** | Settings > Learning & Streak ("Scroll sideways to see the whole year") |
| **The teaching defaults are off** by default | `TUTOR_TEACHING_POLICY` |
| **The adaptive CAPTCHA isn't configured in production** (2026-10-26) | `TURNSTILE_*` unset |
| **Homework Helper and Summarizer are sample-data previews** and never appear in production | `PREVIEW_FEATURES` |

## Files

| Limitation | Where it shows |
|---|---|
| 100 MB per student. Per file: PDF, Word and PowerPoint 25 MB; Excel and images 10 MB; CSV and text 2 MB | The File Library page |
| At most 5 attachments per message. A turn reads the conversation's 5 most recently attached files, up to 4,000 passages in total | `app/files/service.py` (`MAX_ATTACHMENTS`, `SCOPE_FILES`, `SCOPE_CHUNKS`) |
| Uploads stop when PadhAI's own tables reach 3 GiB, or the disk has less than 5 GB free | `app/files/service.py` (`check_capacity`) |
| Images and scanned pages need Tesseract (English and Hindi) on the server | `app/files/extract.py` |
| Files' bytes are stored in PostgreSQL, so the database grows with uploads | `user_file_blobs`; [decisions.md](decisions.md#files-bytes-live-in-postgresql) |

## Production and operations

| Limitation | Where it shows |
|---|---|
| **The jobs worker isn't enabled.** So nothing sends reminder emails (daily, streak, weekly summary) or forwards support requests by email, and the outbox redaction and the read-notification purge don't run. Sign-in codes and security emails are not affected: the API sends them directly | `JOBS_ENABLED`, `NOTIFICATIONS_MODE`; [deployment.md](deployment.md#jobs-worker) |
| **No fallback model is configured in production** (2026-10-26), so if the primary provider is down, replies fail until it is back | `LLM_FALLBACK_MODEL` unset; [operations.md](operations.md#runbooks) |
| **One host:** no staging environment, no replica, no load balancing. A host failure is an outage until restored | [INFRASTRUCTURE.md](../INFRASTRUCTURE.md), "Known limits" |
| **The API is one process**, and it tracks replies in flight in that process | `aichat-api.service`; `app/chat/streams.py` |
| **A deploy that restarts the API ends any reply being written at that moment**; it is saved as cancelled | [INFRASTRUCTURE.md](../INFRASTRUCTURE.md), "Known limits" |
| **The services run as root, and there is no alerting.** `bun run status:netsec` and `infra-check.sh` are run by hand | `deploy-netsec/systemd/`; [INFRASTRUCTURE.md](../INFRASTRUCTURE.md) |
| **Backups stay on the same host, unencrypted**, and are kept for 7 days. Copying them off the host is not set up: an encrypted off-host copy is an owner decision not yet made. A deleted account's data stays in the dumps taken before the deletion, until they rotate out | `deploy-netsec/backup.sh` (`KEEP_DAYS`) |
| **No metrics, tracing or backend error reporting.** Browser errors are reported only when `NEXT_PUBLIC_SENTRY_DSN` is set | [operations.md](operations.md#logs-and-what-they-contain) |

## Development and testing

| Limitation | Where it shows |
|---|---|
| **No Docker on the owner's PC** (the owner's rule): the backend tests run in WSL against a `*_test` database; the end-to-end suite and `bun run verify` do not run there | [testing.md](testing.md#where-each-layer-runs) |
| **The local setup steps are verified by reading the code**, not by running them on that PC; macOS isn't verified at all | [beginner-guide.md](beginner-guide.md) |
| **The production template leaves settings out:** `api.env.example` lists 63 of the 147 API settings; the voice rollout switches and limits, the question-bank switches, the memory worker's bounds and the `RAG_*` tuning keep their defaults unless set | [environment-variables.md](environment-variables.md#what-the-production-template-lists) |
| **`HOST` and `PORT` are declared in the API's settings, but never read.** uvicorn's flags decide | `app/settings.py`; `scripts/run.mjs` |
| **CI does not run:** GitHub Actions are out of credit, so CI is reported as NOT RUN; the enforced check is the local gate `bun run security` | [testing.md](testing.md#ci) |
| **The visual regression baselines were recorded on Windows**, so they are meant to run there | `e2e/visual-regression.e2e.ts` |
| **Fake embeddings only match shared words.** They prove the plumbing, not search quality | [rag.md](rag.md) |

## Platform

| Limitation | Where it shows |
|---|---|
| **The web app is the only client.** Native apps would need a published API and the changes listed in [architecture.md](architecture.md), "Clients" | `docs/architecture.md` |
| **The API has no public address.** Only the web app's server can reach it | [security.md](security/README.md) |
