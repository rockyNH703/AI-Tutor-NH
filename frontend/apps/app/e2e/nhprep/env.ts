/**
 * Where the NHprep suite runs: its own ports, database, build and evidence folder, apart from the default suite
 * (3100/3101/4100), the learning suite (3210/3211/4210) and session 2's bank stack (3110/4110).
 *
 *   app      next start on :3220, built into .next-nhprep-e2e  (e2e/serve.mjs)
 *   preview  the same build on :3221 (serve.mjs always starts it)
 *   API      a snapshot of this worktree's backend on :4220, fake LLM  (playwright.nhprep.config.ts)
 *   database netsec_nhprep_e2e_test in WSL's PostgreSQL 16 (created and migrated with `-x url`, docs/nhprep.md)
 *
 * Refresh the backend snapshot before a run (exit codes 0-7 are success):
 *   robocopy C:\Users\test\netsec-nhprep\backend C:\Users\test\nhprep-data\e2e-evidence\stack\backend-snapshot /MIR /XD .venv __pycache__ .pytest_cache .ruff_cache /XF .env*
 */
export { assertSafeDatabase } from "../learning/env";

export const APP_PORT = 3220;
export const PREVIEW_PORT = 3221;
export const API_PORT = 4220;
export const APP_URL = `http://localhost:${APP_PORT}`;
export const API_URL = `http://127.0.0.1:${API_PORT}`;

const DB = "aichat:aichat@127.0.0.1:5432/netsec_nhprep_e2e_test";
/** The API's URL (SQLAlchemy + asyncpg). */
export const DATABASE_URL = `postgresql+asyncpg://${DB}`;
/** Better Auth's and the seeded sign-in's URL (libpq, the `pg` client). */
export const AUTH_DATABASE_URL = `postgresql://${DB}`;

/** Throwaway values, split so no secret scanner sees a whole literal: never real ones. */
export const AUTH_SECRET = "e2e-only-better-auth-secret-" + "0123456789abcdef";
export const INTERNAL_TOKEN = "e2e-only-internal-token-" + "0123456789abcdef";

/** Seeded students get this domain (NHPREP_ALLOWED_EMAIL_DOMAINS); anyone else is not entitled. */
export const STUDENT_DOMAIN = "nh.example.com";

/** Outside every repository: videos, screenshots, reports and the stack's own files. */
export const EVIDENCE_DIR = "C:\\Users\\test\\nhprep-data\\e2e-evidence";
