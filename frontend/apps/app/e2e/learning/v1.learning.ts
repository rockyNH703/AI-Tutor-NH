import type { Page } from "@playwright/test";

import { send, waitForHydration } from "../support";
import { expect, openFixture, test } from "./support";
import { COPY, card, loadedScripts, practiceTraffic, question, shot } from "./ui";

/**
 * Today's blocks keep working exactly as before (plan §7, "Modes and today's blocks"): ```mcq and
 * ```fill-blank answered, skipped and reloaded; the typed path ('B', 'option c', 'skip', 'next') and its
 * notices on an exam and on a question that is not single choice; and the chunk check: a plain quiz never
 * downloads React Flow or dnd-kit, a diagram or ordering question does.
 */

const userMessages = (page: Page) => page.getByRole("article", { name: "You" });

test.describe("v1 mcq", () => {
  test("Check, then the same after a reload", { tag: "@demo" }, async ({ page }, testInfo) => {
    const { reply } = await openFixture(page, "v1-mcq");
    await card(reply).getByRole("radio", { name: /443/ }).check();
    await card(reply).getByRole("button", { name: COPY.v1Check }).click();
    await expect(card(reply)).toContainText("Correct! You chose B.");
    await page.reload();
    await waitForHydration(page);
    await expect(card(reply)).toContainText("Correct! You chose B.");
    await shot(page, testInfo, "mcq-checked");
  });

  test("Skip, then the same after a reload", async ({ page }) => {
    const { reply } = await openFixture(page, "v1-mcq");
    await card(reply).getByRole("button", { name: COPY.v1Skip }).click();
    await expect(card(reply)).toContainText("Skipped. The answer is B.");
    await page.reload();
    await waitForHydration(page);
    await expect(card(reply)).toContainText("Skipped. The answer is B.");
  });
});

test.describe("v1 fill-blank", () => {
  test("Check answers, then the same after a reload", async ({ page }, testInfo) => {
    const { reply } = await openFixture(page, "v1-fill-blank");
    await card(reply).getByRole("textbox", { name: "Blank 1" }).fill("254");
    await card(reply).getByRole("textbox", { name: "Blank 2" }).fill("255.255.255.0");
    await card(reply).getByRole("button", { name: COPY.v1CheckBlanks }).click();
    await expect(card(reply)).toContainText("2 of 2 right.");
    await page.reload();
    await waitForHydration(page);
    await expect(card(reply)).toContainText("2 of 2 right.");
    await shot(page, testInfo, "fill-blank-checked");
  });

  test("Show answers, then the same after a reload", async ({ page }) => {
    const { reply } = await openFixture(page, "v1-fill-blank");
    await card(reply).getByRole("button", { name: COPY.v1ShowBlanks }).click();
    await expect(card(reply)).toContainText("Answers shown.");
    await page.reload();
    await waitForHydration(page);
    await expect(card(reply)).toContainText("Answers shown.");
  });
});

test.describe("typed answers", () => {
  test("'B', 'option c', 'skip' and 'next' reach the latest unfinished test, never the tutor", async ({ page }, testInfo) => {
    const { reply } = await openFixture(page, "v1-typed");
    const sentBefore = await userMessages(page).count();
    await send(page, "B");
    await expect(card(reply)).toContainText("Correct! You chose B.");
    await send(page, "option c");
    await expect(card(reply)).toContainText("Not quite: you chose C, the answer is A.");
    await send(page, "skip");
    await expect(card(reply)).toContainText("Skipped. The answer is C.");
    await send(page, "next");
    await expect(card(reply)).toContainText("Question 4 of 4 is waiting: pick an option, or type skip.");
    expect(await userMessages(page).count(), "no command became a message").toBe(sentBefore);
    await shot(page, testInfo, "typed");
  });

  test("on a mock exam: a notice, and nothing is sent", async ({ page }) => {
    const traffic = practiceTraffic(page);
    const { reply } = await openFixture(page, "exam");
    const sentBefore = await userMessages(page).count();
    await send(page, "B");
    await expect(card(reply)).toContainText(COPY.examNotice);
    expect(traffic.sent, "no answer sent").toHaveLength(0);
    expect(await userMessages(page).count()).toBe(sentBefore);
    await expect(question(reply).getByRole("radio", { checked: true })).toHaveCount(0);
  });

  test("on a question that is not single choice: a notice, and nothing is sent", async ({ page }) => {
    const traffic = practiceTraffic(page);
    const { reply } = await openFixture(page, "kind-multi");
    await send(page, "B");
    await expect(card(reply)).toContainText(COPY.inCardNotice(1));
    expect(traffic.sent).toHaveLength(0);
    await send(page, "skip"); // 'skip' is Show answer for any practice kind
    await expect(card(reply)).toContainText("Question 2 of 2");
  });
});

test.describe("chunk check", () => {
  const FLOW = "react-flow__";
  const DRAG = /dnd-kit-(?:description|announcement)/;

  async function markers(page: Page): Promise<{ flow: boolean; drag: boolean }> {
    let flow = false;
    let drag = false;
    for (const url of await loadedScripts(page)) {
      const text = await (await page.request.get(url)).text();
      flow ||= text.includes(FLOW);
      drag ||= DRAG.test(text);
    }
    return { flow, drag };
  }

  test("a v1 quiz loads neither React Flow nor dnd-kit; ordering and diagram questions do", async ({ page }) => {
    const quiz = await openFixture(page, "v1-quiz");
    await expect(card(quiz.reply)).toContainText("Question 1 of 2");
    expect(await markers(page), "a plain quiz").toEqual({ flow: false, drag: false });

    const ordering = await openFixture(page, "kind-order");
    await expect(question(ordering.reply).getByRole("listitem").first()).toBeVisible();
    expect((await markers(page)).drag, "an ordering question loads dnd-kit").toBe(true);

    const diagram = await openFixture(page, "kind-diagram");
    await expect(card(diagram.reply).locator(".react-flow__node").first()).toBeVisible();
    expect((await markers(page)).flow, "a diagram question loads React Flow").toBe(true);
  });
});
