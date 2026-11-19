import AxeBuilder from "@axe-core/playwright";
import { type Locator, type Page, type Request, type TestInfo, devices, expect } from "@playwright/test";

/**
 * What the learning specs read on screen, in one place: the copy and names of the approved plan (§3, §4)
 * for the test card, the renderers and their controls. When a lane words something differently, change
 * it here once (stage 4), never a spec's assertion.
 */
export const COPY = {
  /** The check button; after a wrong try (numeric, cli) the same button reads "Try again" (test-card.tsx). */
  check: /^(?:Check answer|Try again)$/,
  showAnswer: "Show answer",
  saving: /^Saving/,
  getFeedback: "Get feedback",
  /** "Question 4 of 5 · 7 of 9 points so far" ("0 of 1 point so far": review.tsx pluralises on the possible). */
  progress: (at: number, total: number, earned: number, possible: number) =>
    `Question ${at} of ${total} · ${earned} of ${possible} ${possible === 1 ? "point" : "points"} so far`,
  /** "You scored 7 of 12 points: 3 of 5 questions fully right, 1 partly." */
  score: /^You scored \d+ of \d+ points/,
  triesLeft: (left: number) => `Not quite: ${left} ${left === 1 ? "try" : "tries"} left.`,
  numericHint: "Digits, with an optional leading minus and a full stop for decimals (for example -12.5)",
  comma: "Use a full stop for decimals, not a comma.",
  /** A value the server refused (422), shown in the card. */
  rejected:
    /doesn't fit the question|can be picked only once|answer is too long|Use digits, with an optional leading minus|saved answers are too long/,
  networkFailed: "Your answer wasn't saved. Check your connection and try again.",
  // Exam
  submit: "Submit",
  submitDialog: /submit/i,
  working: "Working…",
  notSaved: (number: number) => `Question ${number}, not saved`,
  saveAgain: "Save again",
  clear: /^Clear/,
  previous: /^Previous/,
  next: /^Next/,
  changedElsewhere: /Changed in another tab/,
  /** The Submit dialog's count (test-card.tsx missingLine): "2 of 6 questions aren't fully answered; …". */
  unanswered: /(\d+) of \d+ questions (?:isn't|aren't) fully answered/,
  // Typed commands
  examNotice: "Answer in the mock exam card; nothing is graded until you submit.",
  inCardNotice: (number: number) => `Question ${number} is answered in its card.`,
  // v1 (unchanged copy)
  v1Check: "Check answer",
  v1Skip: "Skip",
  v1CheckBlanks: "Check answers",
  v1ShowBlanks: "Show answers",
  // Renderers
  showQuestion: "Show question",
  knowIt: "Know it",
  stillLearning: "Still learning",
  startOver: /^Start over/,
  reviewStillLearning: /still learning/i,
  collapseAll: "Collapse all",
  expandAll: "Expand all",
  copyMarkdown: "Copy as Markdown",
  downloadMarkdown: /^Download \.md/,
  print: /^Print/,
  copied: "Copied",
  jumpToLatest: "Jump to latest",
  play: /^Play/,
  retry: /^Retry/,
  openOnYouTube: /Open on YouTube/,
  jumpTo: /^Jump to/,
  privacyNote: /privacy-enhanced/i,
  devicesAndLinks: "Devices and links",
  write: "Write",
  preview: "Preview",
  why: "Why",
} as const;

/** The chat's practice endpoint, as the browser calls it through the BFF. */
export const RESPONSES = "**/api/v1/messages/*/assessment-responses";

/** The JSON a practice request carried. */
export type PracticeRequest = { position?: number; hash?: string; question?: number; action?: string; value?: unknown; revision?: number };

export function body(request: Request): PracticeRequest {
  return (request.postDataJSON() ?? {}) as PracticeRequest;
}

