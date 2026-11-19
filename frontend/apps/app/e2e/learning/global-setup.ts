import type { FullConfig } from "@playwright/test";

import { assertLoopback } from "../env";
import { API_URL, AUTH_DATABASE_URL, DATABASE_URL, assertSafeDatabase } from "./env";

/**
 * Before any test: everything local, the database a disposable `*_test` one, and the API up on a
 * current schema. (playwright.learning.config.ts already refused an unsafe database before it
 * started the servers; Playwright starts its webServers before this runs.)
 */
export default async function globalSetup(config: FullConfig): Promise<void> {
  assertSafeDatabase(DATABASE_URL);
  assertSafeDatabase(AUTH_DATABASE_URL);
  assertLoopback("the learning API", API_URL);
  for (const project of config.projects) {
    if (project.use.baseURL) assertLoopback(`baseURL (${project.name})`, project.use.baseURL);
  }
  const ready = await fetch(`${API_URL}/ready`).catch(() => null);
  const body = (ready?.ok ? await ready.json().catch(() => null) : null) as { status?: string; schema?: string } | null;
  if (body?.status !== "ok" || body.schema !== "current") {
    throw new Error(
      `The learning API is not ready on a current schema at ${API_URL}/ready (${JSON.stringify(body)}). Migrate netsec_learning_e2e_test first (netsec-learning-evidence/stack/RECIPE.md).`,
    );
  }
}
