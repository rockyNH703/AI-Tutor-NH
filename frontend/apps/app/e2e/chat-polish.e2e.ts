import type { Locator, Page } from "@playwright/test";

import {
  chatLinks,
  composer,
  emptyGreeting,
  expect,
  expectAccessible,
  expectNoHorizontalOverflow,
  expectStudyGreeting,
  lastReply,
  renameChat,
  seedTurn,
  send,
  sendAndWait,
  test,
  waitForHydration,
  waitForIdle,
} from "./support";

/**
 * The chat's polish, as a student sees it: the header is the chat's name and chats are named
 * after their first answered question; the composer has no placeholder or footer; three dots
 * and nothing else until the first words; replies stream evenly and never show half-typed
 * Markdown, and do it within budget; the new chat's greeting; long titles in the sidebar.
 */

const heading = (page: Page) => page.getByRole("banner").getByRole("heading", { level: 1 });

/** "New chat", and wait until the new chat is on screen (typing sooner would go to the old one). */
async function newChat(page: Page): Promise<void> {
  await page.getByRole("link", { name: "New chat", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(heading(page)).toHaveText("New chat");
  await expectStudyGreeting(page);
}

test("the header is the chat's name: New chat, then named after the first answered question", async ({
  page,
  account: _,
}) => {
  await page.goto("/");
  await waitForHydration(page);
  await expect(heading(page)).toHaveText("New chat");
  await expect(page).toHaveTitle(/^New chat · /);
  // The chat is not listed as a tool of its own, and no slogan repeats it.
  await expect(page.getByText(/AI Tutor|Personal Doubts Solver|Network & security engineering assistant/)).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Tools" })).toHaveCount(0);

  await send(page, "Hi");
  await waitForIdle(page);
  await expect(heading(page)).toHaveText("New chat"); // small talk names nothing

  await sendAndWait(page, "Can you explain photosynthesis?");
  await expect(heading(page)).toHaveText("Photosynthesis Explained");
  await expect(chatLinks(page)).toHaveText(["Photosynthesis Explained"]);
  await expect(page).toHaveTitle(/^Photosynthesis Explained · /);

  await sendAndWait(page, "What is the difference between mitosis and meiosis?");
  await expect(heading(page)).toHaveText("Photosynthesis Explained"); // named once
  await page.reload();
  await expect(heading(page)).toHaveText("Photosynthesis Explained");
  await expect(chatLinks(page)).toHaveText(["Photosynthesis Explained"]);
});

test("the composer: no placeholder, no lines under it; its name and keys are for assistive technology", async ({
  page,
  account: _,
}) => {
  await page.goto("/");
  await waitForHydration(page);
  const box = composer(page);
  await expect(box).toHaveAccessibleName("Message NetSec AI");
  await expect(box).toHaveAccessibleDescription("Enter to send, Shift + Enter for a new line.");
  expect(await box.getAttribute("placeholder")).toBeNull();
  await expect(page.getByText(/Powered by|can make mistakes/)).toHaveCount(0);
  await expect(page.getByText("Enter to send, Shift + Enter for a new line.")).toHaveClass(/sr-only/);
  // Near the limit the count shows, in the same place.
  await box.fill("x".repeat(15_100));
  await expect(page.getByText("15,100 / 16,000")).toBeVisible();
  await box.fill("");
  await expect(page.getByText(/\/ 16,000/)).toHaveCount(0);
});

/** Records, in the page, what a streaming reply looks like at every change. */
async function watchReplies(page: Page): Promise<void> {
  await page.evaluate(() => {
    type Watch = {
      dotsAt: number | null;
      dotsGoneAt: number | null;
      firstTextAt: number | null;
      generatingText: boolean;
      katexErrors: number;
      rawMarkers: string[];
      firstBlock: Element | null;
      firstBlockReplaced: boolean;
      commits: number[];
      samples: { t: number; length: number }[];
      settledLength: number | null;
      longTasks: number[];
    };
    const watch: Watch = {
      dotsAt: null,
      dotsGoneAt: null,
      firstTextAt: null,
      generatingText: false,
      katexErrors: 0,
      rawMarkers: [],
      firstBlock: null,
      firstBlockReplaced: false,
      commits: [],
      samples: [],
      settledLength: null,
      longTasks: [],
    };
    Object.assign(window, { __watch: watch });
    const reply = () => [...document.querySelectorAll('article[aria-label="Assistant"]')].at(-1) ?? null;
    const content = () => reply()?.querySelector(".message-content") ?? null;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) watch.longTasks.push(entry.duration);
    }).observe({ entryTypes: ["longtask"] });
    new MutationObserver((records) => {
      const now = performance.now();
      const article = reply();
      if (!article) return;
      // A change inside the reply is one of its commits (its insertion is a change to the list).
      if (records.some((record) => article.contains(record.target))) watch.commits.push(now);
      const dots = article.querySelector("[data-streaming-dots]");
      if (dots && watch.dotsAt === null) watch.dotsAt = now;
      if (!dots && watch.dotsAt !== null && watch.dotsGoneAt === null) watch.dotsGoneAt = now;
      if (/generating/i.test(article.textContent ?? "")) watch.generatingText = true;
      const box = content();
      if (!box) return;
      if ((box.textContent ?? "").length > 0 && watch.firstTextAt === null) watch.firstTextAt = now;
      if (box.querySelector(".katex-error")) watch.katexErrors += 1;
      const visible = (box.textContent ?? "").replace(/```[\s\S]*?```/g, "");
      if (/\*\*|\$/.test(visible)) watch.rawMarkers.push(visible.slice(-60));
      if (box.children.length > 1 && watch.firstBlock === null) watch.firstBlock = box.firstElementChild;
      if (watch.firstBlock && (box.firstElementChild !== watch.firstBlock || !watch.firstBlock.isConnected)) {
        watch.firstBlockReplaced = true;
      }
      if (article.getAttribute("aria-busy") === "false" && watch.settledLength === null) {
        watch.settledLength = (box.textContent ?? "").length;
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["aria-busy"] });
    setInterval(() => {
      const box = content();
      if (box && reply()?.getAttribute("aria-busy") === "true") {
        watch.samples.push({ t: performance.now(), length: (box.textContent ?? "").length });
      }
    }, 50);
  });
}