/** The question waiting in a test card (its fieldset, named by its legend "Q1. …"). */
export const question = (scope: Locator) => scope.getByRole("group", { name: /^Q\d+\./ }).first();

/** The first study card (CardFrame) in a reply: the test card, a deck, a sheet, a video. */
export const card = (reply: Locator) => reply.locator(".study-card").first();

/**
 * Every practice request the page sends (`POST …/assessment-responses`): what was sent, and a wait until
 * each one has been answered or has failed (a card's debounced save is sent ~1 s after typing stops).
 */
export function practiceTraffic(page: Page) {
  const sent: Request[] = [];
  const open = new Set<Request>();
  page.on("request", (request) => {
    if (request.method() !== "POST" || !new URL(request.url()).pathname.endsWith("/assessment-responses")) return;
    sent.push(request);
    open.add(request);
  });
  const close = (request: Request) => open.delete(request);
  page.on("requestfinished", close);
  page.on("requestfailed", close);
  return {
    sent,
    actions: () => sent.map((request) => body(request).action),
    /** Wait for at least `count` requests of `action`, then for none to be in flight. */
    settled: async (action?: string, count = 1) => {
      if (action) {
        await expect.poll(() => sent.filter((request) => body(request).action === action).length, { message: `${count} ${action}` }).toBeGreaterThanOrEqual(count);
      }
      await expect.poll(() => open.size, { message: "practice requests settled" }).toBe(0);
    },
  };
}

/** The order question's drag handles (aria-hidden, so no role query reaches them). */
export const DRAG_HANDLE = "[data-drag-handle]";

// ------------------------------------------------------------------ widths (plan §7: 1440/834/390/320)

export type Width = 1440 | 834 | 390 | 320;

/** `test.use(at(320))`: desktop Chrome at 1440 and 834; Pixel 7 (touch, mobile) at the phone widths. */
export function at(width: Width) {
  if (width >= 834) {
    return { viewport: { width, height: width === 1440 ? 900 : 1112 }, isMobile: false, hasTouch: false, deviceScaleFactor: 1 };
  }
  // defaultBrowserType forces a new worker; the project already says Chromium.
  const { defaultBrowserType: _, ...pixel } = devices["Pixel 7"];
  return { ...pixel, viewport: { width, height: 800 }, screen: { width, height: 800 } };
}

// ------------------------------------------------------------------ checks

/** No serious or critical WCAG 2.2 A/AA violation, on the page or inside `scope`. */
export async function expectAccessible22(page: Page, scope?: string): Promise<void> {
  await settle(page);
  let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]);
  if (scope) builder = builder.include(scope);
  const results = await builder.analyze();
  const serious = results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((violation) => `${violation.id}: ${violation.help} — ${violation.nodes.map((node) => node.target.join(" ")).join(" | ")}`);
  expect(serious, `axe (wcag22aa) on ${page.url()}`).toEqual([]);
}

/** Finite animations still running (a loading pulse that loops forever is not counted). */
export function runningAnimations(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    document
      .getAnimations()
      .filter((animation) => animation.playState === "running" && animation.effect?.getTiming().iterations !== Number.POSITIVE_INFINITY)
      .map((animation) => (animation as CSSAnimation).animationName || "transition"),
  );
}

/** Wait (up to 5 s) until nothing finite animates, so a measurement never catches a fade. */
export async function settle(page: Page): Promise<void> {
  await expect.poll(() => runningAnimations(page), { timeout: 5_000 }).toEqual([]);
}

