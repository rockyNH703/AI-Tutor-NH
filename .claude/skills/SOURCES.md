# Vendored security skills

Reviewed before first use (2026-10-29: a reviewer agent plus a skeptic re-check of every file, run
read-only). They are copied here unchanged except where this file says so. To update one: fetch the
new upstream commit into a scratch folder, diff it against this copy, review what changed (scripts,
commands, network access, file writes), then copy it over and update the commit below. To remove one:
delete its folder and its entry here. Usage, limits and guard rails: docs/security/security-tools.md.

| Folder | Upstream | Commit | Licence | Changes from upstream |
|---|---|---|---|---|
| `security-audit/` | https://github.com/cloudflare/security-audit-skill (`skills/security-audit/`) | `c1c8a8c1471069fb0e188eeaff69b8e8db6564a8` | MIT (`security-audit/LICENSE`) | None. Its validators (`validate-*.cjs`) need Linux Node: run them in WSL. |
| `owasp-security-review/` | https://github.com/getsentry/skills (`skills/security-review/`) | `d12f250460ffd961f549dbebf7eeb0cf4afd4c2e` | Apache-2.0 (`LICENSE-APACHE`); its reference text is CC BY-SA 4.0 from the OWASP Cheat Sheet Series (`LICENSE`): changes to this folder stay CC BY-SA 4.0 | Renamed from `security-review` (it clashed with the built-in `/security-review`); `allowed-tools` narrowed from `Read Grep Glob Bash Task` to `Read Grep Glob`; `disable-model-invocation: true` (runs only when asked by name) |
| `gha-security-review/` | https://github.com/getsentry/skills (`skills/gha-security-review/`) | `d12f250460ffd961f549dbebf7eeb0cf4afd4c2e` | Apache-2.0 (`LICENSE`) | `allowed-tools` narrowed to `Read Grep Glob`; `disable-model-invocation: true` |

Not vendored: Trail of Bits skills (CC BY-SA 4.0; installed per user as plugins if wanted),
semgrep/skills (guidance only, Semgrep Rules License), the OWASP secure-agent-playbook (used as a
reference). CodeQL is not used: its licence does not allow private repositories without GitHub
Advanced Security.
