# Secure development workflow

> The workflow for every change to NetSec AI, from 2026-10-29. It adds security steps to the owner's rules in
> [CLAUDE.md](../../CLAUDE.md); where they differ, the owner's decision of 2026-10-29 applies: security-relevant
> behaviour gets a regression test, backend tests run on isolated `*_test` databases, and `bun run security` gates
> every deploy. The tools are described in [security-tools.md](security-tools.md).

## Contents

- [Before planning](#before-planning)
- [During implementation](#during-implementation)
- [Before completion](#before-completion)
- [The planning template](#the-planning-template)
- [Which review, when](#which-review-when)
- [Mandatory, advisory, and what needs a human](#mandatory-advisory-and-what-needs-a-human)

## Before planning

1. **Read the rules:** [CLAUDE.md](../../CLAUDE.md), `frontend/CLAUDE.md` for frontend work, and this folder.
2. **Know the starting point:** `git status` and `git log -5`. Note other people's uncommitted work, and never
   include it in your change.
3. **Find the blast radius:** `bun run graph`, then `affected` on every file or function you will change
   ([graphify.md](../graphify.md)):

   ```sh
   G="uv tool run --from graphifyy==0.9.97 graphify"
   export GRAPHIFY_NO_AUTO_REFRESH=1 PYTHONHASHSEED=0 PYTHONSAFEPATH=1
   $G affected "backend/app/chat/service.py"
   ```

   A missing edge proves nothing (it does not see `Depends(...)` or SQL); confirm in the code.
4. **Name the boundaries and data flows** the change touches, from [THREAT_MODEL.md](THREAT_MODEL.md#trust-boundaries-and-entry-points):
   browser → BFF, BFF → API, API → database, API → a provider, the extractor, the deploy, the shared host.
5. **Name the sensitive parts:** user content (configs, logs, files, memory), identity and sessions,
   authorisation, migrations, calls to a provider, production state. List the [invariants](THREAT_MODEL.md#security-invariants)
   that could break.
6. **Choose the skills and tools:** `security-audit` for security questions; `owasp-security-review` for the diff
   review; `gha-security-review` for `.github/workflows/`; Graphify; `sandbox_probe.py` for the extractor;
   `eval_engineering.py` for anything the model is told.
7. **Write the security requirements and their acceptance tests into the plan** ([template](#the-planning-template)).

## During implementation

- **Least privilege.** New database access goes through the runtime role's grants; new units and scripts get only
  what they need. Never widen a role, a sandbox property or an allowlist to make something work.
- **Validate untrusted input** at the boundary: request models with `extra="forbid"`, explicit limits on size and
  count, types checked by content as well as name.
- **Authorise on the server, at the data layer.** Every query carries the caller's `user_id` from the session;
  another user's resource is a 404. Never trust the BFF allowlist or a client field for authorisation.
- **Keep tenants apart:** conversations, files, memory, retrieval scope and jobs are per user. A new job reads per
  user and isolates one user's failure from the others.
- **Treat model-facing content as untrusted:** prompts, retrieved passages, documents, memory and model output.
  Put content in fenced, labelled blocks after the frozen system prompt; give the model no tools; render output
  through the sanitiser.
- **Mask before providers:** get models and embeddings only from `build_llm` and `build_embeddings`, which mask
  secrets. A new provider path (Ollalink, another API) does not get masking for free: add it or record why not.
- **No secrets or private content in logs:** log ids, lengths, types and reason codes. Never pass a secret in a
  process argument; use the environment.
- **SQL:** bound parameters only. The gate's Semgrep rule fails on SQL built from strings in `text()`.
- **Regression tests:** a security-relevant change gets a test that fails on the old code and passes on the new.
  Record the failing run. A new route with ownership gets its cross-user case in `test_authorization.py`.
- **Tests run on isolated databases** whose names end in `_test`, in WSL on the owner's PC
  ([how](security-regression-tests.md#how-to-run)).
- **No production mutation without explicit authorisation** ([below](#mandatory-advisory-and-what-needs-a-human)).
  Read-only checks (`bun run status:netsec`, logs) don't need it.

## Before completion

1. **Tests:** the focused tests for the change, then the relevant full suites (backend in WSL, `bun run test` in
   `frontend/`, `bun test deploy-netsec/tests` when deploy scripts changed, `bun run test:graph` when Graphify's
   wiring changed).
2. **Static checks:** `ruff check` and `ruff format --check`; `bun run typecheck`, `bun run lint`, `bun run build`.
   `bun run contract` when the API changed.
3. **The gate:** `bun run security`. On a clean, committed tree a PASS writes the receipt the deploy needs.
4. **Independent review:** for a security-sensitive change, a reviewer who did not write it (a person, or a fresh
   agent given the diff and not your conclusions) runs the `owasp-security-review` skill on the diff.
   `gha-security-review` for workflow changes.
5. **Docs:** update what the change made untrue: [README.md](README.md), [THREAT_MODEL.md](THREAT_MODEL.md),
   [security-regression-tests.md](security-regression-tests.md), and the operator docs.
6. **Record the evidence:** the commands run and their results, the checks skipped and why, and the residual
   risks. Say **BLOCKED** or **NOT TESTED** where that is true; never imply a pass you did not see. CI does not
   run: report it as NOT RUN, never as passed.
7. **Release** (when the task includes it): [production-security-checklist.md](production-security-checklist.md#release-acceptance).

## The planning template

```markdown
## Plan: <change>

**Current state:** what exists, with file:line; git status; what others have uncommitted.

**Scope:** what changes. **Non-goals:** what does not.

**Architecture and security impact:**
- Boundaries and data flows touched (THREAT_MODEL.md B1–B9):
- Sensitive data, authorisation, migrations, provider calls, production state:
- Invariants at risk (I1–I12) and how each stays true:
- Graphify `affected` results, and why each caller outside the change is safe:

**Skills and tools:** security-audit / owasp-security-review / gha-security-review / sandbox_probe / eval_engineering.

**Slices, in order:** each small enough to test and revert on its own.
1.
2.

**Dependencies:** other work, approvals, provider or host facts needed.

**Tests and acceptance criteria:**
- Security requirement → the test that fails on the old code → expected result.
- Suites to run; checks that cannot run here (say so).

**Migration, rollback, production safety:** backward-compatible migration? backup? how to roll back?
which steps need the owner's approval?

**Docs to update:**

**Adversarial review:** how could this be abused (THREAT_MODEL.md actors)? who reviews the diff?

**Completion evidence:** commands, results, skipped checks, residual risks; BLOCKED / NOT TESTED where true.
```

## Which review, when

| Change | Checks |
|---|---|
| Any change | Focused tests, static checks, `bun run security` |
| Touches authentication, sessions, authorisation, user data, memory, files, provider calls, the BFF, the renderer, secrets, deploy scripts or units | The above, plus the cross-user or failing-first regression test, and an independent `owasp-security-review` of the diff |
| `.github/workflows/` | `gha-security-review`, and zizmor in the gate |
| The extractor or its sandbox | `sandbox_probe.py` on the host after the release |
| What the model is told (prompt, profile text, context blocks) | `eval_engineering.py` on the host (real model) |
| A new trust boundary or provider, a new parser, a new sign-in method, turning on memory recall or automatic proposals, a public launch on a named domain, non-root units, a major upgrade of Better Auth or Next.js | **The full Cloudflare-method audit** (`security-audit` skill, full workflow), scoped to what changed and what it touches |
| Scheduled | The full audit every three months (recommended cadence; set by the owner), with [security-audit.md](security-audit.md) as the template |

## Mandatory, advisory, and what needs a human

| Control | Status |
|---|---|
| A PASS receipt from `bun run security` for the commit being deployed | **Mandatory, enforced** by `deploy-netsec/deploy.sh` **(released 0d868b7)**. The override `NETSEC_SECURITY_GATE=skip:<reason>` is logged (in the local checkout only) and is for emergencies |
| Focused and relevant tests, static checks, docs updated, evidence recorded | **Mandatory by this workflow** (not enforced by a tool) |
| An independent diff review for security-sensitive changes | **Mandatory by this workflow** |
| `Status: MATCH` and a healthy `bun run status:netsec` after a deploy | **Mandatory** ([checklist](production-security-checklist.md#release-acceptance)) |
| GitHub Actions CI | **Advisory, and not running** (the account is out of credit). Report it as NOT RUN |
| Deploying to production, changing the host, running migrations outside a deploy | **Needs the owner's explicit approval** |
| Turning on a switch in production (`MEMORY_RECALL_ENABLED`, `MEMORY_AUTOMATIC_PROPOSALS`, the jobs timer, `NOTIFICATIONS_MODE`, `INTENT_ROUTER`, `STUDY_SCOPE`, `VOICE_MODE`, `NHPREP_MODE` `qa` or `on`, `NHPREP_PLAYBACK` once a signer exists; [nhprep.md](../nhprep.md)) | **Needs the owner's explicit approval**. `NHPREP_MODE=off` is the NHprep kill switch (cards, turn and routes off after a restart) |
| Deleting or restoring production data | **Needs the owner's explicit approval**, with a backup first |
| Rotating a secret | **Needs the owner's explicit approval** ([how](production-security-checklist.md#rotating-a-key)) |
| Using the gate override | **Needs the owner's explicit approval**; the reason goes in the release notes |
