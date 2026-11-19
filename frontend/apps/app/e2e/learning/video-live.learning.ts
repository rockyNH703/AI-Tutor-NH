import { expect, openFixture, test } from "./support";
import { COPY, card } from "./ui";

/**
 * The real YouTube player (plan §7 "video, live"): its own keys through the frame, with screenshots. It needs
 * the internet and YouTube's goodwill, so it never gates a release: it runs only with LEARNING_LIVE=1, and a
 * player that never appears (offline, or automation blocked) is reported BLOCKED for the owner, not failed.
 */

const KEYS: [key: string, what: string][] = [
  ["k", "pause"],
  ["k", "play"],
  ["j", "back 10 s"],
  ["l", "forward 10 s"],
  ["m", "mute"],
  ["m", "unmute"],
  ["Shift+Comma", "slower"],
  ["Shift+Period", "faster"],
  ["c", "captions"],
  ["f", "full screen"],
  ["f", "leave full screen"],
];

test("YouTube's keys work inside the privacy-enhanced player", { tag: "@live" }, async ({ page }, testInfo) => {
  test.skip(!process.env.LEARNING_LIVE, "non-gating live check: run with LEARNING_LIVE=1");
  const { reply } = await openFixture(page, "video-live");
  const play = card(reply).getByRole("button", { name: COPY.play });
  await play.focus();
  await page.keyboard.press("Enter"); // the frame takes focus, so the keys below reach the player
  const player = page.frameLocator('iframe[src^="https://www.youtube-nocookie.com/embed/"]').locator(".html5-video-player");
  const ready = await player.waitFor({ timeout: 30_000 }).then(
    () => true,
    () => false,
  );
  if (!ready) {
    testInfo.annotations.push({ type: "BLOCKED", description: "YouTube's player did not load (no internet, or automation blocked): held for the owner." });
    await page.screenshot({ path: testInfo.outputPath("blocked.png") });
    test.skip(true, "BLOCKED: the live player did not load");
  }
  for (const [index, [key, what]] of KEYS.entries()) {
    await page.keyboard.press(key);
    await page.screenshot({ path: testInfo.outputPath(`${String(index + 1).padStart(2, "0")}-${what.replace(/\W+/g, "-")}.png`) });
    if (what === "pause") await expect.soft(player).toHaveClass(/paused-mode/);
    if (what === "play") await expect.soft(player).toHaveClass(/playing-mode|buffering-mode/);
  }
});
