# NetSec AI documentation

Every document in the repository, by what you need to do. **Status** says how far to trust a page:

- **Current:** written or re-checked for NetSec AI on 2026-10-29.
- **PadhAI-era:** written for PadhAI, the school tutor this repository was forked from, and verified at `0e33ec0`
  (2026-10-26). The architecture and code paths mostly still hold; product names, school wording, production hosts
  (`/opt/aichat`, `ayur.namahos.com`, port 4100) and commands (`bun run deploy`) do not. Check the code before
  relying on one.
- **History:** kept as a record; not instructions.

Where a PadhAI-era page and a current page disagree, the current page (and the code) wins.

## Rules and orientation

| Page | What it gives you | Status |
|---|---|---|
| [CLAUDE.md](../CLAUDE.md) | The canonical working rules for people and agents: owner's rules and directives, architecture, production facts | Current |
| [AGENTS.md](../AGENTS.md) | Pointer for non-Claude agents, with the four rules that matter most | Current |
| [frontend/CLAUDE.md](../frontend/CLAUDE.md) | Working rules and architecture of the web app | Current |
| [README.md](../README.md) | What NetSec AI is, what works, limitations | Current |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | How a change gets from your computer to production | Current |
| [ROADMAP.md](../ROADMAP.md) | What comes next | Current |
| [start-here.md](start-here.md) | Orientation and your first hour | PadhAI-era |
| [glossary.md](glossary.md) | Terms in plain words | PadhAI-era |
| [features.md](features.md) | The feature catalogue and production states | PadhAI-era |
| [known-limitations.md](known-limitations.md) | Verified gaps | PadhAI-era |
| [decisions.md](decisions.md) | Choices that shaped the code, with evidence | PadhAI-era |
| [CHANGELOG.md](../CHANGELOG.md) | Milestones by date and commit | PadhAI-era |

## Security

| Page | What it gives you | Status |
|---|---|---|
| [security/README.md](security/README.md) | The security model: trust boundaries, sign-in, ownership, secrets, uploads, AI risks, privacy | Current |
| [security/THREAT_MODEL.md](security/THREAT_MODEL.md) | Assets, actors, attack paths, the invariants and the tests that hold them | Current |
| [security/secure-development.md](security/secure-development.md) | The workflow for every change: before planning, during, before completion; the planning template | Current |
| [security/security-tools.md](security/security-tools.md) | The local gate `bun run security`, the scanners, the vendored skills | Current |
| [security/security-audit.md](security/security-audit.md) | The 2026-10-29 audit: method, findings and their status | Current |
| [security/security-regression-tests.md](security/security-regression-tests.md) | Which test protects which property, and how to run the backend tests in WSL | Current |
| [security/production-security-checklist.md](security/production-security-checklist.md) | Release acceptance, live probes, secrets on the host, rotation, backups, open follow-ups | Current |
| [security.md](security.md) | A pointer to `security/README.md` | – |
| [SECURITY.md](../SECURITY.md) | How to report a vulnerability | Current |
| [.claude/skills/SOURCES.md](../.claude/skills/SOURCES.md) | Provenance and review of the vendored security skills | Current |

## Developers

