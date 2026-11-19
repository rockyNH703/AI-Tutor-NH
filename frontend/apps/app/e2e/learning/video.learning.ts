import type { BrowserContext } from "@playwright/test";

import { LONG_MESSAGE, expectNoHorizontalOverflow, replies, sendAndWait } from "../support";
import { APP_URL } from "./env";
import { expect, fixture, openFixture, test } from "./support";
import { COPY, at, card, expectTargets, shot, transcriptScroll } from "./ui";

/**
 * ```video, gating checks with YouTube stubbed (plan §3 "Video", §7 "video, gating (stub)"): no request to
 * YouTube before Play; Play by keyboard focuses the frame; the exact privacy-enhanced src, title, sandbox,
 * allow and referrer (the bare origin); Jump to; offline with Retry; an invalid id shows the source; the CSP
 * allows exactly that frame; a long chat scrolls past a loaded video. The real player's keys are checked
 * separately and do not gate (video-live.learning.ts).
 */

type Video = { youtube: string; title: string; channel: string; start: number; chapters: { at: number; title: string }[]; captions: string };
const VIDEO = JSON.parse(/```video\n(.*)\n```/.exec(fixture("video").message)?.[1] ?? "{}") as Video;
const ID = new URL(VIDEO.youtube).searchParams.get("v") ?? "";
const EMBED = "https://www.youtube-nocookie.com";
const SANDBOX = "allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox";
const ALLOW = "autoplay; encrypted-media; picture-in-picture; fullscreen";

/** Every request for the privacy-enhanced player, answered by a local stub page. */
async function stubYouTube(context: BrowserContext): Promise<{ url: string; referer: string | undefined }[]> {
  const seen: { url: string; referer: string | undefined }[] = [];
  await context.route(`${EMBED}/**`, async (route) => {
    seen.push({ url: route.request().url(), referer: route.request().headers().referer });
    await route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>Player stub</title><p>Player stub</p>" });
  });
  return seen;
}

function params(src: string): [string, string][] {
  return [...new URL(src).searchParams.entries()].sort(([a], [b]) => a.localeCompare(b));
}

test("the cover, then Play by keyboard: the exact embed, and nothing from YouTube before it", { tag: ["@demo", "@webkit"] }, async ({ page, context, browserName }, testInfo) => {
  const seen = await stubYouTube(context);
  const { reply } = await openFixture(page, "video");
  const video = card(reply);

  await test.step("the cover: title, channel, the privacy note, Open on YouTube; no frame", async () => {
    await expect(video).toContainText(VIDEO.title);
    await expect(video).toContainText(VIDEO.channel);
    await expect(video.getByText(COPY.privacyNote)).toBeVisible();
    await expect(video.getByRole("link", { name: COPY.openOnYouTube })).toHaveAttribute("href", new RegExp(ID));
    await expect(video.locator("iframe")).toHaveCount(0);
    expect(seen, "nothing loaded from YouTube before Play").toEqual([]);
    await expectNoHorizontalOverflow(page);
    await shot(page, testInfo, "cover");
  });

  await test.step("Play from the keyboard gives the frame focus", async () => {
    await video.getByRole("button", { name: COPY.play }).focus();
    await page.keyboard.press("Enter");
    const frame = video.locator("iframe");
    await expect(frame).toBeFocused();
    const src = (await frame.getAttribute("src")) ?? "";
    expect(new URL(src).origin + new URL(src).pathname).toBe(`${EMBED}/embed/${ID}`);
    expect(params(src)).toEqual([
      ["autoplay", "1"],
      ["cc_lang_pref", VIDEO.captions],
      ["cc_load_policy", "1"],
      ["playsinline", "1"],
      ["rel", "0"],
      ["start", String(VIDEO.start)],
    ]);
    await expect(frame).toHaveAttribute("title", VIDEO.title);
    // Exactly the plan's tokens this browser knows (WebKit has no allow-presentation): Chromium gets all five.
    const known = await page.evaluate(
      (tokens) => tokens.split(" ").filter((token) => document.createElement("iframe").sandbox.supports(token)).join(" "),
      SANDBOX,
    );
    if (browserName === "chromium") expect(known).toBe(SANDBOX);
    await expect(frame).toHaveAttribute("sandbox", known);
    await expect(frame).toHaveAttribute("allow", ALLOW);
    await expect(frame).toHaveAttribute("allowfullscreen", "");
    await expect(frame).toHaveAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    await expect.poll(() => seen.length).toBeGreaterThan(0);
    expect(seen[0]?.referer, "the referrer is the bare origin").toBe(`${APP_URL}/`);
    await shot(page, testInfo, "playing");
  });
});

