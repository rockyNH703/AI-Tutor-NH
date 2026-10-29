# Security tools and skills

What NetSec AI uses to find security problems, how to run each tool, and what each one cannot tell you. Every
version is pinned. Upgrading means reading the diff (scripts, commands, network access, file writes), then
changing the pin here and in the file that holds it. The other security pages are listed in
[README.md](README.md); when to use which tool is in [secure-development.md](secure-development.md#which-review-when).

## The local security gate (`bun run security`): the enforced check

GitHub Actions do not run for this repository (the account is out of credit), so CI enforces nothing. The gate is
`scripts/security.sh`. It runs on Windows and drives the scanners installed in WSL Ubuntu 24.04
(`/root/sec-tools`).

| Step | What it checks | Fails on |
|---|---|---|
| Semgrep CE 1.182.1 | Tracked code against the registry packs `p/python`, `p/typescript`, `p/javascript`, `p/secrets`, `p/security-audit`, plus the project rules in `.semgrep/netsec.yml` (each with a self-test in `.semgrep/tests/`) | Any finding |
| gitleaks 8.30.1 | The working tree (never git-ignored files) and the full history, `--redact` | A finding in released history or the tree; a finding only in local `refs/agents/...` refs is a warning |
| pip-audit 2.10.1 | `backend/requirements.lock` (hashes required) | Any known vulnerability |
| `bun audit` (Bun 1.3.14) | `frontend/bun.lock` | High or critical (moderate and low are listed) |
| zizmor 1.31.1 | `.github/workflows/` (offline) | Medium or above |

- **Receipt:** when every step passes on a clean tree, it writes `.git/netsec-security-gate` (commit, PASS, tool
  versions, time; never committed).
- **Deploy refusal:** `deploy-netsec/deploy.sh` refuses to deploy a commit without a matching PASS receipt. A
  rollback to any ancestor of `main` is never blocked: ancestry is all it checks, and that skip is not logged.
- **Override:** `NETSEC_SECURITY_GATE=skip:<reason>` prints a banner and appends to
  `.git/netsec-security-gate.overrides` in the local checkout only (the host's `HISTORY` does not record it). It is
  for emergencies, and the reason goes in the release notes.
- **Runtime:** about 3 minutes; Semgrep is most of it.

### Suppressions (each with a reason)

- **Excluded rule:** `python.sqlalchemy.security.audit.avoid-sqlalchemy-text` (`scripts/security.sh`). It flags
  every `text()` call, static SQL included. `netsec-sql-text-built-string` flags the dangerous form exactly: SQL
  built from strings in `backend/app`, `backend/scripts` and `backend/alembic`. App code has none; the last two,
  a lease interval and an HNSW setting, became bound parameters on 2026-10-29.
- **Test exclusion:** `netsec-ts-eval` skips `*.test.ts(x)`. One test evaluates the pre-paint theme script on
  purpose.
- **Inline `nosemgrep`:** two in `backend/tests/conftest.py`. One for the test harness running this repository's
  own alembic on the `*_test` database, one for a test account's fixed password.
- **gitleaks:** two exact allowlist entries (`.gitleaks.toml`), each with one path, one rule and one value: the
  test settings' fake secret key and a sample unsubscribe token in a contract test.
- **No blanket suppression:** no directory of source code is excluded. `.semgrepignore` lists only generated,
  vendored and data paths.

### Known limits

- **What a pass means:** it shows that these rules found nothing in the files they parse. It does not mean the
  code is secure. Semgrep could only partly parse a few TSX files; the run lists them.
- **Registry rules:** fetched at run time under the Semgrep Rules License (internal use; never redistribute them).
- **Advisory feeds:** pip-audit and `bun audit` only know published advisories.
- **What the receipt covers:** it names the commit, but the scanners read the files on disk; a sparse-checkout or
  skip-worktree file can differ from that commit while the tree looks clean
  ([known-limitations.md](../known-limitations.md#netsec-ai-2026-10-29)).

## Skills for Claude Code (vendored in `.claude/skills/`, see `.claude/skills/SOURCES.md`)

All three were reviewed before first use: a reviewer agent plus a skeptic re-check, read-only.

| Skill | Use it for | Notes |
|---|---|---|
| `security-audit` (Cloudflare, MIT, `c1c8a8c`) | Full audits: recon, then coverage-led hunting, then fresh verifiers, then a findings report. Also day-to-day security guidance | Invoked automatically for security questions; the full audit runs only when asked. Its validators (`validate-findings.cjs`, `validate-coverage-ledger.cjs`) need Linux Node, so run them in WSL (`node --test` passes 65/65). Output goes outside the repo (`~/security-audit-skill/...`). It never runs target code, installs nothing and contacts no deployed service |
| `owasp-security-review` (Sentry, Apache-2.0 + CC BY-SA 4.0 references) | An independent review of a diff before release | Renamed (it clashed with the built-in `/security-review`), limited to Read/Grep/Glob, runs only when asked by name. Its references contain shell commands: never run them on this PC (secrets live in git-ignored files) |
| `gha-security-review` (Sentry, Apache-2.0) | Changes to `.github/workflows/` | Same limits |

**Not used:**
- **CodeQL:** its licence does not allow private repositories without GitHub Advanced Security.
- **Trail of Bits skills:** CC BY-SA 4.0, and their Semgrep runner clones third-party rule repositories. Install
  them per user if wanted.
- **semgrep/skills:** guidance only.
- **OWASP secure-agent-playbook:** used as reference reading.

## Standards these checks map to

- [OWASP ASVS 5.0](https://github.com/OWASP/ASVS) (verification requirements);
- [OWASP API Security Top 10 2023](https://owasp.org/API-Security/);
- [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/) (LLM01 prompt injection, LLM02
  sensitive information disclosure, LLM05 improper output handling, LLM06 excessive agency, LLM08 vector and
  embedding weaknesses, LLM10 unbounded consumption);
- [NIST SSDF SP 800-218](https://csrc.nist.gov/Projects/ssdf) and its generative-AI profile, SP 800-218A.

## Other tools

- [Graphify](../graphify.md): the code graph, used for blast-radius checks in plans and reviews.
- [`backend/scripts/eval_engineering.py`](../../backend/scripts/eval_engineering.py): measures how the assistant
  behaves on 19 engineering and safety scenarios, against the real model, on the host.
- [`backend/scripts/sandbox_probe.py`](../../backend/scripts/sandbox_probe.py): proves on the host that the file
  extractor's sandbox cannot read NetSec's secrets, its deployment, PadhAI's files or the network.