/** Every visible control in `scope` is at least `min` px each way (WCAG 2.5.8, plan §4: 44 px). */
export async function expectTargets(scope: Locator, min = 44, selector = "button, [role=combobox], input:not([type=radio]):not([type=checkbox]), textarea, label:has(input)"): Promise<void> {
  const small = await scope.locator(selector).evaluateAll(
    (elements, least) =>
      elements
        .filter((element) => (element as HTMLElement).offsetParent !== null && !element.closest("[aria-hidden='true']"))
        .map((element) => ({ element, box: element.getBoundingClientRect() }))
        .filter(({ box }) => box.width > 0 && (box.width < least || box.height < least))
        .map(({ element, box }) => `${element.tagName.toLowerCase()} "${(element.getAttribute("aria-label") ?? element.textContent ?? "").trim().slice(0, 40)}" ${Math.round(box.width)}x${Math.round(box.height)}`),
    min,
  );
  expect(small, `targets under ${min} px`).toEqual([]);
}

/** No text in `scope` is cut off sideways (no ellipsis, no clipped overflow): long options wrap in full. */
export async function expectNothingClipped(scope: Locator): Promise<void> {
  const clipped = await scope.evaluate((root) =>
    [...root.querySelectorAll<HTMLElement>("label, li, p, span, button")]
      .filter((element) => {
        if (element.getBoundingClientRect().width <= 1) return false; // sr-only: read out, never shown
        const style = getComputedStyle(element);
        const hides = style.overflowX === "hidden" || style.overflowX === "clip" || style.textOverflow === "ellipsis";
        return hides && element.scrollWidth > element.clientWidth + 1;
      })
      .map((element) => (element.textContent ?? "").trim().slice(0, 60)),
  );
  expect(clipped, "text cut off sideways").toEqual([]);
}

/** Press Tab until `target` has focus (at most `max` presses): a keyboard-only path to a control. */
export async function tabTo(page: Page, target: Locator, max = 25, key = "Tab"): Promise<void> {
  for (let press = 0; press < max; press += 1) {
    if (await target.evaluate((element) => element === document.activeElement).catch(() => false)) return;
    await page.keyboard.press(key);
  }
  await expect(target, `reached by ${key} within ${max} presses`).toBeFocused();
}

/** The form state a question holds (values, checks, choices, order), to compare before and after a reload. */
export function formState(scope: Locator): Promise<string[]> {
  return scope.evaluate((root) =>
    [...root.querySelectorAll<HTMLElement>("input, textarea, [role=combobox], [role=option], [role=listitem], [aria-pressed]")].map(
      (element) => {
        if (element instanceof HTMLInputElement && (element.type === "radio" || element.type === "checkbox")) {
          return `${element.type}:${element.checked}`;
        }
        if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) return `text:${element.value}`;
        const role = element.getAttribute("role") ?? "pressed";
        const state = element.getAttribute("aria-selected") ?? element.getAttribute("aria-pressed") ?? "";
        return `${role}:${state}:${(element.textContent ?? "").trim().slice(0, 80)}`;
      },
    ),
  );
}

/** The transcript's scroll position (the chat's own scroller, not the page). */
export function transcriptScroll(page: Page): Promise<number> {
  return page
    .getByRole("region", { name: "Conversation" })
    .evaluate((section) => (section.parentElement as HTMLElement).scrollTop);
}

/** Open a chat from the sidebar (the phone drawer at narrow widths): client-side, so in-memory drafts survive. */
export async function switchChat(page: Page, conversationId: string): Promise<void> {
  const menu = page.getByRole("button", { name: "Open menu" });
  let scope: Page | Locator = page;
  if (await menu.isVisible()) {
    await menu.click();
    scope = page.getByRole("dialog", { name: "Navigation" });
  }
  await scope.locator(`a[href="/c/${conversationId}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/c/${conversationId}$`));
}

export async function shot(page: Page, testInfo: TestInfo, name: string, scope?: Locator): Promise<void> {
  const path = testInfo.outputPath(`${name}.png`);
  await (scope ? scope.screenshot({ path }) : page.screenshot({ path }));
}

/** The URLs of the app's scripts this page loaded (resource timing), for the chunk check. */
export function loadedScripts(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => entry.name)
      .filter((name) => name.startsWith(location.origin) && /\.js(?:\?|$)/.test(name)),
  );
}
