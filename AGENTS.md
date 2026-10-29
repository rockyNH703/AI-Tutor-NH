# AGENTS.md

For coding agents other than Claude Code. The canonical instructions are [CLAUDE.md](CLAUDE.md) (and
[frontend/CLAUDE.md](frontend/CLAUDE.md) for `frontend/`); read them first. The rules that matter most:

1. Work in this one checkout: no separate git worktrees or branches; never touch other people's uncommitted work.
2. `bun run security` must PASS before every push and deploy (the deploy checks its receipt).
3. No production change (deploy, rollback, host, settings, data) without the owner's explicit authorization.
4. Never open, print or commit the root key files (`dummy_acc.txt`, `git.txt`, `google_auth.txt`, `ollalink.txt`,
   `ollastack.txt`, `ollima.txt`, `openrouter.txt`) or any `.env*` file.
