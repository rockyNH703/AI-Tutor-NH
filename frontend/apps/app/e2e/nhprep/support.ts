import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { type Page, type Route, test as base, expect } from "@playwright/test";
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
export async function sendTurn(page: Page, message: string, conversationId?: string): Promise<{ conversationId: string; done: DoneEvent }> {
  const response = await page.request.post(`${APP_URL}/api/v1/chat/stream`, {
    data: conversationId ? { message, conversationId } : { message },
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

// ------------------------------------------------------------------ NHprep cards without the finder

export type Card = {
  version: number;
  views: string[];
  status: "confident" | "ambiguous";
  primaryLessonId: string;
  alternativeLessonIds: string[];
  matchedItemId: string | null;
};
export type Summary = {
  id: string;
  title: string;
  course: { id: string; title: string };
  trainers: string[];
  position: number;
  lessonCount: number;
  durationSeconds: number | null;
};
export type Lesson = Summary & {
  description: string | null;
  chapters: { id: string; title: string; startSeconds: number | null }[];
  availability: Record<string, string>;
  previousLessonId: string | null;
  nextLessonId: string | null;
  video: "ready" | "no_video" | "not_configured";
  removed: boolean;
};

/**
 * The pinned wire shapes (L4's backend/tests/fixtures/nhprep/cards.json): metadata.nhprep cards and the
 * /v1/nhprep responses. The UI specs draw cards from them without the finder or imported lessons: the chat's
 * own turns are real (this worktree's API, fake LLM), and browser routes add the rest.
 */
export const FIXTURE = JSON.parse(
  readFileSync(join(__dirname, "..", "..", "..", "..", "..", "backend", "tests", "fixtures", "nhprep", "cards.json"), "utf8"),
) as {
  cards: Card[];
  lesson: Lesson;
  lessons: { items: Summary[] };
  items: Record<string, { items: { id: string; kind: string; title: string | null; bodyMd: string | null }[] }>;
  playback: { ready: { status: "ready"; src: string; expiresIn: number }; unavailable: { status: "unavailable"; reason: string } };
};

/** A synthetic lesson id of the fixture's family: `lessonId(1)` is the fixture's lesson, `lessonId(2)` its other match. */
export const lessonId = (n: number) => `0b8f6a52-3c1e-4d7a-9f10-2a6c5e4b7d${String(n).padStart(2, "0")}`;
/** A synthetic link of the shape the server signs (the client checks only its origin and path). */
export const videoSrc = (n: number) => `https://videos.nhprep.com/aaaaaaaa-0000-4000-8000-${String(n).padStart(12, "0")}.mp4?exp=1&sig=e2e`;

/** `count` lessons of one course, the fixture's first, so lists, ties and previous/next resolve; `override` on each. */
export function lessons(count = 8, override: Partial<Lesson> = {}): Map<string, Lesson> {
  const map = new Map<string, Lesson>();
  const tie = FIXTURE.lessons.items[0];
  for (let n = 1; n <= count; n++) {
    map.set(lessonId(n), {
      ...FIXTURE.lesson,
      ...(n === 2 && tie ? { title: tie.title, trainers: tie.trainers } : {}),
      ...(n > 2 ? { title: `Routing step ${n}` } : {}),
      id: lessonId(n),
      position: n,
      lessonCount: count,
      previousLessonId: n > 1 ? lessonId(n - 1) : null,
      nextLessonId: n < count ? lessonId(n + 1) : null,
      ...override,
    });
  }
  return map;
}

export type PlaybackAnswer = { status: number; body: unknown };
export type NhprepStub = {
  /** Every /api/v1/nhprep request the page made: "GET /lessons/{id}", "POST /lessons/{id}/playback", … */
  calls: string[];
  /** Answers for the next playback POSTs, in order; when empty, a ready link to the playable MP4. */
  playback: PlaybackAnswer[];
  /** Detail answers that replace a lesson's (a status and an error code), used once each. */
  detailFailures: Map<string, { status: number; code: string }[]>;
  /** Every request to videos.nhprep.com, as "<status> <url>". */
  media: string[];
};

const envelope = (code: string) => ({ error: { code, requestId: "e2e" } });
const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: "application/json", headers: { "Cache-Control": "no-store" }, body: JSON.stringify(body) });

const MP4 = readFileSync(join(__dirname, "fixtures", "lesson.mp4")); // 4 s, 64×36, VP9 in MP4 (Chromium plays it)

/** The MP4 fixture with byte ranges: the browser seeks only in a video it can ask ranges of. */
function serveMp4(route: Route) {
  const range = /bytes=(\d+)-(\d*)/.exec(route.request().headers().range ?? "");
  const headers = { "Content-Type": "video/mp4", "Accept-Ranges": "bytes" };
  if (!range) return route.fulfill({ status: 200, headers, body: MP4 });
  const start = Number(range[1]);
  const end = range[2] ? Math.min(Number(range[2]), MP4.length - 1) : MP4.length - 1;
  return route.fulfill({
    status: 206,
    headers: { ...headers, "Content-Range": `bytes ${start}-${end}/${MP4.length}` },
    body: MP4.subarray(start, end + 1),
  });
}

/**
 * The NHprep API and its video host, in the browser: lessons from `lessonMap`, items from the fixture, playback
 * from `stub.playback` (else a fresh playable link). Links in `gone` answer 410, as an expired link does.
 */
export async function stubNhprep(page: Page, lessonMap: Map<string, Lesson>, gone: Set<string> = new Set()): Promise<NhprepStub> {
  const stub: NhprepStub = { calls: [], playback: [], detailFailures: new Map(), media: [] };
  let links = 0;
  await page.route("**/api/v1/nhprep/**", async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const path = url.pathname.replace(/^\/api\/v1\/nhprep/, "");
    stub.calls.push(`${method} ${path}${url.search}`);
    if (method === "GET" && path === "/lessons") {
      const ids = (url.searchParams.get("ids") ?? "").split(",");
      return json(route, 200, { items: ids.flatMap((id) => lessonMap.get(id) ?? []) });
    }
    const match = /^\/lessons\/([0-9a-f-]{36})(\/items|\/playback)?$/.exec(path);
    const lesson = match?.[1] ? lessonMap.get(match[1]) : undefined;
    if (!match || !lesson) return json(route, 404, envelope("not_found"));
    if (method === "GET" && !match[2]) {
      const failure = stub.detailFailures.get(lesson.id)?.shift();
      return failure ? json(route, failure.status, envelope(failure.code)) : json(route, 200, lesson);
    }
    if (method === "GET" && match[2] === "/items") {
      return json(route, 200, FIXTURE.items[url.searchParams.get("kind") ?? ""] ?? { items: [] });
    }
    if (method === "POST" && match[2] === "/playback") {
      links += 1;
      const answer = stub.playback.shift() ?? { status: 200, body: { status: "ready", src: videoSrc(100 + links), expiresIn: 600 } };
      return json(route, answer.status, answer.body);
    }
    return json(route, 404, envelope("not_found"));
  });
  await page.route("https://videos.nhprep.com/**", (route) => {
    const url = route.request().url();
    const expired = gone.has(url);
    stub.media.push(`${expired ? 410 : 200} ${url}`);
    return expired ? route.fulfill({ status: 410, body: "" }) : serveMp4(route);
  });
  return stub;
}

/**
 * A chat of real turns (one per entry) whose replies, as the browser reads them, carry those cards in
 * metadata.nhprep, as L4's chat turn writes them (`null` leaves a reply plain). Returns the chat's id.
 */
export async function chatWithCards(page: Page, cards: (Card | null)[]): Promise<string> {
  let conversationId: string | undefined;
  for (let index = 0; index < cards.length; index++) {
    conversationId = (await sendTurn(page, `nhprep card ${index + 1}`, conversationId)).conversationId;
  }
  if (!conversationId) throw new Error("no turns were sent");
  await page.route("**/api/v1/conversations/*/messages*", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { items: { role: string; metadata?: Record<string, unknown> | null }[] };
    let reply = 0;
    for (const message of body.items) {
      if (message.role !== "assistant") continue;
      const card = cards[reply++];
      if (card) message.metadata = { ...(message.metadata ?? {}), nhprep: card };
    }
    await route.fulfill({ response, json: body });
  });
  return conversationId;
}

/** The NHprep slot of reply `index` (0-based among cards), or the last one. */
export const slot = (page: Page, index?: number) => {
  const all = page.locator("[data-nhprep-slot]");
  return index === undefined ? all.last() : all.nth(index);
};
