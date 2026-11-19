import type { Locator } from "@playwright/test";

import { expectNoHorizontalOverflow, waitForHydration } from "../support";
import { expect, fixture, openFixture, test } from "./support";
import { COPY, at, card, expectAccessible22, expectTargets, shot } from "./ui";

/**
 * ```flashcards (plan §3 "Flashcards", §7 "Renderers"): every control and key, the aria-disabled ends, Know it /
 * Still learning to the summary and the review of the "still learning" cards, a 2,000-character back scrolled by
 * keyboard at 320 px with axe while it overflows, restore after a reload, and corrupt storage.
 */

type Deck = { title: string; cards: { front: string; back: string }[] };
const DECK = JSON.parse(/```flashcards\n(.*)\n```/.exec(fixture("flashcards").message)?.[1] ?? "{}") as Deck;
const TOTAL = DECK.cards.length;
const LONG = DECK.cards.findIndex((item) => item.back.length > 1500);

const face = (deck: Locator, number: number, side: "question" | "answer") =>
  deck.getByRole("region", { name: `Card ${number} of ${TOTAL}, ${side}` });
const counter = (deck: Locator, number: number) => deck.getByText(`${number} of ${TOTAL}`, { exact: true });

test("every control and key, the ends, the summary and the review", { tag: "@demo" }, async ({ page }, testInfo) => {
  const { reply } = await openFixture(page, "flashcards");
  const deck = card(reply);
  const first = DECK.cards[0];
  if (!first) throw new Error("empty deck");

  await test.step("the question side only, until turned", async () => {
    await expect(face(deck, 1, "question")).toContainText(first.front);
    await expect(deck.getByText(first.back)).toHaveCount(0);
    await expect(counter(deck, 1)).toBeVisible();
    await expectAccessible22(page);
  });

  await test.step("Show answer / Show question, by click, Enter and Space", async () => {
    await deck.getByRole("button", { name: COPY.showAnswer }).click();
    await expect(face(deck, 1, "answer")).toContainText(first.back);
    const back = deck.getByRole("button", { name: COPY.showQuestion });
    await back.focus();
    await page.keyboard.press("Enter");
    await expect(face(deck, 1, "question")).toBeVisible();
    await page.keyboard.press("Space");
    await expect(face(deck, 1, "answer")).toBeVisible();
    await page.keyboard.press("Space");
    await shot(page, testInfo, "turned");
  });

  await test.step("← and → move between cards; with a modifier they do nothing", async () => {
    await face(deck, 1, "question").focus();
    await page.keyboard.press("ArrowRight");
    await expect(counter(deck, 2)).toBeVisible();
    await page.keyboard.press("Alt+ArrowRight");
    await expect(counter(deck, 2)).toBeVisible();
    await page.keyboard.press("ArrowLeft");
    await expect(counter(deck, 1)).toBeVisible();
  });

  await test.step("Previous and Next are aria-disabled at the ends", async () => {
    const previous = deck.getByRole("button", { name: COPY.previous });
    const next = deck.getByRole("button", { name: COPY.next });
    await expect(previous).toHaveAttribute("aria-disabled", "true");
    await previous.click({ force: true });
    await expect(counter(deck, 1)).toBeVisible();
    for (let step = 1; step < TOTAL; step += 1) await next.click();
    await expect(counter(deck, TOTAL)).toBeVisible();
    await expect(next).toHaveAttribute("aria-disabled", "true");
    await next.click({ force: true });
    await expect(counter(deck, TOTAL)).toBeVisible();
    for (let step = 1; step < TOTAL; step += 1) await previous.click();
    await expect(counter(deck, 1)).toBeVisible();
  });

  await test.step("Know it / Still learning to the summary, which takes focus", async () => {
    const learning = new Set([0, LONG]);
    for (let index = 0; index < TOTAL; index += 1) {
      await expect(counter(deck, index + 1)).toBeVisible();
      await deck.getByRole("button", { name: learning.has(index) ? COPY.stillLearning : COPY.knowIt }).click();
    }
    await expect(deck.locator(":focus"), "the summary, inside the deck, has focus").toContainText(/know/i);
    await shot(page, testInfo, "summary");
  });

  await test.step("review the still-learning cards, then start over", async () => {
    await deck.getByRole("button", { name: COPY.reviewStillLearning }).click();
    await expect(deck.getByText("1 of 2", { exact: true })).toBeVisible();
    await expect(deck.getByRole("region", { name: /^Card 1 of 2, question$/ })).toContainText(first.front);
    // Start over is on the round's summary: finish the review round first.
    for (let step = 0; step < 2; step += 1) await deck.getByRole("button", { name: COPY.knowIt }).click();
    await deck.getByRole("button", { name: COPY.startOver }).click();
    await expect(counter(deck, 1)).toBeVisible();
  });
});

test("restores its place after a reload, and starts over on corrupt storage", async ({ page }) => {
  const { reply } = await openFixture(page, "flashcards");
  const deck = card(reply);
  for (let step = 1; step < 5; step += 1) await deck.getByRole("button", { name: COPY.next }).click();
  await expect(counter(deck, 5)).toBeVisible();
  await page.reload();
  await waitForHydration(page);
  await expect(counter(card(reply), 5)).toBeVisible();

  await page.evaluate(() => {
    for (let index = 0; index < sessionStorage.length; index += 1) {
      const key = sessionStorage.key(index);
      if (key) sessionStorage.setItem(key, "{not json");
    }
  });
  await page.reload();
  await waitForHydration(page);
  await expect(counter(card(reply), 1)).toBeVisible();
});

test.describe("320px", () => {
  test.use(at(320));
  test("a 2,000-character back scrolls by keyboard, accessible while it overflows", async ({ page }, testInfo) => {
    const { reply } = await openFixture(page, "flashcards");
    const deck = card(reply);
    for (let step = 0; step < LONG; step += 1) await deck.getByRole("button", { name: COPY.next }).click();
    await deck.getByRole("button", { name: COPY.showAnswer }).click();
    const back = face(deck, LONG + 1, "answer");
    await expect(back).toContainText(DECK.cards[LONG]?.back.slice(0, 60) ?? "");
    expect(await back.evaluate((element) => element.scrollHeight > element.clientHeight), "the back overflows its face").toBe(true);
    await expectAccessible22(page);
    await back.focus();
    await page.keyboard.press("PageDown");
    await page.keyboard.press("ArrowDown");
    expect(await back.evaluate((element) => element.scrollTop), "scrolled by keyboard").toBeGreaterThan(0);
    await expectNoHorizontalOverflow(page);
    await shot(page, testInfo, "long-back-320");
  });
});

test.describe("390px", () => {
  test.use(at(390));
  test("touch targets", async ({ page }) => {
    const { reply } = await openFixture(page, "flashcards");
    await expect(counter(card(reply), 1)).toBeVisible();
    await expectTargets(card(reply));
  });
});
