# Rollback

> NetSec AI, checked against `deploy-netsec/rollback.sh`, `deploy.sh` and `server/update.sh` on 2026-10-29. A
> rollback changes production: it needs the owner's explicit authorization.

A rollback is a deploy of an earlier commit by the same path as `deploy:netsec` ([deployment.md](deployment.md)),
so only the parts that differ are rebuilt or restarted.

```sh
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run rollback:netsec                 # the commit live before the last deploy
NETSEC_DEPLOY_HOST=root@10.1.50.94 bun run rollback:netsec -- <commit>     # any earlier commit (git log, or the host's HISTORY)
```

Without a commit it reads `/opt/netsecai/app-state/previous` on the host. It then runs `deploy-netsec/deploy.sh
--rollback-to <commit>`, which pushes that commit to the production Git repository, moves the checkout to it and:

- **Security gate:** not checked when the commit is an ancestor of GitHub `main` (restoring service must not wait
  on scanners); any other commit needs a PASS receipt like a deploy. The check is ancestry only
  ([`deploy.sh:93-99`](../deploy-netsec/deploy.sh)): it does not prove the commit was ever released, and the skip is
  not logged ([known-limitations.md](known-limitations.md#netsec-ai-2026-10-29)).
- **Tooling:** the `deploy-netsec/` scripts that run on the host come from this checkout's `HEAD`, not from the
  commit being restored, and rollback mode skips the clean-tree and `HEAD` = `main` checks: roll back from a clean
  checkout of `main`.
- **Frontend:** going back to the build the last deploy replaced (`.next-prev`) is a swap **without building**;
  otherwise the older frontend is built beside the served one first.
- **Backend:** the API restarts on the older code only if the backend differs.
- **Checks:** the same local smoke as a deploy, then the SHAs and a status line.

Afterwards `status:netsec` says `MISMATCH (the production checkout is not GitHub main)`. That is expected: fix the
problem, push, and `deploy:netsec` (with its receipt); the status is `MATCH` again.

Running the rollback twice does not go further back: a rollback never overwrites `previous`. To go further, name the
commit (`/opt/netsecai/app-state/HISTORY` lists every deploy and rollback).

## The database

A rollback never touches the database. If the commit you leave ran a migration, the older code runs on the newer
schema (the deploy prints a warning when the database is at a revision the code doesn't know). That is safe because
every migration is backward compatible ([deployment.md](deployment.md#migrations)). To undo a schema change, write a
new migration and deploy it. Never downgrade the production database by hand.

Every migration was preceded by a dump in `/var/backups/netsecai`. Restoring one loses everything written since, so
it is a last resort, done only with the owner's approval; the commands are in the header of
[`deploy-netsec/backup.sh`](../deploy-netsec/backup.sh) (dumps leave out file chunks, re-queued after a restore, and
live sessions and sign-in codes: everyone signs in again).

Users' files are database rows: a rollback keeps them. To stop only file processing, use the kill switch instead
(`FILES_ENABLED=false`, [deployment.md](deployment.md#file-library)).

## Automatic protection during a deploy

- **Frontend build fails:** users keep the previous build; nothing is restarted.
- **New frontend doesn't start:** the previous build is swapped back in and restarted automatically.
- **API fails its readiness check after a restart:** the deploy stops with `STOP:`; run `rollback:netsec`.
- **Migration fails:** the API is not restarted; the dump taken just before is named in the output.

## Emergency: without the scripts

Only if the deploy scripts themselves are broken, and only with the owner's authorization. On the host:

```sh
cd /opt/netsecai/app
git log --oneline -5                                   # pick a known-good commit
git reset --hard <commit> && printf 'commit=%s\n' "$(git rev-parse HEAD)" > RELEASE
systemctl restart netsecai-api                         # if the backend differs
# the frontend: a build of that commit must be in place (.next-prev is the one before the last deploy)
systemctl stop netsecai-web && mv frontend/apps/app/.next frontend/apps/app/.next-bad \
  && mv frontend/apps/app/.next-prev frontend/apps/app/.next && systemctl start netsecai-web
```

Then fix the scripts, commit, `bun run security`, push, and deploy normally: the next deploy compares against the
recorded state and redoes whatever is needed (`status:netsec` reports MISMATCH until then).
