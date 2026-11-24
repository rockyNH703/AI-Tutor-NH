import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

import {
  AUTH_DATABASE_URL,
  AUTH_SECRET,
  DATABASE_URL,
  EVIDENCE_DIR,
  INTERNAL_TOKEN,
  ON_API_PORT,
  ON_API_URL,
  ON_APP_PORT,
  ON_APP_URL,
  ON_PREVIEW_PORT,
  PYTHON,
  QA_API_PORT,
  QA_API_URL,
  QA_APP_PORT,
  QA_APP_URL,
  QA_EMAILS,
  QA_PREVIEW_PORT,
  ROUND_SECONDS,
  SLOW_API_PORT,
  SLOW_API_URL,
  SLOW_APP_PORT,
  SLOW_APP_URL,
  SLOW_PREVIEW_PORT,
  SLOW_ROUND_SECONDS,
  SNAPSHOT,
  assertSafeDatabase,
} from "./e2e/friendzone/env";

/**
 * The NH Friend Zone suite (plan P1.7), headed Chromium, apart from every other suite: its own ports, database, build
 * (.next-friendzone-e2e) and files (`e2e/friendzone/*.friendzone.ts`).
 *
 *   bunx playwright test -c playwright.friendzone.config.ts
 *
 * Three stacks on one build and one database (e2e/friendzone/env.ts): FRIEND_ZONE_MODE=qa (the QA list only),
 * FRIEND_ZONE_MODE=on (everyone; the QA list stays the isolated qa cohort), and qa with 12-second rounds (the warnings).
 * The `friendzone-webkit` project runs the specs tagged @webkit in WebKit, as the learning suite does. The APIs run on
 * Windows from the backend venv with the code of a snapshot outside the repository and an empty working directory (so
 * no .env is read).
 */
assertSafeDatabase(DATABASE_URL);
assertSafeDatabase(AUTH_DATABASE_URL);

process.env.FRIENDZONE_RUN ??= new Date().toISOString().replace(/[:.]/g, "-");
const runDir = join(EVIDENCE_DIR, "runs", process.env.FRIENDZONE_RUN);
const apiCwd = join(EVIDENCE_DIR, "stack", "api-cwd");
mkdirSync(apiCwd, { recursive: true });

function api(port: number, mode: "qa" | "on", roundSeconds = ROUND_SECONDS) {
  return {
    command: `"${PYTHON}" -m uvicorn app.main:create_app --factory --app-dir "${SNAPSHOT}" --host 127.0.0.1 --port ${port}`,
    cwd: apiCwd,
    url: `http://127.0.0.1:${port}/ready`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe" as const, // the API's own log lines, for a failure's evidence
    env: {
      DATABASE_URL,
      LLM_PROVIDER: "fake",
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
      NHPREP_MODE: "off",
      PROGRAMS_MODE: "on",
      FRIEND_ZONE_MODE: mode,
      FRIEND_ZONE_QA_EMAILS: QA_EMAILS.join(","),
      FRIEND_ZONE_KEY_POLICY: "human_only",
      FRIEND_ZONE_ROUND_SECONDS: String(roundSeconds),
      AUTH_INTERNAL_TOKEN: INTERNAL_TOKEN,
      PYTHONUTF8: "1",
    },
  };
}

function web(port: number, previewPort: number, apiUrl: string, appUrl: string, build: boolean) {
  return {
    command: "node e2e/serve.mjs",
    url: `http://127.0.0.1:${port}/login`,
    reuseExistingServer: false,
    timeout: 360_000,
    stdout: "pipe" as const,
    env: {
      ...(build ? {} : { E2E_SKIP_BUILD: "1" }), // webServers start in order: the qa app has built it by now
      PORT: String(port),
      PREVIEW_PORT: String(previewPort),
      NEXT_DIST_DIR: ".next-friendzone-e2e",
      API_BASE_URL: apiUrl,
      NEXT_PUBLIC_SITE_URL: appUrl,
      AUTH_DATABASE_URL,
      BETTER_AUTH_SECRET: AUTH_SECRET,
      AUTH_INTERNAL_TOKEN: INTERNAL_TOKEN,
      AUTH_RATE_LIMIT: "off",
      NEXT_PUBLIC_SENTRY_DSN: "",
    },
  };
}

export default defineConfig({
  testDir: "./e2e/friendzone",
  testMatch: /\.friendzone\.ts$/,
  workers: 1, // two-player battles, and the QA e-mail pool is handed out in order
  fullyParallel: false,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  timeout: 300_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { outputFolder: join(runDir, "report"), open: "never" }]],
  outputDir: join(runDir, "results"),
  globalSetup: "./e2e/friendzone/global-setup.ts",
  use: { baseURL: QA_APP_URL, video: "on", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    {
      name: "friendzone",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
        headless: false,
        // Two players are two headed windows on one screen. Windows' occlusion detection marks the covered one hidden,
        // and Friend Zone then (rightly) polls every 10 s, as for a background tab: a real player has one window.
        launchOptions: { args: ["--disable-features=CalculateNativeWinOcclusion", "--disable-backgrounding-occluded-windows"] },
      },
    },
    // Contexts are made per person (support.ts) at desktop size: this proves the WebKit engine, not a phone.
    { name: "friendzone-webkit", use: { ...devices["Desktop Safari"], headless: false }, grep: /@webkit/ },
  ],
  webServer: [
    api(QA_API_PORT, "qa"),
    api(ON_API_PORT, "on"),
    api(SLOW_API_PORT, "qa", SLOW_ROUND_SECONDS),
    web(QA_APP_PORT, QA_PREVIEW_PORT, QA_API_URL, QA_APP_URL, true),
    web(ON_APP_PORT, ON_PREVIEW_PORT, ON_API_URL, ON_APP_URL, false),
    web(SLOW_APP_PORT, SLOW_PREVIEW_PORT, SLOW_API_URL, SLOW_APP_URL, false),
  ],
});
