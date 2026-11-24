# Security regression tests

> Written 2026-10-29 against the code on top of `e439b96`, released in `0d868b7` the same day. Test names were checked to exist. "Failed
> first" means the evidence ledger of the 2026-10-29 work records the test failing on the old code before the fix
> ([security-audit.md](security-audit.md)). Counts are the ledger's, from that day.

Each test here protects a security property. If one fails, treat it as a security regression: fix the code, never
the assertion, unless the property itself changed on purpose (and then update [THREAT_MODEL.md](THREAT_MODEL.md)).

## Contents

- [How to run](#how-to-run)
- [Identity, sessions and accounts](#identity-sessions-and-accounts)
- [Authorisation and isolation](#authorisation-and-isolation)
- [Retrieval, context and prompt injection](#retrieval-context-and-prompt-injection)
- [Files, the sandbox and avatars](#files-the-sandbox-and-avatars)
- [Persistent memory](#persistent-memory)
- [Secret masking and log hygiene](#secret-masking-and-log-hygiene)
- [The assistant's behaviour](#the-assistants-behaviour)
- [The BFF and the browser](#the-bff-and-the-browser)
- [NHprep video courses](#nhprep-video-courses)
- [NH Friend Zone](#nh-friend-zone)
- [Deploy scripts and tooling](#deploy-scripts-and-tooling)
- [Known gaps](#known-gaps)

## How to run

| What | Where | Command |
|---|---|---|
| Backend (pytest) | WSL Ubuntu 24.04 with PostgreSQL 16 + pgvector (no Docker on the owner's PC) | Below |
| Frontend unit tests | `frontend/` | `bun run test` (or one file: `cd frontend/apps/app && bun test src/lib/safe-redirect.test.ts`) |
| Frontend checks | `frontend/` | `bun run typecheck`, `bun run lint`, `bun run build` |
| Deploy script tests | repository root | `bun test deploy-netsec/tests` |
| Graphify boundary | repository root | `bun run test:graph` |
| The security gate | repository root | `bun run security` (about 3 minutes; [security-tools.md](security-tools.md)) |
| Everything above except the gate | repository root | `bun run test` runs backend pytest, the frontend typecheck and tests, and `deploy-netsec/tests` ([`scripts/run.mjs`](../../scripts/run.mjs)); its backend step needs a reachable `*_test` database |
| End to end (`e2e/security.e2e.ts`) | Needs the API, PostgreSQL and Mailpit | `bun run test:e2e`; does not run on the owner's PC ([testing.md](../testing.md)) |

**Backend tests in WSL.** The test harness drops, recreates and migrates its database, and refuses any database
whose name does not end in `_test` ([`conftest.py:73`](../../backend/tests/conftest.py)). Copy the code into WSL
(never a second git tree, never an env file), then point `TEST_DATABASE_URL` at your own `_test` database. Give
each parallel run its own directory and its own database name.

```sh
# In WSL. PostgreSQL 16 with pgvector runs locally; <...> are yours to fill in.
SRC="/mnt/c/Users/test/Desktop/AI- Tutor NH"
DEST=~/netsec-test
mkdir -p "$DEST/backend" "$DEST/docs"
(cd "$SRC/backend" && tar --exclude=.venv --exclude=__pycache__ --exclude='.env*' -cf - .) | tar -xf - -C "$DEST/backend"
cp "$SRC/docs/openapi.json" "$DEST/docs/"          # the contract tests read it
cd "$DEST/backend"
python3.12 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/pip install 'pytest>=8.3,<9.0' 'pytest-asyncio>=0.24,<2.0'   # requirements-dev.txt (not hash-pinned)
export TEST_DATABASE_URL='postgresql+asyncpg://<user>:<password>@127.0.0.1:<port>/<name>_test'
.venv/bin/python -m pytest -o addopts="" -ra tests/test_authorization.py tests/test_route_auth.py
```

This is the method in [testing.md, "Backend tests in WSL"](../testing.md#backend-tests-in-wsl), and how the
2026-10-29 evidence was produced. Never point `TEST_DATABASE_URL` at a production database: the harness would drop
it if its name ended in `_test`.

## Identity, sessions and accounts

| Test | What it proves | Evidence |
|---|---|---|
| `backend/tests/test_auth.py::test_sessions_are_found_by_token_hash_old_and_new` | The API looks a session up by the token's SHA-256 | — |
| `test_auth.py::test_expired_and_unverified_sessions_are_refused` | An expired session or an unverified address is a 401 | — |
| `test_auth.py::test_parallel_wrong_passwords_never_pass_the_limit` | The per-account limit holds under parallel attempts | — |
| `test_auth.py::test_internal_routes_need_the_token` | `/internal/auth/*` needs `AUTH_INTERNAL_TOKEN` | — |
| `test_auth.py::test_sign_in_alert_only_for_a_new_device_and_only_for_real_sessions` | Sign-in alerts fire for new devices only (keyed on the UA label: A-31) | — |
| `test_auth.py::test_two_step_accounts_confirm_with_a_recent_full_sign_in_and_are_told_of_changes` | Re-authentication on two-step accounts | — |
| `test_sessions_me.py::test_missing_malformed_and_unknown_tokens_are_401`, `::test_expired_session_is_401`, `::test_revoke_other_sessions` | Session checks and "sign out other devices" | — |
| `test_identity.py::test_devices_are_listed_with_masked_details_only`, `::test_one_other_device_can_be_signed_out`, `::test_an_email_change_needs_the_password`, `::test_a_change_code_allows_a_few_attempts_then_dies` | Devices show masked details; email change needs re-authentication and limits code attempts | — |
| `test_account_deletion.py::test_deleting_an_account_leaves_no_row_that_belongs_to_it`, `::test_a_code_of_a_deleted_account_dies_with_it` | Deletion removes every row of the user, memory included | — |
| `frontend/apps/app/src/lib/auth/config.test.ts` "an authenticator code works once" (4 tests) | A TOTP code is claimed before it is checked; parallel uses pass once; other users and codes don't collide; a no-lock negative control shows the test can fail | F-AUTH-1. Frontend 586/586; on real PostgreSQL, 20 parallel claims gave exactly 1 pass |
| `config.test.ts` "cookies on a shared parent domain (*.trycloudflare.com)" (2 tests) | Every Better Auth cookie is `__Host-netsecai.*` on HTTPS; the session cookie keeps `__Host-padhai_session` | A-0. 11/11 config tests |
| `config.test.ts` "Google sign-in" and "accounts" | No bare ID-token sign-in; Google's identity only from a verified token; keys unavailable fails closed; only a proven address becomes an account | — |
| `src/lib/auth/protection.test.ts` | HIBP gets only five hex digits; HIBP or Turnstile unavailable fails closed | — |
| `src/lib/auth/routes.test.ts` | Exactly the listed Better Auth routes are served; no answer carries a session token | — |

## Authorisation and isolation

| Test | What it proves | Evidence |
|---|---|---|
| `backend/tests/test_authorization.py::test_other_users_resources_are_404` | Another user's conversations, messages and other resources answer 404 | — |
| `test_authorization.py::test_identity_data_is_per_user`, `::test_learning_data_is_per_user`, `::test_progress_is_per_user`, `::test_notification_preferences_and_unsubscribe_links_are_per_user`, `::test_files_are_per_user` | Per-user isolation of each domain | — |
| `test_authorization.py::test_the_engineering_profile_is_per_user` | The engineering profile and its preview use only the caller's row (in-progress profile work) | — |
| `test_authorization.py::test_client_supplied_identity_fields_are_rejected` | A body naming a user id is refused | — |
| `test_authorization.py::test_every_data_endpoint_requires_auth` | Data endpoints need a session | — |
| `test_route_auth.py::test_no_route_is_hidden_from_the_api_description`, `::test_the_public_list_names_only_routes_that_exist`, `::test_every_non_public_route_rejects_a_request_without_a_session` | Every route is in the API description, and every non-public route needs a session: a new route is covered automatically | — |
| `test_architecture_guards.py::test_dependency_direction` and the other guards | Routers never touch retrieval directly; the store knows no LangChain; domains are reached through their public modules | — |
| `test_chat.py::test_naming_touches_only_the_owners_conversation`, `::test_private_knowledge_is_scoped_to_its_owner_in_chat` | Chat side effects and private knowledge stay with the owner | — |
| `test_authorization.py::test_other_users_resources_are_404` (save and submit rows, 2026-11-18) | A mock exam's save and submit on another user's reply are 404, like answer and skip | — |
| `test_learning_engine.py::test_save_clear_and_submit_never_touch_a_bank_test_or_its_ledger` | save/clear/submit on a question-bank (v1) test are 409 `assessment_not_exam` and leave the practice ledger and the record unchanged (they used to pass as a skip) | Failed first (2026-11-18, lane B1): 422 on the old schema, not 409 |
| `test_assessment_api.py::test_save_clear_and_submit_are_refused_on_a_v1_test`, `test_assessment_modes.py::test_save_clear_and_submit_are_refused_on_a_v1_record_and_on_practice` | The v1 contract holds: answer and skip only | Failed first (2026-11-18) |
| `test_assessment_api.py::test_the_endpoint_is_rate_limited_per_user` | Practice-test actions are limited per user (429 `rate_limited`) before the reply's row lock | — |

## Retrieval, context and prompt injection

| Test | What it proves | Evidence |
|---|---|---|
| `test_rag_retrieval.py::test_private_sources_never_leak_across_users` | `AccessScope`: a user's private sources never reach another user | — |
| `test_rag_retrieval.py::test_source_and_metadata_filters_only_narrow`, `::test_invalid_metadata_filter_is_rejected` | Request options can only narrow the scope; bad filters are refused | — |
| `test_rag_retrieval.py::test_other_embedding_model_is_invisible` | Vectors from another embedding model are never mixed in | — |
| `test_rag_retrieval.py::test_search_query_can_use_the_hnsw_index` | The HNSW setting, now a bound parameter, still applies | 71/71 across `test_files`, `test_rag_retrieval`, `test_rag_graph`, `test_chat` after the `text()` change |
| `test_rag_retrieval.py::test_database_unavailable_is_a_typed_error`; `test_rag_graph.py::test_retrieval_timeout_is_bounded` | A retrieval failure is typed and bounded, never a wider read | — |
| `test_files.py::test_the_tutor_reads_attached_files_in_follow_ups_and_regenerations` | A file containing `</attached_files>` cannot close its block; a deleted file is never read again | — |
| `test_rag_context.py::test_personal_memory_is_a_separate_bounded_untrusted_data_block` | Memory goes in its own labelled, bounded block that its content cannot close | — |
| `test_rag_context.py::test_system_and_latest_message_always_kept_even_over_budget` | The system prompt is never dropped for content | — |

## Files, the sandbox and avatars

| Test | What it proves | Evidence |
|---|---|---|
| `test_files.py::test_the_sandbox_hides_this_deployment_and_its_secrets` | The extractor unit hides `/opt/netsecai`, `/etc/netsecai` and PadhAI's paths | F-FILES-1. Failed first (`/opt/aichat:ro`); 19/19 `test_files.py` after |
| `test_files.py::test_unsupported_mismatched_oversized_and_empty_files_are_refused` | Type by name and content, size limits | — |
| `test_files.py::test_storage_quota_is_enforced_and_deleting_frees_it`, `::test_concurrent_uploads_cannot_exceed_the_quota`, `::test_capacity_limits_refuse_uploads_and_fail_processing_clearly` | Quotas hold, under concurrency too | — |
| `test_files.py::test_only_ready_files_can_be_attached`, `::test_attached_file_search_never_crosses_students` | A turn reads only its owner's attached, ready files | — |
| `test_files.py::test_filenames_are_display_names_only` | A file name is never a path | — |
| `deploy-netsec/tests/files-sandbox.test.mjs` (6 tests) | The sandbox matches NetSec's deployment, pauses on `/run/netsecai-deploying`, uses the unit names `status.sh` counts; secrets stay out of arguments (below); a deploy that changes the frontend's dependencies installs with `--force` | F-FILES-2, F-DEPLOY-1, A-22. 4/4 at F-DEPLOY-1; `deploy-netsec/tests` 35/35 at A-22, before the sixth test ("frontend dependencies") was added: the four files now hold 36 |
| `test_identity.py::test_formats_decoded_at_full_size_have_a_smaller_pixel_budget` | A PNG, a WebP or an undraftable JPEG over 16 MP is refused before full decoding | A-30. Failed first (a 20 MP PNG was accepted); 28/28 `test_identity.py` after |
| `test_identity.py::test_a_decompression_bomb_is_refused_before_it_is_decoded`, `::test_unacceptable_photos_are_refused` | The 40 MP header limit; the declared type must match | — |
| `test_identity.py::test_a_photo_loses_its_metadata_and_is_turned_upright` | EXIF and GPS never survive | — |
| `test_identity.py::test_only_the_photo_route_accepts_a_large_body`, `::test_photo_uploads_are_rate_limited` | Body size and rate limits on avatars | — |

## Persistent memory

| Test | What it proves | Evidence |
|---|---|---|
| `test_sessions_me.py::test_settings_refuse_the_memory_consent_keys` | `PATCH /v1/me/settings` refuses `memory*` keys | F-MEM-1. Failed first |
| `test_memory_unit.py::test_stored_memory_preferences_parse_defensively` | Malformed stored consent reads as "no"; only a real `true` is consent | F-MEM-1. Failed first (`ValueError`) |
| `test_memory_jobs.py::test_malformed_stored_consent_never_breaks_chat_or_other_users_jobs`, `::test_one_jobs_unexpected_error_never_stops_the_batch` | One user's bad data or one job's error never stops others | F-MEM-1. 4 tests failed first in all; 31/31 after across the five memory and settings files |
| `test_memory_unit.py::test_engineering_facts_are_allowed_and_network_secrets_and_config_dumps_are_not` | Memory refuses network secrets and config dumps, and keeps engineering facts | — |
| `test_memory.py::test_authorization_source_ownership_and_client_fields` | Memory routes are per user; the source message must be the caller's | — |
| `test_memory.py::test_recalled_memory_is_used_across_conversations_and_is_user_isolated` | Recall reads only the caller's memories, in the untrusted block | — |
| `test_memory.py::test_proposals_are_separate_authenticated_and_reviewed` | A pending proposal is not a memory and is visible only to its owner; approval and rejection | — |
| `test_memory.py::test_crud_export_pagination_and_physical_delete`, `::test_validity_windows_exclude_inactive_memory_from_list_export_and_recall`, `::test_correction_chain_stale_version_and_concurrency`, `::test_clear_removes_applied_proposal_without_constraint_failure` | Deletion is physical; inactive, superseded or expired memories are never listed, exported or recalled | — |
| `test_memory.py::test_memory_recall_failure_does_not_fail_chat` | A recall failure never fails the turn | — |
| `test_memory_commands.py::test_forget_command_exact_match_and_no_resurrection` | A forgotten memory cannot come back through "remember" | — |
| `test_memory_commands.py::test_with_memory_off_a_remember_command_is_an_ordinary_message` | With `MEMORY_ENABLED` off, "remember …" writes no memory and no command receipt, and the model answers it like any other message | A-4, A-11, A-15. Failed before the fix |
| `test_memory_jobs.py::test_malformed_stored_consent_never_breaks_chat_or_other_users_jobs` (its approval step) | Approving a proposal over malformed stored consent reads it like the worker (`MemoryPreferences.from_settings`): 200, never a 500 | The approval hardening in `proposals.py` |
| `test_memory_jobs.py::test_chat_enqueues_in_completion_transaction_only_when_on_and_consented`, `::test_worker_rechecks_consent_before_provider_and_before_insert`, `::test_worker_uses_only_bounded_user_text_and_inserts_idempotently` | The worker runs only with consent, re-checks it, and reads only bounded user text | — |
| `test_memory_jobs.py::test_the_memory_policy_refuses_what_provider_masking_would_mask` | Whatever the provider masking (`app/llm/redact.py`) would mask, such as `snmp-server community` and `crypto isakmp key`, is never stored as a memory (K-06h) | Failed first on `8bfe95b`, 2026-11-04 |
| `test_memory_jobs.py::test_the_worker_never_proposes_text_the_user_already_rejected_or_saved`, `::test_withdrawing_consent_closes_pending_suggestions` | Rejected, deleted or saved text is never suggested again; withdrawing consent closes the suggestions made under it (K-25c, K-25d) | Failed first on `8bfe95b` |
| `test_memory_jobs.py::test_one_refused_proposal_is_dropped_and_the_others_are_kept`, `::test_an_account_keeps_a_bounded_number_of_memories`, `::test_recall_sends_nothing_to_the_provider_for_a_user_with_no_memories` | One refused suggestion no longer kills the job; an account keeps at most 200 memories (409 `memory_limit_reached`); recall sends no text to the embeddings provider when there is nothing to recall (K-25b, K-48b, K-05c) | Failed first on `8bfe95b` |

## Secret masking and log hygiene

| Test | What it proves | Evidence |
|---|---|---|
| `test_redact.py::test_each_secret_value_is_masked_and_its_keyword_kept` | Each secret syntax is masked, keeping the keyword and type | 222 cases in `test_redact.py` |
| `test_redact.py::test_what_is_not_a_secret_stays_byte_identical`, `::test_masking_twice_changes_nothing_more`, `::test_a_two_megabyte_config_is_masked_quickly`, `::test_empty_text` | Precision, idempotence, no catastrophic backtracking | — |
| `test_redact.py::test_the_model_reads_masked_messages_and_the_reply_comes_back_unchanged`, `::test_the_inner_stream_and_errors_pass_through_as_they_are`, `::test_the_embeddings_provider_reads_masked_text` | The wrappers mask what the provider reads and nothing else | — |
| `test_redact_wiring.py::test_the_built_model_masks_secrets_before_the_provider`, `::test_the_built_embeddings_mask_secrets_before_the_provider` | `build_llm` and `build_embeddings` return masking wrappers, so every caller gets them | — |
| `test_chat.py::test_logs_never_contain_the_question_or_document_text`, `::test_slow_generations_are_logged_without_content` | Chat logs carry no message or document text | — |
| `test_intents.py::test_two_short_requests_fail_fast_and_the_key_never_reaches_a_log`; `test_email.py::test_refused_or_failed_sends_raise_without_secrets`; `test_voice.py::test_the_key_and_the_words_never_reach_a_log`; `test_llm.py::test_provider_error_details_are_redacted` | Keys and words never reach logs on the other provider paths | — |
| `test_assessment_api.py::test_no_log_line_holds_an_answer` | Every question kind through answer, save and submit, and the 422 and 409 paths, logs ids and reason codes only, never a value | — |

## The assistant's behaviour

| Test | What it proves | Evidence |
|---|---|---|
| `test_netsec_prompt.py` (3 tests) | The frozen prompt is an engineering assistant's (including the secrets and authorised-testing rules) and no model-facing text speaks of school | — |
| `test_eval_engineering.py` | The engineering eval's scoring logic: refusals, authorised pentest answers, no over-claiming | — |
| `test_assessment_kinds.py::test_each_kind_is_graded_and_its_key_stays_hidden_until_final`, `test_assessment_modes.py::test_a_mock_exam_saves_in_any_order_and_grades_only_on_submit` | A question's key, explanation and output reach a client only once it is final (an exam: once submitted); the assistant's history shows a v2 quiz without them | — |
| `test_assessment_modes.py::test_an_exam_in_progress_puts_the_guard_first_and_tells_no_answer`, `::test_the_summary_stays_within_its_budget_and_keeps_the_frame_and_the_guard` | While a mock exam is open the assistant is told never to reveal or hint at answers, is told no saved answer, and the guard is never cut | — |
| `test_assessment_kinds.py::test_a_malformed_question_refuses_the_whole_test`, `::test_numbers_are_fixed_point_within_the_user_grammar_and_never_raise` | The new parsers refuse malformed or oversized blocks whole; a number like `1e999999999` is refused without being expanded, and a decimal error grades wrong instead of a 500 | — |
| `test_assessment_api.py::test_an_unreadable_record_never_breaks_the_reply_or_the_next_turn`, `test_assessment_modes.py::test_an_unreadable_record_is_left_out_of_the_summary` | A stored record the code can't read is skipped (logged): the reply still loads and the next turn still streams | — |
| `test_assessment_fences.py`, `test_chat_tests_card_open.py`, `test_reply_repair_linear.py` | A reply can't stall the API: the fence scans, the card and directive openers, unclosed card lines and the key lines of a non-JSON exercise run in linear time (each old pattern was quadratic, up to 28 s on a 40,000-character reply), with the same output as before on generated text; JSON nested deeper than Python recurses is `invalid_json` or left as it is, never an error | Failed first (2026-11-18/19): the timing and nesting cases on the old code (`test_reply_repair_linear.py`: 17 of 22) |
| `test_qbank_certification.py`, `test_migrations.py` (0026) | The certification bank: NHprep-derived questions reach only verified NetworkersHome students (ASCII domain, exact; a KELVIN SIGN lookalike fails), every pick checks it, and a reader who stops being one sees an NHprep reply as one neutral line; the model can't open the netsec bank; the bank turn makes no model, recall or classifier call; keys are absent from the reply, its public metadata and history until a question is final; the database keeps one of two question shapes and grades sets of letters, final once given; ordinary chat isn't read as a quiz request; 0026's downgrade refuses while netsec content exists | Failed first: ImportError on the old code (no `app.access`) |
| [`backend/scripts/eval_engineering.py`](../../backend/scripts/eval_engineering.py) (on the host, real model) | Behaviour on 19 scenarios, including `rag_prompt_injection`, `pentest_authorized`, `wifi_unauthorized` and `verify_claim` | Baseline at `e439b96`: 37/57, FAIL (`verify_claim` 0/3, `rag_prompt_injection` 3/3). Not a regression test: a measurement |

## The BFF and the browser

| Test | What it proves | Evidence |
|---|---|---|
| `frontend/apps/app/src/lib/server/proxy-core.test.ts` "allowlist", "CSRF", "size limit" | Unknown routes and non-UUID ids are 404 without calling the API; only listed query params pass; cross-origin mutations are 403; bodies over 64 KB are refused | — |
| `proxy-core.test.ts` "session and identity", "session cookie handling" | No cookie, a planted plain cookie on HTTPS, or an unsigned token is not a session; a client's `X-Forwarded-For` is never passed on | — |
| `proxy-core.test.ts` "files", "voice" | Upload type, size and slot limits; private cache headers only; voice has its own bounded class | — |
| `proxy-core.test.ts` "body deadline" (3 tests) | `readBody` refuses a body that stops arriving at the deadline (`too_slow`, which the proxy answers with 408 `request_timeout`); a complete body within the deadline is returned whole; the size limit still applies first | A-6 follow-up |
| `proxy-core.test.ts` "a client's own X-Forwarded-Host cannot make another origin look like ours", "behind a local proxy…" | A spoofed `X-Forwarded-Host` cannot pass the CSRF check (the tunnel keeps the public `Host`); a loopback `Host` still takes the forwarded one | F-BFF-1. Failed first (200, not 403) |
| `src/lib/server/cookies.test.ts` | Only a cookie Better Auth signed gives a token; on HTTPS only the `__Host-` cookie counts | — |
| `src/lib/server/expired.test.ts` | A hostile link cannot sign a valid session out; an unsafe redirect is dropped | — |
| `src/lib/safe-redirect.test.ts` | No redirect to another origin, including dot segments (`/.//evil.com`, `/a/..//evil.com`, `/%2E//evil.com`) | A-17. Failed first (`/.//evil.com` became `//evil.com`); 5/5 after |
| `src/middleware.test.ts` | API routes never pass through the middleware (no body buffering, no 10 MB cut) | A-6. Failed first; 56/56 middleware and server tests |
| `src/lib/api/routes.test.ts` | Every proxied route and request field exists in `docs/openapi.json` | — |
| `src/features/markdown/message-content.test.tsx` | Raw HTML is text; `javascript:`, `data:` and relative links lose their href; images are never loaded; KaTeX cannot run trusted macros | — |
| `src/lib/api/sse.test.ts` | The SSE parser's framing (a reply cannot forge an event) | — |
| `frontend/apps/app/e2e/security.e2e.ts` | In a browser: security headers, no redirect to another site after sign-in, the proxy refusing other origins, unknown routes and oversized bodies | Needs the e2e stack |

## NHprep video courses

Added 2026-11-20 on the `nhprep` branch (not released; [nhprep.md](../nhprep.md#qa-and-tests)). Backend files run in
WSL on a `netsec_nhprep_*_test` database; the e2e files need the NHprep stack.

| Test | What it proves | Evidence |
|---|---|---|
| `test_nhprep_access.py::test_nhprep_allows`, `::test_no_domain_list_means_nobody` | NHprep is for verified NetworkersHome students only, `qa` narrows to its list and never widens, no domain list means nobody | — |
| `test_nhprep_api.py::test_every_route_is_a_404_before_validation_for_anyone_nhprep_is_not_for`, `::test_mode_off_and_qa_narrow_who_nhprep_is_for`, `::test_reads_are_limited_per_user_and_an_outsider_never_takes_a_slot`; `test_authorization.py::test_nhprep_is_a_404_for_an_account_it_is_not_for` | Every `/v1/nhprep` route is a 404 for anyone else, before any id, query or body is checked, and spends no rate-limit slot | — |
| `test_nhprep_api.py::test_lesson_detail_has_its_course_neighbours_sections_and_study_aids_but_no_nhprep_ids_or_links`, `::test_a_removed_lesson_keeps_its_title_and_nothing_else` | No GET carries an NHprep id, page URL or video link; a removed lesson shows its title only | — |
| `test_nhprep_playback.py::test_a_ready_link_is_sent_uncached_and_never_logged`, `::test_only_a_link_to_this_video_on_nhpreps_video_host_passes`, `::test_a_bad_or_failed_signature_is_signing_failed_never_a_500`, `::test_playbacks_have_their_own_lower_limit_per_user` | A video link leaves only as `https://videos.nhprep.com/{videoId}.mp4?…`, with `no-store`, never in a log; anything else is `signing_failed` | Fake signer only |
| `test_nhprep_playback.py::test_no_signer_can_be_switched_on_before_it_exists`, `::test_qa_mode_needs_its_list_in_production` | Production refuses `NHPREP_PLAYBACK` other than `off`, and `qa` without a list | — |
| `test_nhprep_chat.py::test_nhprep_is_never_asked_for_an_account_it_is_not_for`, `::test_the_card_is_shown_only_to_a_reader_nhprep_is_for`, `::test_the_bank_parse_comes_first`, `::test_only_a_typed_message_without_files_can_be_an_nhprep_turn` | The card turn and stored cards follow the same rule per reader; the bank decides first; voice and attachments never become a card turn | — |
| `test_nhprep_chat.py::test_a_confident_match_is_a_fixed_line_and_the_cards_ids_with_no_model_call`, `::test_the_fixed_copy_is_plain_text_that_never_becomes_a_practice_test`, `::test_no_nhprep_log_line_holds_the_students_words` | A card reply is a fixed line plus ids, with no model call and no NHprep text; logs hold ids and codes only | — |
| `test_nhprep_import.py::test_normalisation_neutralises_widgets_directives_invisible_characters_and_nhprep_links`, `::test_a_video_is_a_stable_reference_never_a_url_or_signature`, `::test_read_export_checks_bytes_counts_and_completeness_and_counts_rejects` | Imported text can't carry a widget, directive, invisible character or nhprep.com link; a video with a URL or signature is refused; the bytes must match the manifest | — |
| `test_nhprep_import.py::test_a_second_import_waits_for_nobody_it_refuses_while_the_lock_is_held`, `::test_a_lost_lock_stops_the_run_marks_it_failed_and_a_rerun_completes`, `::test_removals_need_a_complete_export_and_a_mass_removal_needs_allow_removals`, `::test_refusals_capacity_and_egress_basis_write_nothing`, `::test_purge_deletes_the_catalogue_and_keeps_the_audit` | One import at a time; removals and mass removals are guarded; capacity and a missing egress basis refuse before any write; a purge keeps the audit | — |
| `test_nhprep_schema.py::test_the_embed_url_is_generated_canonical_and_cannot_be_written`, `::test_no_nhprep_table_holds_user_data`; `test_migrations.py::test_migration_0027_adds_the_nhprep_tables_and_refuses_to_drop_imports` | No URL can be written into the catalogue; NHprep holds no user data; 0027 is additive and its downgrade refuses while imports exist | `test_migrations.py` needs a booked slot; not recorded as run in the branch's commits |
| `test_architecture_guards.py::test_nhprep_stays_out_of_the_shared_knowledge_store_the_model_and_the_chat`, `::test_advisory_lock_keys_are_unique_across_the_app` | `app/nhprep` never imports the chat, a model, the shared knowledge store or an HTTP client; other modules reach it only through its public modules; the import lock key is its own | — |
| `src/features/nhprep/nhprep-markdown.test.tsx` | Imported Markdown: links to nhprep.com and its subdomains (not lookalikes) stay text, `javascript:` dropped, images never loaded, raw HTML as text, widget fences stay code | — |
| `src/features/nhprep/nhprep-panel.test.tsx` "the player's link check" | The client accepts only `https://videos.nhprep.com/{uuid}.mp4` | — |
| `scripts/nhprep-export/nhprep-export.test.ts` | The exporter's pacing gate, a password in a URL is detected, URLs and JSON are recorded as names and shapes only, the output folder must be outside every git tree, credentials parse without echoing | 14 pass (2026-11-20) |
| `e2e/security.e2e.ts` (`media-src`), `e2e/nhprep/video.nhprep.ts` | In a browser: media only from `videos.nhprep.com`, no new frame host; nothing loads before Play; the link never reaches the URL or storage | Headed, 14/14 for `e2e/nhprep` (commit `c7bc819`); live playback NOT TESTED |

## NH Friend Zone

Backend: `backend/tests/test_friend_zone_*.py`. Frontend: `features/friendzone/*.test.ts(x)` and
`lib/server/proxy-core.test.ts`. Invariants I14 to I25 are in [THREAT_MODEL.md](THREAT_MODEL.md).

| Test | What it proves | Evidence |
|---|---|---|
| `test_friend_zone_profiles.py::test_join_is_private_by_default_once_only_and_ends_with_the_invoice` | Every switch starts off; no account id, email or invoice in the profile; access ends with the invoice | — |
| `test_friend_zone_profiles.py::test_a_qa_account_joins_the_qa_cohort_for_good`, `::test_a_cross_cohort_friendship_cannot_exist` | The cohort cannot change, and the database refuses a cross-cohort row | — |
| `test_friend_zone_relations.py::test_search_finds_only_a_findable_member_and_every_miss_is_identical` | Every miss is the same 404; a blocked member is a miss; no invoice number or name in the logs (I17) | — |
| `test_friend_zone_relations.py::test_search_takes_the_floor_and_is_throttled` | The 0.4 s floor; the durable hourly limit, with `Retry-After` | — |
| `test_friend_zone_relations.py::test_unfriend_and_block_end_access_at_once_and_look_the_same` | A photo stops at the next request (I23); a block looks like an unfriend (I21) | — |
| `test_friend_zone_relations.py::test_a_qa_member_and_a_live_member_never_meet` | Search, profile, request, block and report are 404 across cohorts (I19) | — |
| `test_friend_zone_relations.py::test_races_end_in_one_consistent_state` | Crossing requests make one friendship; a block beats an accept (I20) | — |
| `test_friend_zone_battles.py::test_a_battle_runs_round_by_round_and_the_server_decides_it` | No key, explanation or opponent pick before a round closes; answers are final; the server scores (I15, I16) | — |
| `test_friend_zone_battles.py::test_only_human_approved_*`, `::test_a_question_withdrawn_mid_round_*` | Only competitive questions; a changed question voids its round (I18) | — |
| `test_friend_zone_battles.py::test_a_block_voids_an_active_battle_*` | A block voids the battle and reads as `ended` | — |
| `test_friend_zone_progress.py` | XP and badges are awarded once, never for void, capped or QA play (I25); AI checks count only for the exact text and only under the policy (I18) | — |
| `test_friend_zone_daily.py` | One attempt; server timing; secrecy until finished; QA never rewarded | — |
| `test_friend_zone_admin.py` | Moderation is audited; suspension hides at once and ends battles; QA cleanup is complete; retention | — |
| `proxy-core.test.ts` "two Friend Zone conflicts pass only their exact, validated fields" | The BFF forwards only `{battleId}` and `{available, needed}` | — |

## Deploy scripts and tooling

| Test | What it proves | Evidence |
|---|---|---|
| `deploy-netsec/tests/security-gate.test.mjs` | `deploy.sh` refuses a commit without a PASS receipt before any remote action; the override needs a reason and is logged; only a rollback to an earlier commit of `main` skips the gate; the gate's tools are pinned, gitleaks redacts, nothing is bypassed; allowlists are exact | — |
| `files-sandbox.test.mjs` "secrets never travel in process arguments" | The smoke cookie's secret and the role passwords go through environments | F-DEPLOY-1, A-22 |
| `deploy-netsec/tests/namespace.test.mjs` | No NetSec deploy file names PadhAI's paths, `systemctl start aichat-` or PadhAI's ports (Docker names and the hostname are not scanned); `safety.sh` keeps its denylist constants; isolated database identity; least-privilege roles; the backup path can't be redirected; the tunnel address check | — |
| `deploy-netsec/tests/legacy-guard.test.mjs` | PadhAI's `deploy/` stops at once in this repository, before any fetch or SSH | — |
| `scripts/tests/graph.test.mjs` | Graphify never reads ignored files (no key, no excluded code, in any output); updates and recovery work | 6/6 (its first run caught a wrong recovery flag) |
| `.semgrep/tests/netsec.py`, `.semgrep/tests/netsec.tsx` | The project's Semgrep rules match what they should and nothing else (run by `bun run security`) | — |

## Known gaps

Properties that no test holds today. Each is a candidate for the next change in that area, not a reason to add
tests on their own.

- **Pending proposals in the prompt:** no test asserts that a pending proposal's text never reaches the model
  (held by the structure of `recall_candidates`; [THREAT_MODEL.md, I3](THREAT_MODEL.md#security-invariants)).
- **Notifications:** the only cross-user test for `/v1/notifications/read` and `/clear` is in
  `test_learning_streak.py` (A-5).
- **Provider error text in logs:** A-13 is fixed in code (the log carries the error code and class only), but no
  test asserts that a failed generation's log line carries no provider text.
- **Avatar memory:** no peak-memory test with an 8000×5000 RGBA PNG (A-30's verifier suggested one).
- **Upload slots:** the "body deadline" tests call `readBody` directly; no test shows a slow upload freeing its slot
  through `proxyRequest`.
- **NHprep:** no signer exists, so no test vector pins one, and live playback against `videos.nhprep.com` is NOT
  TESTED; the NHprep e2e suite stubs the API in the browser, so the finder is not exercised end to end there.
- **Sandbox on the host:** `test_files.py` checks the unit's properties, not the host; only
  [`sandbox_probe.py`](../../backend/scripts/sandbox_probe.py), run on the host, proves them.
