# Security policy

## Reporting a vulnerability

Report a suspected vulnerability privately by email to **sunny@networkershome.com**.
**Don't open a public GitHub issue**, and don't discuss it in public.

Please include:
- what is affected (a page, an API route, a script) and the commit, if you know it;
- the steps to reproduce it, and what you expected instead;
- what an attacker could gain (another user's data, a session, a secret, running code);
- any logs or screenshots, with personal data and secrets removed.

## Rules while you investigate

- **Use only accounts you own.** Never view, change or delete another person's data.
- **Don't run load or denial-of-service tests** against the live site, and don't run automated scanners there
  without written permission.
- **Never use or keep a credential, token or key you find.** Report it, so it can be rotated.
- **Stop and report as soon as you can show the problem.** Don't go further.

## Supported versions

Only the latest commit of `main` is supported. Fixes are released the normal way: a commit to `main` that passes the
local security gate (`bun run security`), then a deploy ([docs/deployment.md](docs/deployment.md)).

## Scope

**In scope:**
- the web app (`frontend/`), including its proxy and sign-in;
- the API (`backend/`);
- the deploy scripts (`deploy-netsec/`);
- NetSec AI's production deployment (its units, paths and tunnel on the shared host).

**Out of scope:** PadhAI, which shares the production host but is a separate application, and the outside services
NetSec AI uses. Report their problems to them:
- the model and embeddings providers;
- OllaLink;
- Cloudflare, Google, and the email providers.

## How NetSec AI is secured

The design is documented in [docs/security/README.md](docs/security/README.md):
- trust boundaries;
- sign-in and sessions;
- per-user ownership;
- secrets;
- the sandbox for uploaded files;
- AI-specific risks, including the masking of secrets in pasted configs before the LLM and embeddings providers
  see them (not in OllaLink's intent and scope classifier or voice requests, which are off by default);
- privacy in logs;
- where each rule is tested.

## If a secret is exposed

If a key, token or password ever appears in a commit, a log, an issue or a message:
1. **Treat it as compromised, and rotate it at once.**
   - Rotating the sign-in secrets and the database passwords is explained in
     [docs/deployment.md, "Sign-in (Better Auth)"](docs/deployment.md#sign-in-better-auth).
   - A provider key is replaced at the provider, then in the server's `/etc/netsecai/api.env`. The API reads it when
     it next starts.
   - Every rotation on the server needs the owner's explicit authorization.
2. Then remove it from wherever it appeared.

Deleting the commit alone doesn't make it safe.
