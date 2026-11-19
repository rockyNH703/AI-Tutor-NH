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
  assertSafeDatabase,
} from "./e2e/learning/env";

/**
 * The learning suite (interactive blocks in the chat), headed Chromium on this PC, apart from the
 * default suite: its own ports, database, build (.next-learning-e2e) and files (`*.learning.ts`).
 *
 *   bunx playwright test -c playwright.learning.config.ts --headed
 *
 * The API runs on Windows from the backend venv, with the code of a backend snapshot outside the
 * repository and an empty working directory (so no .env is read). The full recipe, database
 * included: netsec-learning-evidence/stack/RECIPE.md.
 */
// Before any server starts (Playwright starts webServers before globalSetup).
assertSafeDatabase(DATABASE_URL);
assertSafeDatabase(AUTH_DATABASE_URL);

// Workers load this file again: the run's folder is fixed once by the runner and inherited.
process.env.LEARNING_RUN ??= new Date().toISOString().replace(/[:.]/g, "-");
const runDir = join(EVIDENCE_DIR, "runs", process.env.LEARNING_RUN);

const python = join(__dirname, "..", "..", "..", "backend", ".venv", "Scripts", "python.exe");
const snapshot = join(EVIDENCE_DIR, "stack", "backend-snapshot");
const apiCwd = join(EVIDENCE_DIR, "stack", "api-cwd");
mkdirSync(apiCwd, { recursive: true });

export default defineConfig({
  testDir: "./e2e/learning",
  testMatch: /\.learning\.ts$/,
  workers: 1,
  fullyParallel: false,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  timeout: 300_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { outputFolder: join(runDir, "report"), open: "never" }]],
  outputDir: join(runDir, "results"),
  globalSetup: "./e2e/learning/global-setup.ts",
  use: {
    baseURL: APP_URL,
    video: "on",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  // Headed by the project, so no --headed is needed (and --headed would break learning-pdf: page.pdf() is
  // headless only). Specs pick 834/390/320 per describe (e2e/learning/ui.ts `at`: Pixel 7 at phone widths).
  projects: [
    {
      name: "learning",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, headless: false },
      grepInvert: /@pdf/,
    },
    {
      // For watching: the @demo flows at one viewport, slowed down.
      name: "learning-demo",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, headless: false, launchOptions: { slowMo: 400 } },
      grep: /@demo/,
    },
    { name: "learning-webkit", use: { ...devices["iPhone 14"], headless: false }, grep: /@webkit/ },
    { name: "learning-pdf", use: { ...devices["Desktop Chrome"], headless: true }, grep: /@pdf/ },
  ],
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
        // Model-written tests are recorded as they are (production keeps the bank-only rule).
        QUESTION_BANK_TESTS: "false",
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
        NEXT_DIST_DIR: ".next-learning-e2e",
        API_BASE_URL: API_URL,
        NEXT_PUBLIC_SITE_URL: APP_URL,
        AUTH_DATABASE_URL,
        BETTER_AUTH_SECRET: AUTH_SECRET,
        AUTH_INTERNAL_TOKEN: INTERNAL_TOKEN,
        AUTH_RATE_LIMIT: "off",
        // Whatever a local .env.local says: no error reporting from test runs.
        NEXT_PUBLIC_SENTRY_DSN: "",
      },
    },
  ],
});
