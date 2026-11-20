import { at, expectAccessible22, expectTargets } from "../learning/ui";
import { expectNoHorizontalOverflow, waitForHydration } from "../support";
import { type Card, FIXTURE, chatWithCards, expect, lessonId, lessons, slot, stubNhprep, test } from "./support";

/**
 * G5: the NHprep lesson card in the chat, every view and state, against the pinned wire shapes (L4's fixture)
 * through browser routes; the chat itself is real (this worktree's API, fake LLM). The finder, imported lessons,
 * lost entitlement and the importer's removals are the API's half (pytest and L4's e2e).
 */

const [VIDEO_CARD, STUDY_CARD, AMBIGUOUS_CARD] = FIXTURE.cards as [Card, Card, Card];
const EVERYTHING: Card = { ...STUDY_CARD, views: ["video", "notes", "cheat_sheet", "flashcards", "interview"] };

test("a card draws its lesson, video cover, sections and asked-for study aids; nothing plays before Play", async ({ page }) => {
  const stub = await stubNhprep(page, lessons(6));
  const chat = await chatWithCards(page, [EVERYTHING]);
  await page.goto(`/c/${chat}`);
  await waitForHydration(page);
  const card = slot(page);

  await expect(card.getByText(FIXTURE.lesson.title).first()).toBeVisible();
  await expect(card.getByText(/Routing Basics \(Ajay\) · Lesson 1 of 6 · Trainer: Ajay · 30:00/)).toBeVisible();
  await expect(card.getByRole("button", { name: "Play from 0:45" })).toBeVisible(); // the matched section
  await expect(card.getByText("Jump to")).toBeVisible();
  await expect(card.getByText("Matches your question")).toBeVisible();
  await expect(card.getByText("Static routes point at a next hop.")).toBeVisible(); // notes
  await expect(card.getByText("This lesson has no cheat sheet on NHprep.")).toBeVisible(); // availability: absent
  await expect(card.getByText(/NHprep's interview questions for this lesson couldn't be brought over/)).toBeVisible();
  await expect(card.getByText(FIXTURE.items.flashcard?.items[0]?.title ?? "")).toBeVisible(); // the house flashcards
  await expect(card.getByText(/chapters?|students?/i)).toHaveCount(0);

  expect(stub.calls.filter((call) => call.startsWith("POST"))).toEqual([]);
  expect(stub.media).toEqual([]);
  await expect(card.locator("video")).toHaveCount(0);
  await expectAccessible22(page, "[data-nhprep-slot]");
});

test("Expand opens the notes in a dialog (axe inside it); Escape closes it and gives focus back", async ({ page }) => {
  await stubNhprep(page, lessons(3));
  const chat = await chatWithCards(page, [{ ...STUDY_CARD, views: ["notes"] }]);
  await page.goto(`/c/${chat}`);
  await waitForHydration(page);
  const expand = slot(page).getByRole("button", { name: "Expand notes" });
  await expand.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Static routes point at a next hop.")).toBeVisible();
  await expectAccessible22(page, "[role=dialog]");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(expand).toBeFocused();
});

test("Previous and Next keep focus while the lesson changes; picking another match redraws its study aids", async ({ page }) => {
  const stub = await stubNhprep(page, lessons(4));
  const chat = await chatWithCards(page, [{ ...STUDY_CARD, views: ["video", "notes"] }]);
  await page.goto(`/c/${chat}`);
  await waitForHydration(page);
  const card = slot(page);
  const next = card.getByRole("button", { name: "Next lesson" });
  await expect(card.getByRole("button", { name: "Previous lesson" })).toHaveAttribute("aria-disabled", "true"); // lesson 1
  await next.focus();
  await page.keyboard.press("Enter");
  await expect(card.getByText(/Lesson 2 of 4/).first()).toBeVisible();
  await expect(next).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(card.getByText(/Lesson 3 of 4/).first()).toBeVisible();
  await expect(next).toBeFocused();
  expect(stub.calls).toContain(`GET /lessons/${lessonId(3)}`);

  // Back to lesson 1 is cached: no second detail read.
  const reads = stub.calls.filter((call) => call === `GET /lessons/${lessonId(1)}`).length;
  await card.getByRole("button", { name: "Previous lesson" }).click();
  await card.getByRole("button", { name: "Previous lesson" }).click();
  await expect(card.getByText(/Lesson 1 of 4/).first()).toBeVisible();
  expect(stub.calls.filter((call) => call === `GET /lessons/${lessonId(1)}`).length).toBe(reads);

  // The other match ("Also taught by" when the title is the same) redraws the card and its notes.
  const others = card.getByRole("list", { name: "Other matching videos" });
  await others.getByRole("button").first().click();
  await expect(card.getByText(/Lesson 2 of 4/).first()).toBeVisible();
  await expect(card.getByText("NHprep notes").first()).toBeVisible();
  expect(stub.calls).toContain(`GET /lessons/${lessonId(2)}/items?kind=note`);
});

