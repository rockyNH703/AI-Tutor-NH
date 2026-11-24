import { spawnSync } from "node:child_process";
import { join } from "node:path";

import type { FullConfig } from "@playwright/test";

import { assertLoopback } from "../env";
import {
  AUTH_DATABASE_URL,
  DATABASE_URL,
  ON_API_URL,
  ON_APP_URL,
  PYTHON,
  QA_API_URL,
  QA_APP_URL,
  SLOW_API_URL,
  SLOW_APP_URL,
  SNAPSHOT,
  assertSafeDatabase,
} from "./env";

/**
 * Before any test: everything local, the database a disposable `*_test` one, every API up on a current schema, and
 * the seed applied (seed.py: the approved synthetic questions on networking-ai month 1, and 8 on
 * cyber-cloud-security-ai month 1).
 */
export default async function globalSetup(config: FullConfig): Promise<void> {
  assertSafeDatabase(DATABASE_URL);
  assertSafeDatabase(AUTH_DATABASE_URL);
  for (const [name, url] of [
    ["the qa API", QA_API_URL],
    ["the on API", ON_API_URL],
    ["the qa app", QA_APP_URL],
    ["the on app", ON_APP_URL],
    ["the slow API", SLOW_API_URL],
    ["the slow app", SLOW_APP_URL],
  ] as const) {
    assertLoopback(name, url);
  }
  for (const project of config.projects) {
    if (project.use.baseURL) assertLoopback(`baseURL (${project.name})`, project.use.baseURL);
  }
  for (const api of [QA_API_URL, ON_API_URL, SLOW_API_URL]) {
    const ready = await fetch(`${api}/ready`).catch(() => null);
    const body = (ready?.ok ? await ready.json().catch(() => null) : null) as { status?: string; schema?: string } | null;
    if (body?.status !== "ok" || body.schema !== "current") {
      throw new Error(`The Friend Zone API is not ready on a current schema at ${api}/ready (${JSON.stringify(body)}). Migrate netsec_friendzone_e2e_test first (e2e/friendzone/env.ts).`);
    }
  }
  const seed = spawnSync(PYTHON, [join(__dirname, "seed.py")], {
    cwd: SNAPSHOT,
    env: { ...process.env, DATABASE_URL, PYTHONUTF8: "1" },
    encoding: "utf8",
  });
  if (seed.status !== 0) throw new Error(`seed.py failed (${seed.status}): ${seed.stderr || seed.stdout}`);
  process.stdout.write(seed.stdout);
}