type Watch = {
  dotsAt: number | null;
  dotsGoneAt: number | null;
  firstTextAt: number | null;
  generatingText: boolean;
  katexErrors: number;
  rawMarkers: string[];
  firstBlockReplaced: boolean;
  commits: number[];
  samples: { t: number; length: number }[];
  settledLength: number | null;
  longTasks: number[];
};
const watched = (page: Page) =>
  page.evaluate(() => {
    const { firstBlock: _, ...rest } = (window as unknown as { __watch: Watch & { firstBlock: unknown } }).__watch;
    return rest as Watch;
  });

test("three dots and nothing else until the first words, then the words replace them", async ({ page, account: _ }) => {
  await page.goto("/");
  await waitForHydration(page);
  await watchReplies(page);
  await sendAndWait(page, "Explain photosynthesis");
  const watch = await watched(page);
  expect(watch.dotsAt, "the dots showed").not.toBeNull();
  expect(watch.firstTextAt, "then the words").not.toBeNull();
  expect(watch.dotsAt as number).toBeLessThanOrEqual(watch.firstTextAt as number);
  expect((watch.dotsGoneAt as number) - (watch.firstTextAt as number), "gone with the first words").toBeLessThanOrEqual(100);
  expect(watch.generatingText, "no 'generating' text").toBe(false);
  await expect(lastReply(page).locator("[data-streaming-dots]")).toHaveCount(0);
});

/** A tutor-like reply of about 2,000 characters: headings, lists, bold, code, maths, a table. */
const RICH = [
  "Here is the whole picture.",
  "",
  "# Photosynthesis",
  "",
  "Plants turn **light energy** into chemical energy. The overall equation is $6CO_2 + 6H_2O \\to C_6H_{12}O_6 + 6O_2$ and it happens in the `chloroplast`.",
  "",
  "## The two stages",
  "",
  "1. **Light reactions** split water and make ATP and NADPH.",
  "2. **The Calvin cycle** fixes carbon dioxide into sugar.",
  "",
  "$$",
  "\\text{rate} = \\frac{\\Delta O_2}{\\Delta t}",
  "$$",
  "",
  "| Stage | Where | Makes |",
  "|---|---|---|",
  "| Light reactions | Thylakoid | ATP, NADPH, O2 |",
  "| Calvin cycle | Stroma | Glucose |",
  "",
  "```python",
  "def rate(oxygen, seconds):",
  "    return oxygen / seconds",
  "```",
  "",
  "- Chlorophyll absorbs red and blue light, so leaves look green.",
  "- More light raises the rate until another factor limits it.",
  "- Temperature matters because the enzymes of the Calvin cycle work best when warm.",
  "",
  "## Why it matters",
  "",
  "Almost every food chain starts with photosynthesis, and it keeps the oxygen in the air we breathe. ".repeat(6).trim(),
  "",
  "**Summary:** light in, sugar and oxygen out.",
].join("\n");

