import type { Locator, Page } from "@playwright/test";

import { expectAccessible22 } from "../learning/ui";
import { waitForHydration } from "../support";
import { type Card, FIXTURE, type Lesson, chatWithCards, expect, lessonId, lessons, slot, stubNhprep, test, videoSrc } from "./support";

/**
 * G13 with a fake signer (Revision 5): the native player against browser routes. The API's links are stubbed;
 * the video host is served the 4-second MP4 fixture (e2e/nhprep/fixtures/lesson.mp4) or, for an expired link,
 * 410. Live playback against videos.nhprep.com is NOT TESTED until NHprep's signing is chosen.
 */

const CARD: Card = { ...(FIXTURE.cards[0] as Card), primaryLessonId: lessonId(1), views: ["video"] };
// The fixture's sections start at 0:45 and 5:00; the MP4 is 4 s long, so this lesson's are at 0 s and 2 s.
const SHORT: Partial<Lesson> = {
  chapters: [
    { id: "5d1e2f3a-4b5c-4d6e-8f70-81a2b3c4d5e6", title: "Why static routes", startSeconds: 0 },
    { id: "5d1e2f3a-4b5c-4d6e-8f70-81a2b3c4d5e7", title: "Floating statics", startSeconds: 2 },
  ],
};

const posts = (calls: string[]) => calls.filter((call) => call.startsWith("POST /lessons/"));
const currentTime = (video: Locator) => video.evaluate((element) => (element as HTMLVideoElement).currentTime);
const ready = (src: string) => ({ status: 200, body: { status: "ready", src, expiresIn: 600 } });

async function open(page: Page, gone: Set<string> = new Set(), override: Partial<Lesson> = SHORT) {
  const stub = await stubNhprep(page, lessons(3, override), gone);
  const chat = await chatWithCards(page, [CARD]);
  await page.goto(`/c/${chat}`);
  await waitForHydration(page);
  const card = slot(page);
  await expect(card.getByRole("button", { name: /^Play/ })).toBeVisible();
  return { stub, card };
}

test("Play asks for one link and plays it; Jump to seeks the same player without asking again", async ({ page }) => {
  const { stub, card } = await open(page);
  expect(posts(stub.calls)).toEqual([]);
  await card.getByRole("button", { name: "Play" }).click();
  const video = card.locator("video");
  await expect(video).toHaveAttribute("src", /^https:\/\/videos\.nhprep\.com\/[0-9a-f-]{36}\.mp4\?/);
  await expect(video).toHaveAttribute("controls", "");
  await expect(video).toHaveAttribute("preload", "none");
  await expect(video).toHaveAttribute("controlslist", "nodownload");
  await expect(video).toBeFocused();
  await expect.poll(() => currentTime(video)).toBeGreaterThan(0.2); // it really plays

  await card.getByRole("button", { name: /Floating statics/ }).click();
  await expect.poll(() => currentTime(video)).toBeGreaterThanOrEqual(2);
  expect(posts(stub.calls)).toHaveLength(1);
  await expect(card.locator("iframe")).toHaveCount(0);
  // The link lives only in the player: never in the address bar or storage.
  expect(page.url()).not.toContain("videos.nhprep.com");
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  expect(stored).not.toContain("videos.nhprep.com");
  await expectAccessible22(page, "[data-nhprep-slot]");
});

test("an expired link (410): one new link, resumed at the saved time", async ({ page }) => {
  const gone = new Set([videoSrc(1)]);
  const { stub, card } = await open(page, gone);
  stub.playback.push(ready(videoSrc(1)), ready(videoSrc(2)));
  await card.getByRole("button", { name: /Floating statics/ }).click(); // from the cover: plays from 0:02
  const video = card.locator("video");
  await expect(video).toHaveAttribute("src", videoSrc(2));
  await expect.poll(() => currentTime(video)).toBeGreaterThanOrEqual(2);
  expect(posts(stub.calls)).toHaveLength(2);
  expect(stub.media.some((line) => line.startsWith(`410 ${videoSrc(1)}`))).toBe(true);
  await expect(card.getByRole("button", { name: "Reload video" })).toHaveCount(0);
});

test("a second failure: the honest line and Reload video, which asks again and plays", async ({ page }) => {
  const gone = new Set([videoSrc(1), videoSrc(2)]);
  const { stub, card } = await open(page, gone);
  stub.playback.push(ready(videoSrc(1)), ready(videoSrc(2)), ready(videoSrc(3)));
  await card.getByRole("button", { name: "Play" }).click();
  await expect(card.getByRole("status").filter({ hasText: "The video stopped or couldn't load. Reload it to try again." })).toBeVisible();
  const reload = card.getByRole("button", { name: "Reload video" });
  await expect(reload).toBeVisible();
  expect(posts(stub.calls)).toHaveLength(2);
  await expectAccessible22(page, "[data-nhprep-slot]");

  await reload.click();
  const video = card.locator("video");
  await expect(video).toHaveAttribute("src", videoSrc(3));
  await expect.poll(() => currentTime(video)).toBeGreaterThan(0.2);
  expect(posts(stub.calls)).toHaveLength(3);
});

test("no link: not configured says so; a link outside videos.nhprep.com is never loaded", async ({ page }) => {
  const { stub, card } = await open(page);
  stub.playback.push({ status: 200, body: FIXTURE.playback.unavailable });
  await card.getByRole("button", { name: "Play" }).click();
  await expect(card.getByText("Video isn't available in NetSec AI yet.")).toBeVisible();
  await expect(card.locator("video")).toHaveCount(0);

  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  const other = await open(page);
  other.stub.playback.push(ready("https://videos.example.com/aaaaaaaa-0000-4000-8000-000000000009.mp4?sig=x"));
  await other.card.getByRole("button", { name: "Play" }).click();
  await expect(other.card.getByText("Video isn't available in NetSec AI yet.")).toBeVisible();
  await expect(other.card.locator("video")).toHaveCount(0);
  expect(requests.filter((url) => url.includes("videos.example.com"))).toEqual([]);
});

test("rate limited at Play: a status line and Try again, which plays", async ({ page }) => {
  const { stub, card } = await open(page);
  stub.playback.push({ status: 429, body: { error: { code: "rate_limited" } } });
  await card.getByRole("button", { name: "Play" }).click();
  await expect(card.getByRole("status").filter({ hasText: "You've reached the limit for now" })).toBeVisible();
  const again = card.getByRole("button", { name: "Try again" });
  await expect(again).toBeFocused();
  await again.click();
  await expect.poll(() => currentTime(card.locator("video"))).toBeGreaterThan(0.2);
});

test("the policy plays media only from videos.nhprep.com and frames no new host", async ({ page }) => {
  const response = await page.goto("/");
  const csp = response?.headers()["content-security-policy"] ?? "";
  const directive = (name: string) => csp.split("; ").find((part) => part.startsWith(`${name} `));
  expect(directive("media-src")).toBe("media-src 'self' https://videos.nhprep.com");
  expect(directive("frame-src")).toBe("frame-src https://challenges.cloudflare.com https://www.youtube-nocookie.com");
  expect(csp).not.toMatch(/mediadelivery|b-cdn|bunny/i);
});
