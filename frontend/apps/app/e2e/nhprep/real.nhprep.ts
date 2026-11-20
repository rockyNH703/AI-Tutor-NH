import { randomBytes } from "node:crypto";

import type { BrowserContext, Page } from "@playwright/test";
import { Client } from "pg";

import { PLAIN_COOKIE } from "../../src/lib/server/cookies";
import { signedCookieValue } from "../learning/wire";
import { conversationIdOf, lastReply, replies, send, waitForHydration, waitForIdle } from "../support";
import { APP_URL, AUTH_DATABASE_URL, AUTH_SECRET, assertSafeDatabase } from "./env";
import { type Card, type Lesson, expect, slot, test } from "./support";

/**
 * G5, the real path: no browser routes. A seeded student types an NHprep request into the chat; this worktree's API
 * (NHPREP_MODE=on, NHPREP_PLAYBACK off, fake LLM) parses it, finds the lesson in the imported export
 * (netsec_nhprep_e2e_test) and writes metadata.nhprep; the card reads the real /v1/nhprep lesson. Anyone else gets
 * the ordinary reply, no card, and the 404 of a missing resource on every /v1/nhprep route.
 */

/** A dev-set case (partial-012) the API's finder (fake dense leg on) answers `confident` on the imported export. */
const REQUEST = "play the inter-vlan routing fhrp video please";
const CONFIDENT = "Here is the NHprep lesson that matches your request.";
const UNAVAILABLE = "Video isn't available in NetSec AI yet.";

type Message = { role: string; content: string; metadata?: { nhprep?: Card } | null };

async function messagesOf(page: Page, conversationId: string): Promise<Message[]> {
  const response = await page.request.get(`/api/v1/conversations/${conversationId}/messages`);
  expect(response.status(), "messages").toBe(200);
  return ((await response.json()) as { items: Message[] }).items;
}

async function lessonOf(page: Page, id: string): Promise<Lesson> {
  const response = await page.request.get(`/api/v1/nhprep/lessons/${id}`);
  expect(response.status(), `GET lesson ${id}`).toBe(200);
  return (await response.json()) as Lesson;
}

/** Types the request into the chat, as a student does, and waits for the whole reply. */
async function typeRequest(page: Page): Promise<string> {
  await page.goto("/");
  await waitForHydration(page);
  await send(page, REQUEST);
  await expect(replies(page)).toHaveCount(1, { timeout: 60_000 });
  await waitForIdle(page, 60_000);
  await page.waitForURL(/\/c\/[0-9a-f-]{36}$/);
  return conversationIdOf(page);
}

test("a student's typed request gets the real lesson card: title, sections, the honest video line, previous and next", async ({ page }, testInfo) => {
  const conversationId = await typeRequest(page);

  // The reply as stored: the fixed line and the card (ids only).
  const reply = (await messagesOf(page, conversationId)).filter((message) => message.role === "assistant").at(-1);
  expect(reply?.content).toBe(CONFIDENT);
  const card = reply?.metadata?.nhprep;
  if (!card) throw new Error("the reply carries no metadata.nhprep");
  expect(card).toMatchObject({ version: 1, status: "confident", views: ["video"] });
  const lesson = await lessonOf(page, card.primaryLessonId);
  expect(lesson.removed).toBe(false);
  expect(lesson.video, "NHPREP_PLAYBACK is off: no signer").toBe("not_configured");
  expect(lesson.chapters.length, "the chosen lesson has sections").toBeGreaterThan(0);

  // The card draws that lesson.
  const shown = slot(page);
  const title = shown.locator("[data-card='nhprep-lesson'] span[tabindex='-1']");
  await expect(lastReply(page)).toContainText(CONFIDENT);
  await expect(title).toHaveText(lesson.title);
  await expect(shown.getByText(`Lesson ${lesson.position} of ${lesson.lessonCount}`, { exact: false }).first()).toBeVisible();
  // The requested view (video) with playback off: the honest line, no Play, and the sections as a plain list.
  await expect(shown.getByRole("status").filter({ hasText: UNAVAILABLE })).toBeVisible();
  await expect(shown.getByRole("button", { name: /^Play/ })).toHaveCount(0);
  await expect(shown.getByText("In this video")).toBeVisible();
  const first = lesson.chapters[0];
  if (first) await expect(shown.getByText(first.title, { exact: true }).first()).toBeVisible();
  // What Play would ask for answers the same, through the BFF: no link while there is no signer.
  const play = await page.request.post(`/api/v1/nhprep/lessons/${lesson.id}/playback`, { headers: { Origin: APP_URL } });
  expect(play.status()).toBe(200);
  expect(await play.json()).toEqual({ status: "unavailable", reason: "not_configured" });
  await page.screenshot({ path: testInfo.outputPath("card.png") });

  // Previous / Next walk the real course and back.
  const forward = Boolean(lesson.nextLessonId);
  const neighbourId = forward ? lesson.nextLessonId : lesson.previousLessonId;
  if (!neighbourId) throw new Error("the lesson has no neighbour");
  const neighbour = await lessonOf(page, neighbourId);
  const go = shown.getByRole("button", { name: forward ? "Next lesson" : "Previous lesson" });
  const back = shown.getByRole("button", { name: forward ? "Previous lesson" : "Next lesson" });
  await go.click();
  await expect(title).toHaveText(neighbour.title);
  await expect(shown.getByText(`Lesson ${neighbour.position} of ${neighbour.lessonCount}`, { exact: false }).first()).toBeVisible();
  expect(neighbour.position).toBe(lesson.position + (forward ? 1 : -1));
  await back.click();
  await expect(title).toHaveText(lesson.title);
});