test("a long rich reply streams evenly, never half-typed, within budget, and ends as it reloads", async ({
  page,
  account: _,
}) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await waitForHydration(page);
  await watchReplies(page);
  const started = Date.now();
  // ~330 words at the fake model's 50 ms each stream for ~17 s; the default 30 s left too little for the reply
  // to be saved when the shared test database is busy.
  await sendAndWait(page, RICH, 60_000);
  const seconds = (Date.now() - started) / 1000;
  const watch = await watched(page);
  // A new account's first answer starts its streak, and its celebration opens over the page once the chat is
  // still. Close it before reading the reply: the reads below are evaluate()s, which the pageGuard's locator
  // handler never runs before (it guards only actions and auto-waiting assertions), so they raced the dialog.
  const celebration = page.locator("[data-celebration$='-dialog']");
  if (await celebration.waitFor({ timeout: 10_000 }).then(() => true, () => false)) await page.keyboard.press("Escape");
  await expect(celebration).toHaveCount(0);

  // Never a maths error or a raw ** / $$ on screen, and finished blocks are never redrawn.
  expect(watch.katexErrors, "katex errors while streaming").toBe(0);
  expect(watch.rawMarkers, "raw Markdown markers while streaming").toEqual([]);
  expect(watch.firstBlockReplaced, "the first block was redrawn").toBe(false);

  // Even: most steps are small; everything is on screen the moment it completes.
  const steps = watch.samples.slice(1).map((sample, index) => sample.length - (watch.samples[index]?.length ?? 0));
  const moving = steps.filter((step) => step > 0);
  expect(moving.length, "it moved many times").toBeGreaterThan(20);
  expect(moving.filter((step) => step <= 120).length / moving.length).toBeGreaterThanOrEqual(0.8);
  const final = await lastReply(page).locator(".message-content").evaluate((element) => (element.textContent ?? "").length);
  expect(watch.settledLength, "all of it when it finished").toBe(final);

  // Budget: no long task over 150 ms; long tasks under 15% of the streaming time; <= 25 commits/s.
  expect(Math.max(0, ...watch.longTasks), "longest task (ms)").toBeLessThan(150);
  expect(watch.longTasks.reduce((sum, value) => sum + value, 0) / 1000 / seconds).toBeLessThan(0.15);
  const span = ((watch.commits.at(-1) ?? 0) - (watch.commits[0] ?? 0)) / 1000;
  expect(watch.commits.length / Math.max(span, 1), "reply commits per second").toBeLessThanOrEqual(25);

  // What streamed is exactly what a reload shows.
  const streamed = await lastReply(page).locator(".message-content").innerHTML();
  await page.reload();
  await expect(lastReply(page).locator(".katex-display")).toHaveCount(1);
  expect(await lastReply(page).locator(".message-content").innerHTML()).toBe(streamed);
});

test("memory stays flat over fifteen turns", async ({ page, account: _ }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await waitForHydration(page);
  const cdp = await page.context().newCDPSession(page);
  const heap = async () => {
    await cdp.send("HeapProfiler.collectGarbage");
    return (await cdp.send("Runtime.getHeapUsage")).usedSize;
  };
  await sendAndWait(page, "Explain photosynthesis");
  const before = await heap();
  for (let turn = 0; turn < 15; turn++) await sendAndWait(page, `Question number ${turn} about the water cycle`);
  expect((await heap()) - before, "heap growth (bytes)").toBeLessThan(40 * 1024 * 1024);
});

