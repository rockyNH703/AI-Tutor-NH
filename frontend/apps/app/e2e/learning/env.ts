import { assertLoopback } from "../env";

/**
 * Where the learning suite runs: its own ports and its own database, so it never shares a server
 * or data with the default suite (3100/3101/4100) or the dev servers.
 *
 *   app      next start on :3210, built into .next-learning-e2e  (e2e/serve.mjs)
 *   preview  the same build on :3211 (serve.mjs always starts it)
 *   API      the stage-0 backend snapshot on :4210, fake LLM      (playwright.learning.config.ts)
 *   database netsec_learning_e2e_test in WSL's PostgreSQL 16
 *
 * The app is addressed as `localhost` (see ../env.ts: cookies are per host name).
 */
export const APP_PORT = 3210;
export const PREVIEW_PORT = 3211;
export const API_PORT = 4210;
export const APP_URL = `http://localhost:${APP_PORT}`;
export const API_URL = `http://127.0.0.1:${API_PORT}`;

const DB = "aichat:aichat@127.0.0.1:5432/netsec_learning_e2e_test";
/** The API's URL (SQLAlchemy + asyncpg). */
export const DATABASE_URL = `postgresql+asyncpg://${DB}`;
/** Better Auth's and the seeded sign-in's URL (libpq, the `pg` client). */
export const AUTH_DATABASE_URL = `postgresql://${DB}`;

/** The default suite's throwaway values (playwright.config.ts): never real ones. */
export const AUTH_SECRET = "e2e-only-better-auth-secret-" + "0123456789abcdef";
export const INTERNAL_TOKEN = "e2e-only-internal-token-" + "0123456789abcdef";

/** Outside the repository: videos, screenshots, reports and the stack's own files. */
export const EVIDENCE_DIR = "C:\\Users\\test\\Desktop\\netsec-learning-evidence";

/** Refuse any database that is not on this machine or not a disposable `*_test` one. */
export function assertSafeDatabase(url: string): void {
  assertLoopback("the learning database", url);
  const name = new URL(url).pathname.slice(1);
  if (!/^[a-z0-9_]+_test$/.test(name)) {
    throw new Error(`The learning suite writes only to a *_test database, got "${name}".`);
  }
}
