import type { Page } from "@playwright/test";

import { LONG_MESSAGE, describeFocus, replies, send, waitForIdle } from "../support";
import { DRIVERS } from "./drivers";
import { expect, fixture, openFixture, sendTurn, test } from "./support";
import { COPY, DRAG_HANDLE, card, expectAccessible22, question, runningAnimations, shot, switchChat } from "./ui";

/**
 * The brutal list, part two (plan §7 "Brutal"): mixed renderers streamed in a long chat arrive in source
 * order; a token never touches earlier cards; the heap stays flat across chat switches; reduced motion (the
 * OS or the app's setting) leaves nothing running after a flip or a drag; the Tab walk never reaches a drag
 * handle; focus and selection seen in both themes; axe (wcag22aa) in both themes.
 */

test("mixed renderers stream into a long chat in source order", async ({ page }, testInfo) => {
  const { conversationId } = await openFixture(page, "v1-quiz");
  await sendTurn(page, LONG_MESSAGE, conversationId);
  await page.reload();
  // A block's slot is its card, or, while the reply streams, the "Preparing <label>…" placeholder in its place.
  // The widgets themselves mount when the stream ends and appear as their chunks load (a cached one first), so
  // the stream's order is read from the slots and the final order from the DOM.
  type Seen = { kind: string; at: number; reply: number };
  await page.evaluate(() => {
    const seen: Seen[] = [];
    (window as unknown as { __slots: Seen[] }).__slots = seen;
    const assistant = "article[aria-label='Assistant']";
    const placeholders: Record<string, string> = {
      flashcards: "flashcards",
      "network diagram": "topology",
      video: "video",
      question: "mcq",
    };
    const kindOf = (element: HTMLElement) =>
      element.getAttribute("data-card") ??
      (element.getAttribute("role") === "status"
        ? placeholders[/^Preparing (.+)…$/.exec(element.textContent ?? "")?.[1] ?? ""]
        : undefined);
    new MutationObserver((records) => {
      for (const record of records) {
        for (const added of record.addedNodes) {
          if (!(added instanceof HTMLElement)) continue;
          for (const element of [added, ...added.querySelectorAll<HTMLElement>("[data-card], [role='status']")]) {
            const kind = kindOf(element);
            const reply = [...document.querySelectorAll(assistant)].indexOf(element.closest(assistant) as Element);
            if (kind) seen.push({ kind, at: performance.now(), reply });
          }
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  });
  await send(page, fixture("mixed").message);
  await waitForIdle(page, 60_000);
  const all = await page.evaluate(() => (window as unknown as { __slots: Seen[] }).__slots);
  const latest = Math.max(...all.map((item) => item.reply));
  const seen = all.filter((item) => item.reply === latest);
  const order = ["note", "flashcards", "topology", "video", "mcq"];
  const firsts = order.map((kind) => seen.find((item) => item.kind === kind));
  expect(firsts.map((item) => item?.kind), "every block has its place while streaming").toEqual(order);
  const times = firsts.map((item) => item?.at ?? 0);
  expect(times, "the stream places them in source order").toEqual([...times].sort((a, b) => a - b));
  expect(times[0], "the first card is drawn while the reply is still streaming").toBeLessThan(times.at(-1) ?? 0);
  const drawn = await page
    .locator("article[aria-label='Assistant']")
    .last()
    .locator("[data-card]")
    .evaluateAll((cards) => cards.map((card) => card.getAttribute("data-card")));
  expect(drawn, "every renderer drawn, in source order").toEqual(order);
  await shot(page, testInfo, "mixed");
});

test("a streaming token never changes an earlier card", async ({ page }) => {
  const { conversationId } = await openFixture(page, "flashcards");
  const { reply } = await openFixture(page, "v1-quiz", { conversationId });
  await card(reply).getByRole("radio", { name: /Network/ }).check();
  await card(reply).getByRole("button", { name: COPY.v1Check }).click();
  await expect(card(reply)).toContainText("Correct! You chose B.");
  await page.evaluate(() => {
    const target = window as unknown as { __mutations: number };
    target.__mutations = 0;
    const observer = new MutationObserver((records) => {
      target.__mutations += records.length;
    });
    for (const element of document.querySelectorAll(".study-card")) {
      observer.observe(element, { childList: true, subtree: true, attributes: true, characterData: true });
    }
  });
  await send(page, LONG_MESSAGE);
  await waitForIdle(page, 60_000);
  expect(await page.evaluate(() => (window as unknown as { __mutations: number }).__mutations), "mutations inside earlier cards").toBe(0);
});

async function heapUsed(page: Page): Promise<number> {
  const session = await page.context().newCDPSession(page);
  await session.send("HeapProfiler.collectGarbage");
  await session.send("Performance.enable");
  const { metrics } = await session.send("Performance.getMetrics");
  await session.detach();
  return metrics.find((metric) => metric.name === "JSHeapUsedSize")?.value ?? 0;
}

test("the heap stays flat across chat switches", async ({ page }) => {
  test.slow();
  const chats: string[] = [];
  for (const name of ["kind-diagram", "flashcards", "cheatsheet"]) chats.push((await openFixture(page, name)).conversationId);
  for (const id of chats) await switchChat(page, id); // warm every chunk first
  const start = await heapUsed(page);
  for (let round = 0; round < 5; round += 1) {
    for (const id of chats) {
      await switchChat(page, id);
      await expect(card(replies(page).last())).toBeVisible();
    }
  }
  const growth = (await heapUsed(page)) - start;
  expect(growth / 1024 / 1024, "MB of heap kept after 15 switches").toBeLessThan(20);
});

for (const source of ["os", "settings"] as const) {
  test(`reduced motion (${source === "os" ? "the OS" : "the app's setting"}): a flip and a drag leave nothing running`, async ({ page }) => {
    if (source === "os") await page.emulateMedia({ reducedMotion: "reduce" });
    const motion = async () => {
      if (source === "settings") await page.evaluate(() => document.documentElement.setAttribute("data-motion", "reduce"));
    };
    const deck = await openFixture(page, "flashcards");
    await motion();
    await card(deck.reply).getByRole("button", { name: COPY.showAnswer }).click();
    expect(await runningAnimations(page), "after a flip").toEqual([]);

    const ordering = await openFixture(page, "kind-order");
    await motion();
    const handle = question(ordering.reply).locator(DRAG_HANDLE).nth(0);
    const target = question(ordering.reply).getByRole("listitem").nth(2);
    await handle.dragTo(target);
    expect(await runningAnimations(page), "after a drop").toEqual([]);
  });
}

test("the Tab walk never reaches a drag handle", async ({ page }) => {
  const { reply } = await openFixture(page, "kind-order");
  await expect(question(reply).getByRole("listitem").first()).toBeVisible();
  await page.locator("body").focus();
  const reached: string[] = [];
  for (let press = 0; press < 120; press += 1) {
    await page.keyboard.press("Tab");
    const found = await page.evaluate((handle) => {
      const element = document.activeElement as HTMLElement | null;
      if (!element) return null;
      const draggable =
        element.matches(handle) ||
        element.getAttribute("draggable") === "true" ||
        /draggable/i.test(element.getAttribute("aria-roledescription") ?? "") ||
        element.closest("[aria-hidden='true']") !== null;
      return draggable ? element.outerHTML.slice(0, 120) : null;
    }, DRAG_HANDLE);
    if (found) reached.push(found);
  }
  expect(reached).toEqual([]);
});

for (const theme of ["light", "dark"] as const) {
  test(`focus and selection are visible in the ${theme} theme; axe passes`, async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: theme });
    const single = await openFixture(page, "kind-single");
    await DRIVERS.single.right(question(single.reply), page);
    // The ring is :focus-visible (keyboard) only: after the click, come back to the option by keyboard.
    const chosen = question(single.reply).getByRole("radio", { checked: true });
    await chosen.focus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await expect(chosen).toBeFocused();
    expect((await describeFocus(page)).indicated, "a focus ring on the chosen option").toBe(true);
    await shot(page, testInfo, `${theme}-single`, card(single.reply));
    await expectAccessible22(page);

    const lines = await openFixture(page, "kind-lines");
    await DRIVERS.lines.right(question(lines.reply), page);
    const line = question(lines.reply).getByRole("option", { selected: true }).first();
    await line.focus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab"); // the listbox's roving tab stop is the line last focused
    await expect(line).toBeFocused();
    expect((await describeFocus(page)).indicated, "a focus ring on a selected line").toBe(true);
    await shot(page, testInfo, `${theme}-lines`, card(lines.reply));

    const diagram = await openFixture(page, "kind-diagram");
    await DRIVERS.diagram.right(question(diagram.reply), page);
    await shot(page, testInfo, `${theme}-diagram`, card(diagram.reply));

    const mixed = await openFixture(page, "mixed");
    await expect(card(mixed.reply)).toBeVisible();
    await expectAccessible22(page);
    await shot(page, testInfo, `${theme}-mixed`);
  });
}