test("the greeting: one of the fixed pool, a new one for the next chat, solid ink with one NH-red word", async ({
  page,
  account: _,
}) => {
  await page.goto("/");
  await waitForHydration(page);
  const first = await expectStudyGreeting(page);
  const words = emptyGreeting(page).locator("[data-greeting-word]");
  await expect(words).toHaveCount(first.split(" ").length);
  await expect(emptyGreeting(page)).toHaveAccessibleName(first); // one heading, one sentence

  // Solid colour, never gradient text; exactly one word in NH red (--signal), the rest in ink.
  const paint = await words.evaluateAll((spans) => {
    const probe = document.createElement("span");
    probe.style.color = "hsl(var(--signal))";
    document.body.append(probe);
    const signal = getComputedStyle(probe).color;
    probe.remove();
    return spans.map((span) => {
      const style = getComputedStyle(span);
      return { color: style.color, clip: style.backgroundClip, red: style.color === signal };
    });
  });
  for (const word of paint) {
    expect(word.clip, "no gradient text").not.toBe("text");
    expect(word.color, "a solid, visible colour").not.toBe("rgba(0, 0, 0, 0)");
  }
  expect(paint.filter((word) => word.red), "one key word in NH red").toHaveLength(1);
  // One arrival for the whole line, not a word-by-word stagger.
  expect(await emptyGreeting(page).evaluate((heading) => getComputedStyle(heading).animationName)).toBe("greeting-in");

  // The next new chat gets a different greeting.
  await sendAndWait(page, "Explain photosynthesis");
  await newChat(page);
  const second = await expectStudyGreeting(page);
  expect(second).not.toBe(first);
});

/** The lowest contrast of the greeting's words (solid colours) against the page behind them. */
async function renderedGreetingContrast(page: Page): Promise<number> {
  return emptyGreeting(page)
    .locator("[data-greeting-word]")
    .evaluateAll((spans) => {
      const rgb = (value: string) => (value.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
      const luminance = ([r, g, b]: number[]) =>
        [r ?? 0, g ?? 0, b ?? 0]
          .map((value) => value / 255)
          .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
          .reduce((sum, c, channel) => sum + c * ([0.2126, 0.7152, 0.0722][channel] ?? 0), 0);
      const ground = luminance(rgb(getComputedStyle(document.body).backgroundColor));
      return Math.min(
        ...spans.map((span) => {
          const text = luminance(rgb(getComputedStyle(span).color));
          return (Math.max(text, ground) + 0.05) / (Math.min(text, ground) + 0.05);
        }),
      );
    });
}

test("the greeting as rendered clears 4.5:1 at every point: light and dark, wide and narrow", async ({
  page,
  account: _,
}) => {
  test.setTimeout(120_000);
  for (const scheme of ["light", "dark"] as const) {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
      await page.goto("/");
      await waitForHydration(page);
      // One of the fixed pool (lib/greeting.ts), picked at random: whichever it is must pass.
      const text = await expectStudyGreeting(page);
      const words = emptyGreeting(page).locator("[data-greeting-word]");
      const lines = await words.evaluateAll((spans) => new Set(spans.map((span) => (span as HTMLElement).offsetTop)).size);
      expect(lines, "the greeting stays on one line, phone or desktop").toBe(1);
      const lowest = await renderedGreetingContrast(page);
      expect(lowest, `"${text}", ${lines} line(s), ${scheme}, ${width}px`).toBeGreaterThanOrEqual(4.5);
      // Reduced motion: the line in place at once.
      const style = await emptyGreeting(page).evaluate((heading) => `${getComputedStyle(heading).animationName}|${getComputedStyle(heading).opacity}`);
      expect(style).toBe("none|1");
    }
  }
  await expectAccessible(page);
});

