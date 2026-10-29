# Production setup history

> How NetSec AI's production came to be, from `git log` and the scripts in `deploy-netsec/` (checked 2026-10-29).
> What runs now is in [INFRASTRUCTURE.md](../INFRASTRUCTURE.md) and is shown by `bun run status:netsec`; this page
> does not claim live state. The PadhAI cutover runbook that used to be here is in this file's git history.

## Where NetSec AI came from

- **PadhAI** (Networkers Home's Class 6–12 tutor) runs its own production on the VM `10.1.50.94` from another
  repository: `/opt/aichat`, `aichat-*` units, `ayur.namahos.com`. Its own history (an Ayurveda chatbot replaced by
  PadhAI on 2026-09-24, the legacy stack removed on 2026-09-29) belongs to that repository.
- **This repository** starts on 2026-10-26 with PadhAI's code (`c264000`, `ccc4c20`), renamed to NetSec AI on
  2026-10-27 (`f30f83a`).

## How NetSec production was added beside PadhAI (2026-10-27)

NetSec AI was installed on the same VM in its own namespace, never sharing PadhAI's resources:

| Commit | Step |
|---|---|
| `f7c7535` | `deploy-netsec/`: `/opt/netsecai`, `/etc/netsecai`, `/var/backups/netsecai`, `netsecai-*` units, ports 4300/4400/55432, a dedicated Compose project, and `safety.sh`, a preflight self-check of NetSec's own constants (the isolation itself is the hard-coded `netsecai` names, checked by `deploy-netsec/tests/namespace.test.mjs`) |
| `6a89967` | `APP_NAME` must be `NetSec AI` in production (`Settings.validate_for_env`) |
| `3a9f312`, `a41dda3`, `2436487` | Public access through a manager-approved Cloudflare **Quick Tunnel** (`netsecai-cloudflared`), isolated from PadhAI's cloudflared and stable across app restarts |
| `aeae51a` | `status:netsec` fails when `PUBLIC_APP_URL` points at an old tunnel address |

The approvals were staged, and each stage must be reviewed before the next:
1. host inventory and installation (`deploy-netsec/install.sh`);
2. the first app deployment and its loopback smoke (`deploy:netsec`, `status:netsec`);
3. public ingress. The Quick Tunnel is in use; a named tunnel for `chat.netsecai.com`
   (`deploy-netsec/cloudflared/config.yml.template`) needs its own tunnel, credential and DNS approval, and is not
   in use.

## Data

NetSec's database started empty. `install.sh` writes the database identity marker (`NETSECAI_DB_IDENTITY`) only
when it has proven that the container, volume and network did not exist before and that the database has no tables;
it never adopts an existing or non-empty database. NetSec's scripts copy no data and refuse PadhAI's paths, ports
and Docker resources (`safety.sh`).

## Older layout

`update.sh` still carries a one-time cutover from a release layout (`/opt/netsecai/current` →
`/opt/netsecai/releases/<commit>`) to the checkout model, putting the old units back if it fails, and `status.sh`
notes leftover `/opt/netsecai/{backend,frontend,releases}` as "remove after approval". Whether the host ever had that
layout is not recorded here (unverified); nothing is removed without the owner's approval.
