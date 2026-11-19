import { randomBytes } from "node:crypto";

import { type Page, test as base, expect } from "@playwright/test";
import { Client } from "pg";

import { PLAIN_COOKIE } from "../../src/lib/server/cookies";
import { signedCookieValue, sseEvents } from "../learning/wire";
import { APP_URL, AUTH_DATABASE_URL, AUTH_SECRET, STUDENT_DOMAIN, assertSafeDatabase } from "./env";

export { expect };

const SESSION_MS = 24 * 60 * 60 * 1000;

export type SeededAccount = { email: string; userId: string };

/**
 * A verified student (STUDENT_DOMAIN) and a session written the way Better Auth writes them (as
 * e2e/learning/support.ts does), its signed cookie in the context's jar. Deleted after the test, chats included.
 */
export const test = base.extend<{ signedIn: SeededAccount }>({
  signedIn: [
    async ({ context }, use) => {
      assertSafeDatabase(AUTH_DATABASE_URL);
      const db = new Client({ connectionString: AUTH_DATABASE_URL });
      await db.connect();
      const email = `nhprep-${Date.now()}-${randomBytes(4).toString("hex")}@${STUDENT_DOMAIN}`;
      try {
        const token = randomBytes(24).toString("base64url");
        const now = Date.now();
        const { rows } = await db.query<{ id: string }>(
          "INSERT INTO users (email, email_verified, display_name) VALUES ($1, true, $2) RETURNING id",
          [email, "NHprep Tester"],
        );
        const userId = rows[0]?.id;
        if (!userId) throw new Error("the seeded account was not created");
        await db.query(
          "INSERT INTO auth_sessions (user_id, token, created_at, last_seen_at, expires_at) VALUES ($1, $2, $3, $3, $4)",
          [userId, token, new Date(now), new Date(now + SESSION_MS)],
        );
        await context.addCookies([
          { name: PLAIN_COOKIE, value: signedCookieValue(token, AUTH_SECRET), url: APP_URL, httpOnly: true, sameSite: "Lax" },
        ]);
        await use({ email, userId });
      } finally {
        await db.query("DELETE FROM users WHERE email = $1", [email]);
        await db.end();
      }
    },
    { auto: true },
  ],
});

export type DoneEvent = { message: { id: string; status: string; content: string; metadata?: Record<string, unknown> | null } };

/** One turn through the BFF as the chat sends it, read to the end of its stream (the fake LLM echoes "You said: …"). */
export async function sendTurn(page: Page, message: string): Promise<{ conversationId: string; done: DoneEvent }> {
  const response = await page.request.post(`${APP_URL}/api/v1/chat/stream`, {
    data: { message },
    headers: { Origin: APP_URL },
    timeout: 120_000,
  });
  expect(response.status(), "chat/stream").toBe(200);
  const events = sseEvents(await response.text());
  const meta = events.find((item) => item.event === "meta")?.data as { conversationId?: string } | undefined;
  const done = events.find((item) => item.event === "done")?.data as DoneEvent | undefined;
  if (!meta?.conversationId || !done) throw new Error(`the turn did not finish: ${JSON.stringify(events.at(-1) ?? null)}`);
  return { conversationId: meta.conversationId, done };
}