/** WCAG contrast of an element's own text colour against its own background colour. */
const ownContrast = (locator: Locator) =>
  locator.evaluate((element) => {
    const channels = (value: string) => (value.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
    const luminance = (value: string) =>
      channels(value)
        .map((v) => v / 255)
        .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
        .reduce((sum, c, index) => sum + c * ([0.2126, 0.7152, 0.0722][index] ?? 0), 0);
    const style = getComputedStyle(element);
    const [a, b] = [luminance(style.color), luminance(style.backgroundColor)];
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  });

/** WCAG contrast between two tokens, each read from a fresh element (no transition to catch). */
const tokenContrast = (page: Page, one: string, two: string) =>
  page.evaluate(
    ({ first, second }) => {
      const luminance = (token: string) => {
        const probe = document.createElement("span");
        probe.style.color = `hsl(var(${token}))`;
        document.body.append(probe);
        const value = getComputedStyle(probe).color;
        probe.remove();
        return (value.match(/[\d.]+/g) ?? [])
          .slice(0, 3)
          .map(Number)
          .map((v) => v / 255)
          .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
          .reduce((sum, c, index) => sum + c * ([0.2126, 0.7152, 0.0722][index] ?? 0), 0);
      };
      const [a, b] = [luminance(first), luminance(second)];
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    },
    { first: one, second: two },
  );

test("primary controls read clearly in both themes: at rest, hovered, disabled, and the focus ring", async ({
  page,
  account: _,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/");
    await waitForHydration(page);
    const ask = page.getByRole("complementary").getByRole("link", { name: "New chat", exact: true });
    const send = page.getByRole("button", { name: "Send message" });
    expect(await ownContrast(ask), `New chat, ${scheme}`).toBeGreaterThanOrEqual(4.5);
    await ask.hover();
    await page.waitForTimeout(300);
    expect(await ownContrast(ask), `New chat hovered, ${scheme}`).toBeGreaterThanOrEqual(4.5);
    await page.mouse.move(800, 300);
    await expect(send).toBeDisabled();
    await page.waitForTimeout(300);
    expect(await ownContrast(send), `Send disabled, ${scheme}`).toBeGreaterThanOrEqual(3);
    await composer(page).fill("Explain gravity");
    await page.waitForTimeout(300);
    expect(await ownContrast(send), `Send, ${scheme}`).toBeGreaterThanOrEqual(4.5);
    await send.hover();
    await page.waitForTimeout(300);
    expect(await ownContrast(send), `Send hovered, ${scheme}`).toBeGreaterThanOrEqual(4.5);
    await composer(page).fill("");
    expect(await tokenContrast(page, "--ring", "--background"), `focus ring on the page, ${scheme}`).toBeGreaterThanOrEqual(3);
    expect(await tokenContrast(page, "--ring", "--card"), `focus ring on a card, ${scheme}`).toBeGreaterThanOrEqual(3);

    await page.goto("/settings/privacy");
    const button = page.getByRole("button", { name: "Change password", exact: true });
    await expect(button).toBeVisible();
    expect(await ownContrast(button), `a primary Button, ${scheme}`).toBeGreaterThanOrEqual(4.5);
  }
});

test("a long title moves only while its row is hovered or keyboard-focused; at rest an ellipsis, sharp from the left", async ({
  page,
  account: _,
}) => {
  const LONG = "A very long chat title about photosynthesis, respiration and the carbon cycle in green plants";
  const short = await seedTurn(page, "Define osmosis");
  const long = await seedTurn(page, "Explain photosynthesis");
  await renameChat(page, long, LONG);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await waitForHydration(page);

  const row = (id: string) => page.locator(`nav[aria-labelledby='chats-heading'] a[href='/c/${id}']`);
  const state = (id: string) =>
    row(id)
      .locator(".marquee")
      .evaluate((box) => {
        const track = box.querySelector(".marquee-track") as HTMLElement;
        const style = getComputedStyle(box);
        const text = track.firstChild as Text;
        const range = document.createRange();
        range.setStart(text, 0);
        range.setEnd(text, 1);
        return {
          overflowing: box.hasAttribute("data-overflowing"),
          textOverflow: style.textOverflow,
          mask: style.maskImage || style.webkitMaskImage || "none",
          animation: getComputedStyle(track).animationName,
          running: track.getAnimations().length,
          firstLetterInside: range.getBoundingClientRect().left >= box.getBoundingClientRect().left - 0.5,
        };
      });
  const moving = () =>
    page.locator(".marquee-track").evaluateAll((tracks) => tracks.reduce((count, track) => count + track.getAnimations().length, 0));

  // At rest: still, an ellipsis, no mask, the first letter fully inside; nothing moves anywhere. (It is
  // measured only when hovered or focused, the only times it can move.)
  await page.mouse.move(900, 400);
  await expect(row(long)).toHaveAccessibleName(LONG);
  expect(await state(long)).toEqual({
    overflowing: false,
    textOverflow: "ellipsis",
    mask: "none",
    animation: "none",
    running: 0,
    firstLetterInside: true,
  });
  expect(await moving()).toBe(0);
  const restWidth = (await row(long).boundingBox())?.width;

  // Hovered: after a short hold it slides, with a fade on the RIGHT edge only; the row keeps its width.
  await row(long).hover();
  await page.waitForTimeout(600);
  const hovered = await state(long);
  expect(hovered.animation).toBe("marquee");
  expect(hovered.running).toBe(1);
  expect(hovered.mask).toMatch(/^linear-gradient\((to right|90deg), rgb\(0, 0, 0\) .*rgba\(0, 0, 0, 0\)\)$/);
  expect((await row(long).boundingBox())?.width).toBe(restWidth);
  await expectNoHorizontalOverflow(page);

  // A short title never moves; leaving the long one stops it.
  await row(short).hover();
  await page.waitForTimeout(600);
  expect((await state(short)).running).toBe(0);
  expect(await state(long)).toMatchObject({ running: 0, mask: "none", textOverflow: "ellipsis" });

  // Keyboard focus moves it; moving focus on stops it. (Reached back from its own Rename button,
  // whatever the sidebar lists above the chats.)
  await page.mouse.move(900, 400);
  await page.getByRole("complementary").getByRole("button", { name: `Rename “${LONG}”` }).focus();
  await page.keyboard.press("Shift+Tab");
  await expect(row(long)).toBeFocused();
  await page.waitForTimeout(600);
  expect((await state(long)).animation).toBe("marquee");
  await page.keyboard.press("Shift+Tab");
  await page.waitForTimeout(100);
  expect((await state(long)).running).toBe(0);

  // A click is not keyboard focus: once the pointer leaves, it is still.
  await row(long).click();
  await expect(page).toHaveURL(new RegExp(`/c/${long}$`));
  await page.mouse.move(900, 400);
  await page.waitForTimeout(600);
  expect((await state(long)).running).toBe(0);

  // Reduced motion, the device's and then the app's own: never moves, not even hovered.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await row(long).hover();
  await page.waitForTimeout(600);
  expect(await state(long)).toMatchObject({ animation: "none", mask: "none", textOverflow: "ellipsis" });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.mouse.move(900, 400);
  await page.evaluate(() => document.documentElement.setAttribute("data-motion", "reduce"));
  await row(long).hover();
  await page.waitForTimeout(600);
  expect(await state(long)).toMatchObject({ animation: "none", mask: "none" });
});

test("short screens: the greeting and the composer stay in view, the picture steps aside below 600px", async ({
  page,
  account: _,
}) => {
  const sizes = [
    [1280, 600],
    [1280, 500],
    [1280, 450],
    [390, 500],
    [390, 450],
    [390, 420], // a phone with its keyboard open
    [844, 390],
  ] as const;
  for (const [width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await waitForHydration(page);
    await expectStudyGreeting(page);
    const header = await page.getByRole("banner").boundingBox();
    const top = (header?.y ?? 0) + (header?.height ?? 0) - 1;
    for (const [name, locator] of [
      ["greeting", emptyGreeting(page)],
      ["composer", composer(page)],
    ] as const) {
      const box = await locator.boundingBox();
      expect(box && box.y >= top && box.y + box.height <= height + 0.5, `${name} in view at ${width}×${height}`).toBe(true);
    }
    await expect(page.locator("[data-study-illustration]")).toHaveCount(0);
    expect(await page.evaluate(() => (document.scrollingElement?.scrollHeight ?? 0) - window.innerHeight)).toBeLessThanOrEqual(0);
    await expectNoHorizontalOverflow(page);
  }
});

test("keyboard: the controls come in reading order", async ({ page, account: _ }) => {
  await seedTurn(page, "Define osmosis");
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await waitForHydration(page);
  const names: string[] = [];
  // Enough presses to reach the message box (about 17 now) with room to spare: the sidebar's places
  // (Skills & Certifications, File Library, Lab Activity) sit between "New chat" and the chats. The order
  // below is checked, not the count.
  for (let press = 0; press < 24; press++) {
    await page.keyboard.press("Tab");
    names.push(
      await page.evaluate(() => {
        const element = document.activeElement as HTMLElement | null;
        return element?.getAttribute("aria-label") || element?.textContent?.trim() || element?.tagName || "";
      }),
    );
  }
  const expected = [
    /^Skip to content$/,
    /NetSec AI/,
    /^New chat$/,
    /^Osmosis Definition$/,
    /^Rename/,
    /^Delete/,
    /^Account: /,
    /sidebar$/,
    /streak/i,
    /^Notifications/,
    /^Colour theme$/,
    /^Message NetSec AI$|^TEXTAREA$/,
  ];
  let at = 0;
  for (const name of names) if (at < expected.length && expected[at]?.test(name)) at += 1;
  expect(at, `in order: ${names.join(" → ")}`).toBe(expected.length);
});

test("no layout shift on arrival or on the first message; the greeting's animations end, titles never start at rest", async ({
  page,
  account: _,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.addInitScript(() => {
    const store = window as unknown as { __shift: number };
    store.__shift = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) {
        if (!entry.hadRecentInput) store.__shift += entry.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  await page.goto("/");
  await waitForHydration(page);
  await expectStudyGreeting(page);
  await page.waitForTimeout(2000);
  const shift = () => page.evaluate(() => (window as unknown as { __shift: number }).__shift);
  expect(await shift(), "layout shift on arrival").toBeLessThan(0.02);
  const greeting = await page
    .locator("[data-greeting-word]")
    .evaluateAll((spans) => spans.flatMap((span) => span.getAnimations().map((animation) => animation.playState)));
  expect(greeting.every((state) => state === "finished"), `greeting animations: ${greeting.join(",")}`).toBe(true);
  expect(await page.locator(".marquee-track").evaluateAll((tracks) => tracks.reduce((n, t) => n + t.getAnimations().length, 0))).toBe(0);

  await page.evaluate(() => {
    (window as unknown as { __shift: number }).__shift = 0;
  });
  await sendAndWait(page, "Explain photosynthesis");
  await page.waitForTimeout(500);
  expect(await shift(), "layout shift after the first message").toBeLessThan(0.02);
});

test("a hostile first message names the chat in plain words only", async ({ page, account: _ }) => {
  let dialogs = 0;
  page.on("dialog", (dialog) => {
    dialogs += 1;
    void dialog.dismiss();
  });
  await page.goto("/");
  await waitForHydration(page);
  await sendAndWait(page, "Explain <img src=x onerror=alert(1)> photosynthesis [x](javascript:alert(1)) <script>alert(2)</script>");
  const title = (await heading(page).textContent()) ?? "";
  expect(title).not.toBe("New chat");
  expect(title).toMatch(/^[\p{L}\p{M}\p{N} '&-]+$/u);
  expect(title.split(" ").length).toBeLessThanOrEqual(6);
  await expect(page.locator("img[src='x']")).toHaveCount(0);
  expect(dialogs).toBe(0);
});

test("races: a double Enter sends once; a rename while the reply streams is kept; leaving mid-reply still names it", async ({
  page,
  account: _,
}) => {
  const other = await seedTurn(page, "Define osmosis");
  await page.goto("/");
  await waitForHydration(page);

  // Double Enter.
  await composer(page).fill("Explain gravity");
  await composer(page).press("Enter");
  await composer(page).press("Enter");
  await waitForIdle(page);
  await expect(page.getByRole("article", { name: "You" })).toHaveCount(1);

  // A rename while the first reply of a new chat is still streaming wins.
  await newChat(page);
  await send(page, `Explain photosynthesis ${"step by step with every detail ".repeat(12)}`);
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
  const id = new URL(page.url()).pathname.split("/").pop() as string;
  const list = page.locator("nav[aria-labelledby='chats-heading']");
  await list.locator("li", { has: page.locator(`a[href="/c/${id}"]`) }).getByRole("button", { name: /^Rename/ }).click();
  const dialog = page.getByRole("dialog", { name: "Rename chat" });
  await dialog.getByLabel("Name").fill("My biology notes");
  await dialog.getByRole("button", { name: "Rename" }).click();
  await waitForIdle(page);
  await expect(heading(page)).toHaveText("My biology notes");
  await page.reload();
  await expect(heading(page)).toHaveText("My biology notes");

  // Leaving for another chat before the reply ends: it still gets its name.
  await newChat(page);
  await send(page, `What is the water cycle ${"and how does it move water around ".repeat(8)}`);
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
  await page.locator(`a[href="/c/${other}"]`).click();
  await expect(heading(page)).toHaveText("Osmosis Definition");
  await expect(chatLinks(page).filter({ hasText: "Water Cycle Explained" })).toHaveCount(1, { timeout: 20_000 });
});