| Page | What it gives you | Status |
|---|---|---|
| [development.md](development.md) | Local setup and commands on the owner's PC (no Docker; WSL) | Current |
| [testing.md](testing.md) | Test layers, where each runs, backend tests in WSL, CI not running | Current |
| [graphify.md](graphify.md) | The code knowledge graph: commands, boundaries, limits | Current |
| [environment-variables.md](environment-variables.md) | Every setting of the API (all 147), the web app, the database and the tests | Current |
| [architecture.md](architecture.md) | The architecture, its extension points, and what native clients would need | PadhAI-era |
| [codebase-map.md](codebase-map.md) | Every module of the API and the web app | PadhAI-era |
| [backend-architecture.md](backend-architecture.md) | The API: start-up, layers, domains, background work, errors, guards | PadhAI-era |
| [frontend-architecture.md](frontend-architecture.md) | The web app: pages, sign-in, the proxy, state, chat, rendering, Settings, voice | PadhAI-era |
| [request-lifecycle.md](request-lifecycle.md) | One chat message end to end | PadhAI-era |
| [api-contract.md](api-contract.md) | The API's conventions and resources ([openapi.json](openapi.json) is the generated contract, always current) | PadhAI-era |
| [api-reference.md](api-reference.md) | Every route on every surface | PadhAI-era (Engineering Profile routes added 2026-10-29) |
| [frontend-integration.md](frontend-integration.md) | What the web app relies on from the API | PadhAI-era |
| [data-model.md](data-model.md) | Tables, relationships, vectors, retention, migrations | PadhAI-era (to `0017`; the chain is now at `0020_engineering_profiles`) |
| [adding-a-feature.md](adding-a-feature.md) | The path from a table to production | PadhAI-era |
| [beginner-guide.md](beginner-guide.md) | Step-by-step setup with Docker | PadhAI-era |
| [troubleshooting.md](troubleshooting.md) | Symptoms, causes and fixes | PadhAI-era |
| [rebuild-prompt.md](rebuild-prompt.md) | A prompt for an agent to set up and verify the project | PadhAI-era |
| [screenshots/README.md](screenshots/README.md) | Screenshots of PadhAI's live site (2026-10-26) | History |

## AI and data

| Page | What it gives you | Status |
|---|---|---|
| [ai-architecture.md](ai-architecture.md) | Providers, what the model is told, the intent router and scope filter, retrieval, voice, flags | PadhAI-era (the NetSec prompt is `SYSTEM_PROMPT` in `backend/app/chat/assistant.py`) |
| [rag.md](rag.md) | Retrieval: the pgvector knowledge base, files, embeddings, the NCERT service (dormant here) | PadhAI-era |
| [memory-evaluation.md](memory-evaluation.md) | `memory_eval.py`: the memory evaluation modes and gates | Current for memory (2026-10-28) |
| [memory-regression-audit.md](memory-regression-audit.md) | The persistent-memory regression audit | 2026-10-28 |
| [question_bank.md](question_bank.md) | The question bank: content, import, selection, chat tests | PadhAI-era (content is still NCERT) |
| [question_bank_coverage.md](question_bank_coverage.md), [question_bank_queries.sql](question_bank_queries.sql) | Stored questions per book and topic; SQL for inspecting the bank | PadhAI-era |
| [qbank/README.md](../qbank/README.md) | The question bank's source files | PadhAI-era |

## Operators

| Page | What it gives you | Status |
|---|---|---|
| [deployment.md](deployment.md) | How a commit goes live (`deploy:netsec`), the security gate, sign-in setup, the Quick Tunnel, the jobs worker, owner-decision switches | Current |
| [operations.md](operations.md) | Health, logs, backups and runbooks | Current |
| [rollback.md](rollback.md) | Going back to an earlier commit, and the database | Current |
| [production-migration.md](production-migration.md) | How NetSec production was set up beside PadhAI | Current (history) |
| [INFRASTRUCTURE.md](../INFRASTRUCTURE.md) | The shared host, NetSec's layout and known limits | Current |
| [deploy/README.md](../deploy/README.md) | PadhAI's deploy scripts, kept as history; they refuse to run here | History |
| [self-hosted/glitchtip/README.md](../self-hosted/glitchtip/README.md) | An optional error collector (not deployed) | PadhAI-era |

## Inside the two halves

| Page | What it covers | Status |
|---|---|---|
| [backend/README.md](../backend/README.md) | The API's own overview | PadhAI-era |
| [frontend/README.md](../frontend/README.md) | The web app's own overview | PadhAI-era |
| [frontend/docs/frontend-api.md](../frontend/docs/frontend-api.md) | How the web app's code calls the API | PadhAI-era |
| [frontend/ROADMAP.md](../frontend/ROADMAP.md) | The web app's deferred work | PadhAI-era |
| [LICENSE.md](../LICENSE.md), [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) | Licence and third-party notices | – |
