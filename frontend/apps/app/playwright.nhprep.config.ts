import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

import {
  API_PORT,
  API_URL,
  APP_PORT,
  APP_URL,
  AUTH_DATABASE_URL,
  AUTH_SECRET,
  DATABASE_URL,
  EVIDENCE_DIR,
  INTERNAL_TOKEN,
  PREVIEW_PORT,
  STUDENT_DOMAIN,
  assertSafeDatabase,
} from "./e2e/nhprep/env";

/**
 * The NHprep suite (video-course cards in the chat), headed Chromium, apart from the default and learning suites:
 * its own ports, database, build (.next-nhprep-e2e) and files (`e2e/nhprep/*.nhprep.ts`).
 *
 *   bunx playwright test -c playwright.nhprep.config.ts
 *
 * The API runs on Windows from the backend venv, with the code of a backend snapshot outside the repository
 * (refresh it as e2e/nhprep/env.ts says) and an empty working directory (so no .env is read).
 */
assertSafeDatabase(DATABASE_URL);
assertSafeDatabase(AUTH_DATABASE_URL);

process.env.NHPREP_RUN ??= new Date().toISOString().replace(/[:.]/g, "-");
const runDir = join(EVIDENCE_DIR, "runs", process.env.NHPREP_RUN);

const python = join(__dirname, "..", "..", "..", "backend", ".venv", "Scripts", "python.exe");
const snapshot = join(EVIDENCE_DIR, "stack", "backend-snapshot");
const apiCwd = join(EVIDENCE_DIR, "stack", "api-cwd");
mkdirSync(apiCwd, { recursive: true });

export default defineConfig({
  testDir: "./e2e/nhprep",
  testMatch: /\.nhprep\.ts$/,
  workers: 1,
  fullyParallel: false,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  timeout: 300_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { outputFolder: join(runDir, "report"), open: "never" }]],
  outputDir: join(runDir, "results"),
  globalSetup: "./e2e/nhprep/global-setup.ts",
  use: { baseURL: APP_URL, video: "on", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "nhprep", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, headless: false } }],
  webServer: [
    {
      command: `"${python}" -m uvicorn app.main:create_app --factory --app-dir "${snapshot}" --host 127.0.0.1 --port ${API_PORT}`,
      cwd: apiCwd,
      url: `${API_URL}/ready`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        DATABASE_URL,
        LLM_PROVIDER: "fake",
        LLM_FAKE_TOKEN_DELAY_S: "0.01",
        EMBEDDING_PROVIDER: "fake",
        EMAIL_BACKEND: "memory",
        FILES_ENABLED: "false",
        // Nothing leaves this machine: no classifiers, voice, external knowledge or memory recall.
        INTENT_ROUTER: "off",
        STUDY_SCOPE: "off",
        VOICE_MODE: "off",
        NCERT_KB_BASE_URL: "",
        MEMORY_RECALL_ENABLED: "false",
        MEMORY_AUTOMATIC_PROPOSALS: "off",
        CHAT_GENERATIONS_PER_MINUTE: "1000",
        CHAT_GENERATIONS_PER_DAY: "100000",
        NHPREP_MODE: "on",
        NHPREP_ALLOWED_EMAIL_DOMAINS: STUDENT_DOMAIN,
        AUTH_INTERNAL_TOKEN: INTERNAL_TOKEN,
        PYTHONUTF8: "1",
      },
    },
    {
      command: "node e2e/serve.mjs",
      url: `http://127.0.0.1:${APP_PORT}/login`,
      reuseExistingServer: false,
      timeout: 300_000,
      stdout: "pipe",
      env: {
        PORT: String(APP_PORT),
        PREVIEW_PORT: String(PREVIEW_PORT),
        NEXT_DIST_DIR: ".next-nhprep-e2e",
        API_BASE_URL: API_URL,
        NEXT_PUBLIC_SITE_URL: APP_URL,
        AUTH_DATABASE_URL,
        BETTER_AUTH_SECRET: AUTH_SECRET,
        AUTH_INTERNAL_TOKEN: INTERNAL_TOKEN,
        AUTH_RATE_LIMIT: "off",
        NEXT_PUBLIC_SENTRY_DSN: "",
      },
    },
  ],
});
