import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { type Page, test as base, expect } from "@playwright/test";
import { Client } from "pg";

import { PLAIN_COOKIE } from "../../src/lib/server/cookies";
// Only page-relative helpers come from ../support. Never its `test` (its guard counts every frame's console),
// `account`, `seedTurn` or anything else bound to the default suite's :3100 or Mailpit.
import { lastReply, waitForHydration } from "../support";
import { APP_URL, AUTH_DATABASE_URL, AUTH_SECRET, assertSafeDatabase } from "./env";
import { signedCookieValue, sseEvents } from "./wire";

export { expect };

/** backend/tests/fixtures/learning/<name>.json: a message, and the code `build` refuses it with (null: it builds). */
export type LearningFixture = { message: string; refusal: string | null };

const FIXTURES = join(__dirname, "..", "..", "..", "..", "..", "backend", "tests", "fixtures", "learning");

/**
 * A fixture as checked by backend/tests/test_learning_fixtures.py. `mode: "exam"` turns a kind-* practice quiz
 * into the same questions as a mock exam (one word of its JSON; masking and size are unchanged by it).
 */
export function fixture(name: string, mode?: "exam"): LearningFixture {
  const found = JSON.parse(readFileSync(join(FIXTURES, `${name}.json`), "utf8")) as LearningFixture;
  if (!mode) return found;
  const message = found.message.replace('"mode":"practice"', '"mode":"exam"');
  if (message === found.message) throw new Error(`${name} has no practice mode to turn into an exam`);
  return { ...found, message };
}

const PRACTICE_FENCE = /^(`{3,}|~{3,})[ \t]*(quiz|mcq|fill-blank)\b[^\n]*\n([\s\S]*?)\n\1[ \t]*$/m;

/** The JSON of a fixture's practice block (its questions, options, explanations), so specs never copy them. */
// biome-ignore lint/suspicious/noExplicitAny: the author's JSON, read by tests only
export function blockOf(name: string): any {
  const body = PRACTICE_FENCE.exec(fixture(name).message)?.[3];
  if (body === undefined) throw new Error(`${name} has no practice block`);
  return JSON.parse(body);
}

const SESSION_MS = 24 * 60 * 60 * 1000;

export type SeededAccount = { email: string; userId: string };

/** What a test may expect the page to log: Chromium logs every 4xx/5xx it is shown (a 422 a test provoked). */
export type PageGuard = { allow: (pattern: RegExp) => void };

const APP_ORIGIN = new URL(APP_URL).origin;

export const test = base.extend<{ signedIn: SeededAccount; pageGuard: PageGuard }>({
  /**
   * Fails the test on any console error from the app's own origin (CSP violations included) and any
   * uncaught page error. A third-party frame's console (YouTube's player) is not the app's and is not
   * counted. A new account's first answer starts a streak, celebrated in a dialog over the page
   * (features/streak/celebrations): whenever one is in a test's way, it is closed.
   */
  pageGuard: [
    async ({ page }, use) => {
      const problems: string[] = [];
      const allowed: RegExp[] = [];
      page.on("console", (message) => {
        if (message.type() !== "error") return;
        const where = message.location().url || page.url();
        let origin = "";
        try {
          origin = new URL(where).origin;
        } catch {
          origin = APP_ORIGIN; // no location: the page itself (a CSP report has one)
        }
        if (origin !== APP_ORIGIN || allowed.some((pattern) => pattern.test(message.text()))) return;
        problems.push(`console: ${message.text()} (${where})`);
      });
      page.on("pageerror", (error) => problems.push(`Uncaught: ${error.message}`));
      await page.addLocatorHandler(page.locator("[data-celebration$='-dialog']"), async () => {
        await page.keyboard.press("Escape");
      });
      await use({ allow: (pattern) => allowed.push(pattern) });
      expect(problems, "no app console errors or uncaught errors in the page").toEqual([]);
    },
    { auto: true },
  ],
  /**
   * A verified account and a session written into the suite's database the way Better Auth writes
   * them (as backend/scripts/smoke_chat.py does), its signed cookie in the context's jar. No email
   * code, so no Mailpit. The account is deleted after the test, its chats with it.
   */
  signedIn: [
    async ({ context }, use) => {
      assertSafeDatabase(AUTH_DATABASE_URL);
      const db = new Client({ connectionString: AUTH_DATABASE_URL });
      await db.connect();
      const email = `learning-${Date.now()}-${randomBytes(4).toString("hex")}@example.com`;
      try {
        const token = randomBytes(24).toString("base64url");
        const now = Date.now();
        const { rows } = await db.query<{ id: string }>(
          "INSERT INTO users (email, email_verified, display_name) VALUES ($1, true, $2) RETURNING id",
          [email, "Learning Tester"],
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

/** End every session of the account (the session-expiry flow), in the suite's own database only. */
export async function endSessions(userId: string): Promise<void> {
  assertSafeDatabase(AUTH_DATABASE_URL);
  const db = new Client({ connectionString: AUTH_DATABASE_URL });
  await db.connect();
  try {
    await db.query("DELETE FROM auth_sessions WHERE user_id = $1", [userId]);
  } finally {
    await db.end();
  }
}

export type DoneEvent = {
  message: {
    id: string;
    status: string;
    content: string;
    metadata?: { assessments?: unknown[]; assessmentError?: { code: string } } | null;
  };
};

/**
 * One turn through the BFF exactly as the chat sends it, read to the end of its stream (the fake LLM
 * echoes "You said: <message>"). For setting up content fast; a test of the composer types instead.
 */
export async function sendTurn(page: Page, message: string, conversationId?: string): Promise<{ conversationId: string; done: DoneEvent }> {
  const response = await page.request.post(`${APP_URL}/api/v1/chat/stream`, {
    data: conversationId ? { conversationId, message } : { message },
    headers: { Origin: APP_URL },
    timeout: 120_000,
  });
  expect(response.status(), "chat/stream").toBe(200);
  const events = sseEvents(await response.text());
  const meta = events.find((item) => item.event === "meta")?.data as { conversationId?: string } | undefined;
  const done = events.find((item) => item.event === "done")?.data as DoneEvent | undefined;
  if (!meta?.conversationId || !done) {
    throw new Error(`the turn did not finish: ${JSON.stringify(events.at(-1) ?? null)}`);
  }
  return { conversationId: meta.conversationId, done };
}

/**
 * Send a fixture as a turn (into `conversationId`, or a new chat), check what the server recorded for it
 * (its practice record, or its refusal code), then open the chat. Returns the turn and the reply on screen.
 */
export async function openFixture(page: Page, name: string, options: { conversationId?: string; mode?: "exam" } = {}) {
  const { message, refusal } = fixture(name, options.mode);
  const turn = await sendTurn(page, message, options.conversationId);
  expect(turn.done.message.status, `${name}: the reply finished`).toBe("complete");
  const metadata = turn.done.message.metadata;
  if (refusal) {
    expect(metadata?.assessmentError?.code, `${name}: refused with its code`).toBe(refusal);
  } else if (PRACTICE_FENCE.test(message)) {
    expect(metadata?.assessments, `${name}: done.message.metadata.assessments`).toHaveLength(1);
  }
  await page.goto(`/c/${turn.conversationId}`);
  await waitForHydration(page);
  // Hydrated is not drawn: the transcript arrives after it, and specs count and read it right away.
  await expect(lastReply(page)).toBeVisible();
  return { ...turn, messageId: turn.done.message.id, reply: lastReply(page) };
}
