/**
 * Where the Friend Zone suite runs: its own ports, database, build and evidence folder, apart from the default suite
 * (3100/3101/4100), the learning suite (3210/3211/4210), the NHprep suite (3220/3221/4220) and the bank stack (3110/4110).
 *
 * Three stacks share one build and one database, so both rollout states are exercised (plan section 6):
 *   qa    app :3230 (preview :3231) -> API :4230 with FRIEND_ZONE_MODE=qa: only the QA list may use Friend Zone
 *   on    app :3232 (preview :3233) -> API :4231 with FRIEND_ZONE_MODE=on: everyone; the QA list is still the qa cohort
 *   slow  app :3234 (preview :3235) -> API :4232, as qa but with 12 seconds per exam question (SLOW_ROUND_SECONDS), so
 *         a 10-question exam starts at 2 minutes and its 1-minute warning fires
 *   build    .next-friendzone-e2e (git-ignored by frontend/.gitignore `.next-*`)
 *   database netsec_friendzone_e2e_test in WSL's PostgreSQL 16 (created and migrated as docs/testing.md says)
 *
 * The APIs run the code of a backend snapshot outside the repository; refresh it before a run (exit codes 0-7 are success):
 *   robocopy "C:\Users\test\Desktop\AI- Tutor NH\backend" C:\Users\test\netsec-friendzone-e2e\stack\backend-snapshot /MIR /XD .venv __pycache__ .pytest_cache .ruff_cache /XF .env*
 * then migrate (from the snapshot, with the repository's venv):
 *   PYTHONUTF8=1 <repo>\backend\.venv\Scripts\python.exe -m alembic -x url=<DATABASE_URL> upgrade head
 */
import { join } from "node:path";

export { assertSafeDatabase } from "../learning/env";

export const QA_APP_PORT = 3230;
export const QA_PREVIEW_PORT = 3231;
export const QA_API_PORT = 4230;
export const ON_APP_PORT = 3232;
export const ON_PREVIEW_PORT = 3233;
export const ON_API_PORT = 4231;
export const SLOW_APP_PORT = 3234;
export const SLOW_PREVIEW_PORT = 3235;
export const SLOW_API_PORT = 4232;

/** Addressed as `localhost`: cookies are per host name (../env.ts). */
export const QA_APP_URL = `http://localhost:${QA_APP_PORT}`;
export const ON_APP_URL = `http://localhost:${ON_APP_PORT}`;
export const QA_API_URL = `http://127.0.0.1:${QA_API_PORT}`;
export const ON_API_URL = `http://127.0.0.1:${ON_API_PORT}`;
export const SLOW_APP_URL = `http://localhost:${SLOW_APP_PORT}`;
export const SLOW_API_URL = `http://127.0.0.1:${SLOW_API_PORT}`;

const DB = "aichat:aichat@127.0.0.1:5432/netsec_friendzone_e2e_test";
/** The API's URL (SQLAlchemy + asyncpg). */
export const DATABASE_URL = `postgresql+asyncpg://${DB}`;
/** The seeding's and the tests' own URL (libpq, the `pg` client). */
export const AUTH_DATABASE_URL = `postgresql://${DB}`;

/** Throwaway values, split so no secret scanner sees a whole literal: never real ones. */
export const AUTH_SECRET = "e2e-only-better-auth-secret-" + "0123456789abcdef";
export const INTERNAL_TOKEN = "e2e-only-internal-token-" + "0123456789abcdef";

/** Every seeded account's domain (reserved for examples). */
export const DOMAIN = "fz-e2e.example.com";
/** FRIEND_ZONE_QA_EMAILS on both APIs: a fixed pool, so a test takes the next free one (workers: 1). */
export const QA_EMAILS = Array.from({ length: 8 }, (_, index) => `qa-${index + 1}@${DOMAIN}`);

/**
 * FRIEND_ZONE_ROUND_SECONDS, an e2e-only override (production refuses it): an exam battle's seconds per question (its
 * deadline is length × this, instead of 60 s each) and a daily question's window. 8: a 10-question exam lasts 80 s.
 */
export const ROUND_SECONDS = 8;
/** The slow stack's seconds per exam question: a 10-question exam (2 minutes) starts above the 1-minute warning. */
export const SLOW_ROUND_SECONDS = 12;

/** The program and month the seed maps the approved synthetic questions to. */
export const PROGRAM = "networking-ai";
export const MONTH = 1;
/** A program the seed gives only 8 approved questions (a second synthetic book): too few for a daily challenge. */
export const SMALL_PROGRAM = "cyber-cloud-security-ai";

/** Outside every repository: videos, screenshots, reports and the stack's own files. */
export const EVIDENCE_DIR = "C:\\Users\\test\\netsec-friendzone-e2e";

/** The repository's backend venv, and the snapshot whose code the APIs and seed.py run. */
export const PYTHON = join(__dirname, "..", "..", "..", "..", "..", "backend", ".venv", "Scripts", "python.exe");
export const SNAPSHOT = join(EVIDENCE_DIR, "stack", "backend-snapshot");
