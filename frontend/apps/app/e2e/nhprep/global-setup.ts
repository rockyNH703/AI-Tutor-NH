import type { FullConfig } from "@playwright/test";

import { assertLoopback } from "../env";
import { API_URL, AUTH_DATABASE_URL, DATABASE_URL, assertSafeDatabase } from "./env";

/** Before any test: everything local, the database a disposable `*_test` one, the API up on a current schema. */
export default async function globalSetup(config: FullConfig): Promise<void> {
  assertSafeDatabase(DATABASE_URL);
  assertSafeDatabase(AUTH_DATABASE_URL);
  assertLoopback("the NHprep API", API_URL);
  for (const project of config.projects) {
    if (project.use.baseURL) assertLoopback(`baseURL (${project.name})`, project.use.baseURL);
  }
  const ready = await fetch(`${API_URL}/ready`).catch(() => null);
  const body = (ready?.ok ? await ready.json().catch(() => null) : null) as { status?: string; schema?: string } | null;
  if (body?.status !== "ok" || body.schema !== "current") {
    throw new Error(`The NHprep API is not ready on a current schema at ${API_URL}/ready (${JSON.stringify(body)}). Migrate netsec_nhprep_e2e_test first.`);
  }
}