test("Jump to reloads the embed at its start", async ({ page, context }) => {
  await stubYouTube(context);
  const { reply } = await openFixture(page, "video");
  const video = card(reply);
  const areas = VIDEO.chapters.find((chapter) => chapter.at === 135);
  await video.getByRole("button", { name: new RegExp(`2:15 ${areas?.title ?? ""}`) }).click();
  await expect.poll(async () => new URL((await video.locator("iframe").getAttribute("src")) ?? EMBED).searchParams.get("start")).toBe("135");
});

test("offline: a Retry that returns focus to Play, then the frame", async ({ page, context, pageGuard }) => {
  pageGuard.allow(/ERR_INTERNET_DISCONNECTED|Failed to fetch/);
  await stubYouTube(context);
  const { reply } = await openFixture(page, "video");
  const video = card(reply);
  await context.setOffline(true);
  await video.getByRole("button", { name: COPY.play }).click();
  const retry = video.getByRole("button", { name: COPY.retry });
  await expect(retry).toBeVisible();
  await expect(video.locator("iframe")).toHaveCount(0);
  await context.setOffline(false);
  await retry.click();
  await expect(video.getByRole("button", { name: COPY.play })).toBeFocused();
  await video.getByRole("button", { name: COPY.play }).click();
  await expect(video.locator("iframe")).toBeVisible();
});

test("an invalid id shows the block's source, never a player", async ({ page }) => {
  const { reply } = await openFixture(page, "video-invalid");
  await expect(reply.locator("pre")).toContainText("not a video id");
  await expect(reply.getByRole("button", { name: COPY.play })).toHaveCount(0);
  await expect(reply.locator("iframe")).toHaveCount(0);
});

test("the CSP allows exactly the privacy-enhanced player as a frame", async ({ page }) => {
  await openFixture(page, "video");
  const policy = (await page.request.get(page.url())).headers()["content-security-policy"] ?? "";
  const frames = /(?:^|;)\s*frame-src ([^;]*)/.exec(policy)?.[1]?.trim().split(/\s+/) ?? [];
  expect(frames).toContain(EMBED);
  expect(frames.filter((source) => /youtube/.test(source))).toEqual([EMBED]);
});

test("a long chat scrolls past a loaded video, which stays loaded", async ({ page, context }) => {
  await stubYouTube(context);
  const { reply } = await openFixture(page, "video");
  const video = card(reply);
  await video.getByRole("button", { name: COPY.play }).click();
  const src = await video.locator("iframe").getAttribute("src");
  for (let turn = 0; turn < 3; turn += 1) await sendAndWait(page, LONG_MESSAGE);
  const transcript = page.getByRole("region", { name: "Conversation" });
  await transcript.hover();
  await page.mouse.wheel(0, -20_000);
  await expect.poll(() => transcriptScroll(page)).toBe(0);
  await page.mouse.wheel(0, 20_000);
  await expect.poll(() => transcriptScroll(page)).toBeGreaterThan(0);
  // `video` follows the last reply, now a newer one: the video is the chat's first reply.
  await expect(card(replies(page).first()).locator("iframe")).toHaveAttribute("src", src ?? "");
});

test.describe("390px", () => {
  test.use(at(390));
  test("touch targets on the cover and the Jump to list", async ({ page }) => {
    const { reply } = await openFixture(page, "video");
    await expect(card(reply).getByRole("button", { name: COPY.play })).toBeVisible();
    await expectTargets(card(reply));
    await expectNoHorizontalOverflow(page);
  });
});