test("ambiguous: the same card with the neutral lead line, the other matches, and a tie across trainers", async ({ page }) => {
  const map = lessons(6);
  const tie = map.get(lessonId(2));
  if (tie) map.set(lessonId(2), { ...tie, title: FIXTURE.lesson.title, trainers: ["Ravi"] });
  await stubNhprep(page, map);
  const chat = await chatWithCards(page, [AMBIGUOUS_CARD]);
  await page.goto(`/c/${chat}`);
  await waitForHydration(page);
  const card = slot(page);
  await expect(card.getByText(/^Several NHprep lessons match your question/)).toBeVisible();
  await expect(card.getByRole("list", { name: "Other matching videos" }).getByRole("listitem")).toHaveCount(5);
  await expect(card.getByText(/^Also taught by Ravi · /)).toBeVisible();
  await expectAccessible22(page, "[data-nhprep-slot]");
});

test("states: not available (404), rate limited then Try again, removed, video not configured, unknown version", async ({ page }) => {
  const map = lessons(6);
  const three = map.get(lessonId(3));
  const four = map.get(lessonId(4));
  if (three) map.set(lessonId(3), { ...three, removed: true });
  if (four) map.set(lessonId(4), { ...four, video: "not_configured" });
  const stub = await stubNhprep(page, map);
  stub.detailFailures.set(lessonId(2), [{ status: 429, code: "rate_limited" }]);
  const one = (id: string, views: Card["views"] = ["video"]): Card => ({ ...VIDEO_CARD, primaryLessonId: id, views });
  const chat = await chatWithCards(page, [
    one("0b8f6a52-3c1e-4d7a-9f10-2a6c5e4b7dff"), // not in the bank: 404
    one(lessonId(2)),
    one(lessonId(3)),
    one(lessonId(4), ["video", "chapters"]),
    { ...one(lessonId(1)), version: 2 },
  ]);
  await page.goto(`/c/${chat}`);
  await waitForHydration(page);

  await expect(page.getByText("This NHprep card needs a newer version of the app. Reload the page to see it.")).toBeVisible();
  const cards = page.locator("[data-nhprep-slot]");
  await expect(cards).toHaveCount(4); // the unknown version draws no slot
  for (let index = 0; index < 4; index++) await cards.nth(index).scrollIntoViewIfNeeded();

  await expect(cards.nth(0).getByRole("status").filter({ hasText: "isn't available to your account" })).toBeVisible();
  await expect(cards.nth(0).getByRole("button", { name: "Try again" })).toHaveCount(0);

  await expect(cards.nth(1).getByRole("status").filter({ hasText: "You've reached the limit for now" })).toBeVisible();
  await cards.nth(1).getByRole("button", { name: "Try again" }).click();
  await expect(cards.nth(1).getByRole("button", { name: /^Play/ })).toBeVisible();

  await expect(cards.nth(2).getByText("This lesson was removed from NHprep.")).toBeVisible();
  await expect(cards.nth(2).getByRole("button", { name: /^Play/ })).toHaveCount(0);

  await expect(cards.nth(3).getByText("Video isn't available in NetSec AI yet.")).toBeVisible();
  await expect(cards.nth(3).getByText("In this video")).toBeVisible();
  await expect(cards.nth(3).getByRole("button", { name: /^Play/ })).toHaveCount(0);
  expect(stub.calls.filter((call) => call.startsWith("POST"))).toEqual([]);
  await expectAccessible22(page, "[data-nhprep-slot]");
});

test("more than 20 cards: the latest at once, the rest as they come into view, each lesson read once", async ({ page }) => {
  test.setTimeout(600_000);
  const map = lessons(24);
  const stub = await stubNhprep(page, map);
  const cards = Array.from({ length: 22 }, (_, index): Card => ({ ...VIDEO_CARD, primaryLessonId: lessonId(index + 1) }));
  const chat = await chatWithCards(page, cards);
  await page.goto(`/c/${chat}`);
  await waitForHydration(page);
  await expect(slot(page).getByRole("button", { name: /^Play/ })).toBeVisible(); // the latest card
  const firstLook = new Set(stub.calls.filter((call) => /^GET \/lessons\/[0-9a-f-]{36}$/.test(call)));
  expect(firstLook.size, "only the cards near the viewport were read").toBeLessThan(cards.length);

  // Scroll through the whole chat and back: every card reads its lesson once, and no more.
  for (let index = 0; index < cards.length; index++) await slot(page, index).scrollIntoViewIfNeeded();
  for (let index = cards.length - 1; index >= 0; index--) await slot(page, index).scrollIntoViewIfNeeded();
  await expect(page.locator("[data-nhprep-slot] button", { hasText: /^Play/ })).toHaveCount(cards.length);
  const details = stub.calls.filter((call) => /^GET \/lessons\/[0-9a-f-]{36}$/.test(call));
  expect(details.length).toBe(cards.length);
  expect(new Set(details).size).toBe(cards.length);
});

test("phone width: no sideways scroll; targets 44 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await stubNhprep(page, lessons(6));
  const chat = await chatWithCards(page, [EVERYTHING]);
  await page.goto(`/c/${chat}`);
  await waitForHydration(page);
  await expect(slot(page).getByRole("button", { name: /^Play/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectTargets(slot(page), 44, "button, summary");
  await expectAccessible22(page, "[data-nhprep-slot]");
});