/** An account outside NHPREP_ALLOWED_EMAIL_DOMAINS, signed in in place of the fixture's student; deleted afterwards. */
async function signInOutsider(context: BrowserContext, db: Client): Promise<string> {
  const email = `nhprep-outsider-${Date.now()}-${randomBytes(4).toString("hex")}@example.com`;
  const token = randomBytes(24).toString("base64url");
  const { rows } = await db.query<{ id: string }>(
    "INSERT INTO users (email, email_verified, display_name) VALUES ($1, true, $2) RETURNING id",
    [email, "Not A Student"],
  );
  const now = Date.now();
  await db.query(
    "INSERT INTO auth_sessions (user_id, token, created_at, last_seen_at, expires_at) VALUES ($1, $2, $3, $3, $4)",
    [rows[0]?.id, token, new Date(now), new Date(now + 24 * 60 * 60 * 1000)],
  );
  await context.clearCookies();
  await context.addCookies([
    { name: PLAIN_COOKIE, value: signedCookieValue(token, AUTH_SECRET), url: APP_URL, httpOnly: true, sameSite: "Lax" },
  ]);
  return email;
}

test("anyone else: the ordinary reply, no card, and 404 on every /v1/nhprep route", async ({ page, context }) => {
  assertSafeDatabase(AUTH_DATABASE_URL);
  const db = new Client({ connectionString: AUTH_DATABASE_URL });
  await db.connect();
  let email: string | undefined;
  try {
    const lessonId = (await db.query<{ id: string }>("SELECT id FROM nhprep_lessons WHERE removed_at IS NULL ORDER BY id LIMIT 1")).rows[0]?.id;
    if (!lessonId) throw new Error("the e2e database holds no NHprep lessons: import the export first");
    email = await signInOutsider(context, db);
    const session = await page.request.get("/api/auth/get-session");
    expect(((await session.json()) as { user?: { email?: string } } | null)?.user?.email).toBe(email);

    const conversationId = await typeRequest(page);
    const reply = (await messagesOf(page, conversationId)).filter((message) => message.role === "assistant").at(-1);
    expect(reply?.metadata?.nhprep).toBeUndefined();
    expect(reply?.content).toBe(`You said: ${REQUEST}`); // the fake LLM's ordinary answer
    await expect(lastReply(page)).toContainText(`You said: ${REQUEST}`);
    await expect(page.locator("[data-nhprep-slot]")).toHaveCount(0);
    await expect(page.getByText(/NHprep/)).toHaveCount(0);

    const routes = [
      page.request.get(`/api/v1/nhprep/lessons?ids=${lessonId}`),
      page.request.get(`/api/v1/nhprep/lessons/${lessonId}`),
      page.request.get(`/api/v1/nhprep/lessons/${lessonId}/items?kind=note`),
      page.request.post(`/api/v1/nhprep/lessons/${lessonId}/playback`, { headers: { Origin: APP_URL } }),
    ];
    for (const response of await Promise.all(routes)) {
      expect(response.status(), response.url()).toBe(404);
      expect(((await response.json()) as { error?: { code?: string } }).error?.code).toBe("not_found");
    }
  } finally {
    if (email) await db.query("DELETE FROM users WHERE email = $1", [email]);
    await db.end();
  }
});
