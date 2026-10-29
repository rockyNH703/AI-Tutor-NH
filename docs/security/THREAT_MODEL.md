# Threat model

> Written 2026-10-29 for NetSec AI, against the code on top of `e439b96`, from the code, the
> [2026-10-29 audit](security-audit.md) and its evidence ledger, corrected after the final review the same day.
> **(released 0d868b7)** marks a control that is fixed, and tested unless the row says otherwise, and was deployed to
> production on 2026-10-29 (`Status: MATCH`). Audit findings are `A-<n>`; the earlier triage is `F-*`
> ([security-audit.md](security-audit.md#earlier-triage-f-)). Where an invariant is held only by review, it says so.

NetSec AI is a network, security and infrastructure engineering assistant: a chat with retrieval, attached files
and opt-in memory, forked from the PadhAI school tutor and being adapted. Its users are engineers. They paste
device configurations, logs and captures, which often contain secrets and internal topology. The design is in
[README.md](README.md) (the security model) and [architecture.md](../architecture.md).

## Contents

- [Assets and sensitive data](#assets-and-sensitive-data)
- [Trust boundaries and entry points](#trust-boundaries-and-entry-points)
- [Threat actors](#threat-actors)
- [Security invariants](#security-invariants)
- [Attack paths and abuse cases](#attack-paths-and-abuse-cases)
- [Residual risks](#residual-risks)
- [Mapping to OWASP and ASVS](#mapping-to-owasp-and-asvs)

## Assets and sensitive data

| Asset | Where it lives | Why it matters |
|---|---|---|
| Credentials: password hashes, two-step secrets and recovery codes | `users`, `auth_accounts`, `auth_two_factors` (two-step data encrypted with `BETTER_AUTH_SECRET`) | Account takeover |
| Sessions and sign-in codes | The browser's `__Host-padhai_session` cookie; `auth_sessions`, `auth_verifications`, `email_codes` | A live token or code is a way into an account. Backups leave these rows out |
| Chats | `conversations`, `messages` | Pasted configurations, logs and topology. **Stored as the user wrote them**: masking applies only to what leaves for a provider |
| Files | `user_files`, `user_file_blobs` (bytes), `file_chunks` (text and vectors) | Same content as chats, at larger scale; untrusted bytes |
| Memory | `memory_items`, `memory_proposals`, `memory_extraction_jobs`, `memory_command_receipts`, `memory_deletion_markers` | Durable personal context that can reach future prompts; consent and deletion promises |
| Profile and settings | `engineering_profiles`, `user_settings` (memory consent among them), `user_avatars` | What the assistant is told about the user; consent integrity |
| Knowledge base | `knowledge_sources`, `documents`, `document_chunks` | Global sources reach every user's prompt; per-user sources only their owner's |
| Secrets | `/etc/netsecai/{api,web,postgres,migration}.env` on the host; the root key files and the deploy SSH key on the release workstation | Provider spend, database access, cookie forgery, production access |
| Backups | `/var/backups/netsecai` on the same host | A copy of everything above except tokens, codes and file chunks; unencrypted |
| Availability | One web process, one API process, one database container | Every user shares them |
| Release integrity | GitHub `main`, the gate receipt `.git/netsec-security-gate`, `/opt/netsecai/app.git` | What runs as root on the host |
| PadhAI production | `/opt/aichat`, `/etc/aichat`, `aichat-*` units, on the same VM | A separate service with separate users; NetSec must never touch it |

## Trust boundaries and entry points

| # | Boundary | Entry points | Main controls |
|---|---|---|---|
| B1 | Browser → Cloudflare edge → web app | Pages; `/api/auth/*` (Better Auth, exact allowlist); `/api/v1/*` (the BFF); `/api/account/password`; `/api/session/expired` | HTTPS at the edge; Quick Tunnel to `127.0.0.1:4300` only; `__Host-` cookies; CSP; `Origin` checks; body limits ([README.md](README.md#the-web-apps-protections)) |
| B2 | Web app (BFF) → API | `/v1/*` on `127.0.0.1:4400` with a bearer token; `/internal/auth/*` with `AUTH_INTERNAL_TOKEN` | Route allowlist; HMAC-verified cookie before the token is used; the API authorises every request |
| B3 | Web app and API → PostgreSQL | `127.0.0.1:55432`; roles `netsecai_auth` (identity tables only), `netsecai_runtime`, `netsecai_migrator` | Loopback publish; least-privilege roles ([`database-grants.sh`](../../deploy-netsec/database-grants.sh)); bound SQL parameters |
| B4 | API and jobs → LLM and embeddings providers | Prompts, history, file passages, memory text, search queries | HTTPS; secrets masked first **(released 0d868b7)**; one model call per turn; no tools |
| B5 | API → Ollalink and email providers | Classifier text, voice audio and text (off in the example env); email messages | HTTPS enforced in production for Ollalink; not masked |
| B6 | API → file extractor | Uploaded bytes | Sandboxed transient systemd unit per file |
| B7 | Release workstation → host | `deploy-netsec/deploy.sh` over SSH as root | Clean tree, GitHub `main` only, PASS receipt **(released 0d868b7)**, deploy lock, backup before migrations |
| B8 | Cloudflare edge | Quick Tunnel on the shared parent domain `trycloudflare.com` | `__Host-` cookies **(released 0d868b7, A-0)**; the address changes when cloudflared restarts (`status:netsec` checks it) |
| B9 | NetSec ↔ PadhAI on one host | None by design | Separate namespace, hard-coded as `netsecai` names, kept by [`namespace.test.mjs`](../../deploy-netsec/tests/namespace.test.mjs) and the database identity checks ([README.md](README.md#trust-boundaries)); [`safety.sh`](../../deploy-netsec/server/safety.sh) only self-checks NetSec's constants; the extractor hides PadhAI's paths **(released 0d868b7)**; but all services run as root (A-21) |

## Threat actors

| Actor | Can | Wants |
|---|---|---|
| Anonymous internet user | Reach the public hostname; create an account by email code; send any request through the tunnel | Accounts, other users' data, free model use, outage |
| Signed-in user | Everything a user can do, with their own session; send crafted requests to every allowlisted route | Other users' data, more than their quota, to break shared service |
| Prompt-injection author | Place text in a document a victim attaches, in a future knowledge-base source, or in content a victim pastes | Steer the model: mislead the victim, exfiltrate through links, offer harmful commands or downloads |
| Malicious document or upload | Arbitrary bytes into a parser (PDF, Office, images) or into Pillow (avatars) | Code execution, memory exhaustion |
| Compromised or careless provider | See what is sent to it; return anything; echo input in errors | Secrets in prompts, prompt retention, hostile output |
| Local user or workload on the host | Read other processes' arguments, reach loopback ports; with root, everything | Database passwords, the cookie secret, PadhAI's data |
| Compromised dependency or download origin | Code in a package, a lifecycle script, Bun's install script, a container image | Root code execution at deploy time, or code in the web or API process |
| Sibling host on `trycloudflare.com`, or a network attacker before HSTS | Set cookies for the parent domain | Login CSRF (plant a state cookie) |
| Someone with commit access to the release checkout | Create local commits | Run unscanned code as root on the host |

## Security invariants

Each invariant names where it is enforced and what holds it. "Test" means a regression test fails if it breaks;
"review" means only code review holds it today. Run the tests as described in
[security-regression-tests.md](security-regression-tests.md#how-to-run).

| # | Invariant | Enforced in | Held by | Known gaps |
|---|---|---|---|---|
| I1 | **The identity in the session decides ownership.** No request names its own user; the API derives the user from the session token's hash and requires a verified address | [`dependencies.py:59-78`](../../backend/app/auth/dependencies.py); `extra="forbid"` in [`schemas.py:11-13`](../../backend/app/schemas.py); the BFF verifies the cookie's HMAC first ([`cookies.ts:55-86`](../../frontend/apps/app/src/lib/server/cookies.ts)) | **Test:** `test_authorization.py::test_client_supplied_identity_fields_are_rejected`, `::test_every_data_endpoint_requires_auth`; `test_route_auth.py::test_every_non_public_route_rejects_a_request_without_a_session`; `test_auth.py::test_sessions_are_found_by_token_hash_old_and_new`, `::test_expired_and_unverified_sessions_are_refused`; `test_sessions_me.py::test_missing_malformed_and_unknown_tokens_are_401`; `cookies.test.ts`; `proxy-core.test.ts` ("a token without Better Auth's signature is not a session") | — |
| I2 | **No cross-user leakage** of conversations, messages, files, memory, profile, notifications or retrieval. Another user's resource is a 404 | `user_id` predicates in every repository; retrieval scope in SQL ([`store.py:83-95, 212-231`](../../backend/app/rag/store.py)); memory recall ([`repository.py:256-277`](../../backend/app/memory/repository.py)); composite foreign keys on memory proposals and jobs | **Test:** `test_authorization.py` (all nine); `test_rag_retrieval.py::test_private_sources_never_leak_across_users`; `test_files.py::test_attached_file_search_never_crosses_students`; `test_memory.py::test_authorization_source_ownership_and_client_fields`, `::test_recalled_memory_is_used_across_conversations_and_is_user_isolated`, `::test_proposals_are_separate_authenticated_and_reviewed`; `test_chat.py::test_private_knowledge_is_scoped_to_its_owner_in_chat`, `::test_naming_touches_only_the_owners_conversation` | The only cross-user test for `/v1/notifications/read` and `/clear` is in `test_learning_streak.py` (A-5) |
| I3 | **An unapproved memory proposal never reaches the model.** Only active, currently valid memories the user created or approved are recalled | Recall reads `memory_items` only ([`repository.py:256-277`](../../backend/app/memory/repository.py)); proposals are a separate table; approval re-checks deletion markers under the per-user lock ([`memory/service.py:312-330`](../../backend/app/memory/service.py)); recall is off by default | **Partly test:** `test_memory.py::test_proposals_are_separate_authenticated_and_reviewed` (a pending proposal is not a memory), `test_memory_jobs.py::test_worker_rechecks_consent_before_provider_and_before_insert`. **Review:** no test asserts that a pending proposal's text is absent from the prompt | — |
| I4 | **Untrusted content never overrides policy.** Retrieved passages, attached files, memory and the user's own settings text are data, placed after the frozen system prompt and fenced | [`context.py:38-57, 61-71, 136-186`](../../backend/app/rag/context.py); quoted settings in `app/learning/tutor.py` | **Test** (the fencing): `test_files.py::test_the_tutor_reads_attached_files_in_follow_ups_and_regenerations` (a file cannot close its block); `test_rag_context.py::test_personal_memory_is_a_separate_bounded_untrusted_data_block`. **Measured, not guaranteed:** the model's obedience (eval case `rag_prompt_injection`, [`eval_engineering.py`](../../backend/scripts/eval_engineering.py)) | Prompt injection cannot be fully prevented (LLM01); the model has no tools, which bounds the impact |
| I5 | **Deleted or superseded memory cannot be recalled or resurrected** | Physical delete; `status == "active"` and validity windows on every read ([`repository.py:29-35`](../../backend/app/memory/repository.py)); deletion markers checked by commands and approval | **Test:** `test_memory.py::test_crud_export_pagination_and_physical_delete`, `::test_validity_windows_exclude_inactive_memory_from_list_export_and_recall`, `::test_correction_chain_stale_version_and_concurrency`, `::test_clear_removes_applied_proposal_without_constraint_failure`; `test_memory_commands.py::test_forget_command_exact_match_and_no_resurrection`; `test_account_deletion.py::test_deleting_an_account_leaves_no_row_that_belongs_to_it` | Not every writer takes the per-user lock, so a clear-all racing a create can leave the new item (A-14). Older backups still hold deleted data until they rotate out |
| I6 | **A provider failure never bypasses authorisation** and never widens what is read. Checks that guard a provider call fail closed | Ownership and quotas are checked in the request transaction before generation starts; retrieval failures skip context with a reason code; HIBP, Turnstile and Google key failures refuse | **Test:** `test_memory.py::test_memory_recall_failure_does_not_fail_chat`; `test_rag_retrieval.py::test_database_unavailable_is_a_typed_error`; `test_rag_graph.py::test_retrieval_timeout_is_bounded`; `protection.test.ts` ("an unavailable service throws", "an unreachable Cloudflare throws"); `config.test.ts` ("Google's keys unavailable: no identity (fails closed)"). **Review:** the ordering of checks before generation | — |
| I7 | **Secrets and private content never reach logs** | JSON logs with ids, sizes and codes only; a failed generation is logged with its error code and class, never the provider's text ([`chat/service.py:962`](../../backend/app/chat/service.py), A-13) **(released 0d868b7)**; key-like strings redacted in provider errors ([`openai_compatible.py:30-41`](../../backend/app/llm/openai_compatible.py)); Semgrep rule `netsec-log-sensitive-value` in the gate | **Test:** `test_chat.py::test_logs_never_contain_the_question_or_document_text`, `::test_slow_generations_are_logged_without_content`; `test_intents.py::test_two_short_requests_fail_fast_and_the_key_never_reaches_a_log`; `test_email.py::test_refused_or_failed_sends_raise_without_secrets`; `test_voice.py::test_the_key_and_the_words_never_reach_a_log`; `test_llm.py::test_provider_error_details_are_redacted`; `test_redact.py::test_the_model_reads_masked_messages_and_the_reply_comes_back_unchanged` (masking logs counts only) | No test asserts that a failed generation's log carries no provider text (A-13 is fixed in code, untested) |
| I8 | **New endpoints and jobs enforce the same boundaries** | Routers depend on `get_principal`; the public list is explicit; the BFF forwards only allowlisted routes; jobs read per user | **Test:** `test_route_auth.py::test_no_route_is_hidden_from_the_api_description`, `::test_the_public_list_names_only_routes_that_exist`, `::test_every_non_public_route_rejects_a_request_without_a_session`; `test_architecture_guards.py::test_dependency_direction`; `lib/api/routes.test.ts` (every proxied route exists in the contract); `lib/auth/routes.test.ts`; `test_memory_jobs.py::test_one_jobs_unexpected_error_never_stops_the_batch`. **Review:** per-user scoping inside a new job or query | A new route's ownership checks need their own cross-user case in `test_authorization.py` ([secure-development.md](secure-development.md)) |
| I9 | **Secrets are masked before the LLM and embeddings providers see them** | `RedactingProvider` and `RedactingEmbeddings` around everything `build_llm` and `build_embeddings` return ([`factory.py:24-27`](../../backend/app/llm/factory.py), [`embeddings.py:155-159`](../../backend/app/rag/embeddings.py), [`redact.py:365-460`](../../backend/app/llm/redact.py)); memory refuses secrets and config dumps ([`policy.py:13-79`](../../backend/app/memory/policy.py)) **(released 0d868b7)** | **Test:** `test_redact.py` (masking, precision, idempotence, a 2 MB config, the wrappers); `test_redact_wiring.py` (the built objects mask); `test_memory_unit.py::test_engineering_facts_are_allowed_and_network_secrets_and_config_dumps_are_not` | Patterns catch obvious secrets only. Ollalink requests (classifiers, voice) are not masked. Messages and file text are stored unmasked |
| I10 | **A production change needs authorisation and has a recovery path** | `deploy-netsec/deploy.sh`: clean tree, exactly GitHub `main`, a PASS receipt for the commit, a logged override; backup before migrations ([`update.sh:196-209`](../../deploy-netsec/server/update.sh)); `rollback:netsec`; NetSec's hard-coded namespace (`namespace.test.mjs`); PadhAI's `deploy/` refuses this repository | **Test:** `deploy-netsec/tests/security-gate.test.mjs`, `namespace.test.mjs`, `legacy-guard.test.mjs`. **Review and process:** the owner's approval for production changes (CLAUDE.md) | The receipt is written by the same person who deploys: it is a self-check, not a separation of duties. The deploy tooling is archived from local `HEAD` (A-25, rejected as a finding). A rollback to any ancestor of `main` skips the gate unlogged; overrides are logged locally only ([known-limitations.md](../known-limitations.md#netsec-ai-2026-10-29)) |
| I11 | **Secrets never travel in process arguments** | [`lib.sh:177-188`](../../deploy-netsec/server/lib.sh) (F-DEPLOY-1), [`database-grants.sh:29-36`](../../deploy-netsec/database-grants.sh) (A-22) **(released 0d868b7)** | **Test:** `deploy-netsec/tests/files-sandbox.test.mjs` ("secrets never travel in process arguments") | Static test only; checks these two scripts, not every future script |
| I12 | **Untrusted file bytes are parsed only in the sandbox, which sees no secrets, no network and no other deployment** | [`processing.py:85-120`](../../backend/app/files/processing.py); production refuses any other runner ([`settings.py:334-341`](../../backend/app/settings.py)) | **Test:** `test_files.py::test_the_sandbox_hides_this_deployment_and_its_secrets`; `files-sandbox.test.mjs`. **Host check:** [`sandbox_probe.py`](../../backend/scripts/sandbox_probe.py) | Avatars are decoded in the API process, not the sandbox (A-32) |

## Attack paths and abuse cases

| # | Path | Mitigations | Residual |
|---|---|---|---|
| P1 | Anonymous → sign-in → password guessing or code guessing | Per-account limits in `auth_attempts` under an advisory lock; Better Auth per-IP limits; HIBP refusal; argon2id; two-step sign-in | Anyone who knows an address can spend its hard limits and block that user's sign-in and reset (A-1). Turnstile may be unset |
| P2 | Anonymous → phishing link on the real domain → open redirect after sign-in | `safeRedirect` re-checks the parsed path **(released 0d868b7, A-17)** | — |
| P3 | Sibling host on `trycloudflare.com` → plants a Better Auth state cookie → Google login CSRF | Every Better Auth cookie is `__Host-` **(released 0d868b7, A-0)** | The Quick Tunnel's shared parent domain remains; a named tunnel on an owned domain removes it |
| P4 | Anonymous → spoofed `X-Forwarded-Host` → wrong public origin in redirects or the CSRF check | The header counts only when `Host` is loopback; browsers cannot set it cross-origin without a preflight (F-BFF-1, fixed after `0d868b7`) | The tunnel passes a client's own header through (seen live 2026-10-29) |
| P5 | Signed-in user → another user's id in a URL (BOLA) | `user_id` predicates; 404 | — (I2) |
| P6 | Signed-in user → extra fields (`userId`, memory consent keys) in a body | `extra="forbid"`; `memory*` keys refused in `/v1/me/settings` **(released 0d868b7, F-MEM-1)** | — |
| P7 | Signed-in user → TOTP code replay in parallel | The code is claimed atomically before it is checked **(released 0d868b7, F-AUTH-1)** | — |
| P8 | Signed-in user → decompression-bomb avatar → API out of memory | 40 MP header limit, 16 MP decoded budget **(released 0d868b7, A-30)**, 2 decodes at once | Every Pillow plugin probes the bytes in the root API process (A-32) |
| P9 | Signed-in user → malicious PDF or Office file → parser bug | Parsing only in the sandbox (I12) | A parser exploit lands in a throwaway, network-less user; host kernel bugs are out of scope |
| P10 | Signed-in user → resource exhaustion | Quotas, BFF in-flight caps, 64 KB JSON bodies, upload size caps, per-user file quota; the middleware no longer buffers API bodies **(released 0d868b7, A-6)**; a body-read deadline of 30 s, 300 s for uploads (408) **(released 0d868b7, A-6 follow-up)** | Download slots are freed before delivery (A-7); one account can hold both upload slots for up to 300 s each (no per-user upload cap in the BFF); the chat quota can be overshot by parallel requests (A-10) |
| P11 | Prompt-injection author → attached file or knowledge-base passage → model follows it | Fenced, labelled blocks; no tools; output sanitised; links limited to `http`, `https`, `mailto`; images never loaded | The model can still be misled into wrong or harmful advice; users must review commands before running them |
| P12 | Injected reply → offers a script download or a dangerous command | Downloads are text Blobs the user must save and run; the prompt asks for impact and backout before disruptive steps | Any extension is accepted (A-20, hardening note) |
| P13 | User pastes a config with secrets → provider sees and keeps it | Masking before every LLM and embeddings provider **(released 0d868b7)**; memory refuses secrets | Non-obvious secrets pass; Ollalink unmasked; the stored message and backups keep the original |
| P14 | Compromised provider → hostile output, retained prompts, input echoed in errors | Sanitised rendering; masking; error text redacted for key-like strings and no longer logged on a failed generation **(released 0d868b7, A-13)** | Retention at the provider; A-13 has no regression test |
| P15 | Local user on the host → reads secrets from `ps` during a deploy | Secrets moved to environments **(released 0d868b7, F-DEPLOY-1, A-22)** | Services run as root, so a foothold in any of them is root (A-21) |
| P16 | LAN neighbour → `127.0.0.1:55432` through Docker's DNAT on Docker Engine below 28 | Role passwords; loopback publish | Unverified host fact (A-23) |
| P17 | Compromised dependency or download origin → root code at deploy | Hashed Python lock; `bun.lock`; Node checked against SHASUMS; pip-audit and `bun audit` in the gate; the PostgreSQL image pinned by digest in the repository **(released 0d868b7, A-28)** | Bun via `curl \| bash` (A-24, A-27); production keeps its current image until the pinned compose file is copied onto the host on purpose (A-28); unhashed workstation tools (A-29) |
| P18 | Commit access to the release checkout → unscanned code as root | Gate receipt for `HEAD`; clean tree; GitHub `main` only | Tooling comes from local `HEAD` (A-25, rejected: the same principal already has that power) |
| P19 | NetSec release → breaks or reads PadhAI | Separate, hard-coded namespace (`namespace.test.mjs`, database identity checks); extractor hides PadhAI's paths **(released 0d868b7)**; PadhAI's deploy refuses this repository **(released 0d868b7)** | Shared `/root/.bun` (A-26); shared root; `safety.sh` checks no real target, and no test covers PadhAI's Docker names |
| P20 | Stolen session → persists after the victim reacts | Sign out other devices; reset revokes all sessions | Turning on two-step sign-in keeps other sessions (A-2); the new-device alert can be silenced with a common User-Agent (A-31) |

## Residual risks

Ranked roughly by impact. Each has an owner and a status in [security-audit.md](security-audit.md#findings) and
the [production checklist](production-security-checklist.md#open-follow-ups).

1. **One root foothold is the whole host**, PadhAI included: every NetSec service runs as root (A-21), and
   `/root/.bun` is shared with PadhAI (A-26).
2. **Prompt injection and misinformation** are inherent (LLM01, LLM09). The engineering eval's baseline failed its
   gate (37/57 at `e439b96`).
3. **Secret masking is pattern-based** and skips Ollalink; chats and files are stored unmasked; backups are
   unencrypted on the same host.
4. **Sign-in availability**: per-address hard limits can be spent by others (A-1); no Turnstile unless configured.
5. **Session hygiene**: A-2, A-31.
6. **Shared-process availability**: A-7, A-10, one account holding both upload slots (up to 300 s each), avatar
   decoding in the API (A-32).
7. **Supply chain at deploy**: A-24, A-27, A-29, and A-28 until the pinned compose file is applied on the host;
   GitHub Actions do not run, so the local gate is the only automated check.
8. **Memory consistency**: A-14, A-16. Same-user only. (The kill switch, A-4/A-11/A-15, is fixed in the working
   tree.)
9. **Unverified deployment facts**: A-7, A-23; the Quick Tunnel's address drift.
10. **Everything marked (released 0d868b7)** reached production on 2026-10-29; production ran without it before.

## Mapping to OWASP and ASVS

Only where relevant. This maps where controls live; it is not a verification of every requirement.

### OWASP Top 10 for LLM Applications 2025

| Risk | Here |
|---|---|
| LLM01 Prompt Injection | I4, P11. Fenced blocks, no tools, sanitised output. Residual: inherent |
| LLM02 Sensitive Information Disclosure | I2, I7, I9, P13. Masking, per-user scoping, log rules, memory policy. Residual: Ollalink unmasked; A-13 fixed (released 0d868b7) without a test |
| LLM03 Supply Chain | P17. Locks, audits in the gate, the PostgreSQL image pinned in the repository. Residual: A-24, A-27, A-29; A-28 until applied on the host |
| LLM04 Data and Model Poisoning | The knowledge base is written only by `ingest_documents.py` (`--global` or `--owner-email`); per-user sources reach only their owner; memories are explicit or approved. NetSec's own knowledge base does not exist yet: its sources need review when it does |
| LLM05 Improper Output Handling | Sanitise first, raw HTML as text, safe link schemes, no images, KaTeX without trust, mermaid strict (`features/markdown/message-content.test.tsx`); SSE data is JSON-encoded and the parser splits only on line ends, so a reply cannot forge an event (audit note; `lib/api/sse.test.ts` covers the framing) |
| LLM06 Excessive Agency | The model has no tools; tests come from the question bank, not the model |
| LLM07 System Prompt Leakage | The prompt holds no secrets and no authorisation logic ([`assistant.py`](../../backend/app/chat/assistant.py)); leaking it reveals nothing that protects anything |
| LLM08 Vector and Embedding Weaknesses | `AccessScope`, attached-file scoping, embedding model match (`test_rag_retrieval.py::test_other_embedding_model_is_invisible`) |
| LLM09 Misinformation | Prompt rules against invented output and "verified" claims; `eval_engineering.py`. Residual: the baseline fails its gate |
| LLM10 Unbounded Consumption | Chat quotas, input and context budgets, BFF caps, file quotas. Residual: A-7, A-10 |

### OWASP API Security Top 10 2023

| Risk | Here |
|---|---|
| API1 Broken Object Level Authorization | I2, P5 |
| API2 Broken Authentication | I1, P1, P7, P20 |
| API3 Broken Object Property Level Authorization | `extra="forbid"`; reserved `memory*` settings keys (F-MEM-1); error envelopes keep codes only |
| API4 Unrestricted Resource Consumption | P8, P10 |
| API5 Broken Function Level Authorization | I8; `/internal/auth/*` needs `AUTH_INTERNAL_TOKEN` and is not on the BFF allowlist |
| API6 Unrestricted Access to Sensitive Business Flows | Code sends and sign-ups are limited per address and per network; A-1 |
| API7 Server Side Request Forgery | No endpoint fetches a URL a user supplies; outbound calls go only to configured provider URLs (review only) |
| API8 Security Misconfiguration | `validate_for_env` refuses unsafe production values ([`settings.py:384-435`](../../backend/app/settings.py)); docs off in production; security headers |
| API9 Improper Inventory Management | `test_route_auth.py` (no hidden route); the OpenAPI contract check and `routes.test.ts` |
| API10 Unsafe Consumption of APIs | Typed provider errors, logged without their text (A-13, released 0d868b7); Google ID tokens verified; HIBP and Turnstile fail closed |

### OWASP ASVS 5.0 (chapters)

| Chapter | Where |
|---|---|
| V1 Encoding and Sanitization | Markdown sanitising; bound SQL parameters (Semgrep `netsec-sql-text-built-string`) |
| V2 Validation and Business Logic | Pydantic request models; quotas and limits |
| V3 Web Frontend Security | CSP (`'unsafe-inline'` remains), HSTS, `__Host-` cookies, `safeRedirect`, framing denied |
| V4 API and Web Service | BFF allowlist, `Origin` checks, JSON content type, body limits |
| V5 File Handling | Type by content, size and count limits, the sandbox, avatar pixel budgets |
| V6 Authentication | argon2id, HIBP, single-use TOTP, per-account limits |
| V7 Session Management | Token hash lookup, HMAC-signed cookie, revocation; A-2 |
| V8 Authorization | Per-user scoping; 404 for others' resources |
| V10 OAuth and OIDC | Google ID token verification, PKCE, server-side state, no implicit linking |
| V11 Cryptography | argon2id, HMAC-SHA256, constant-time comparison |
| V12 Secure Communication | HTTPS at the edge; loopback-only origin services; HTTPS required for Ollalink in production |
| V13 Configuration | Secrets in mode-600 env files, never in arguments; production validation |
| V14 Data Protection | Masking before providers; logs without content; backups without tokens (unencrypted) |
| V15 Secure Coding and Architecture | Dependency-direction guards; pinned dependencies; the security gate |
| V16 Security Logging and Error Handling | JSON logs without content; one error envelope; failed sign-ins logged with hashed accounts |
