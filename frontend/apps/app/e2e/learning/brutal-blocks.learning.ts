import type { Locator, Page } from "@playwright/test";

import { waitForHydration } from "../support";
import { DRIVERS } from "./drivers";
import { blockOf, endSessions, expect, fixture, openFixture, test } from "./support";
import { COPY, DRAG_HANDLE, RESPONSES, body, card, practiceTraffic, question, runningAnimations, shot } from "./ui";

/**
 * The brutal list, part one (plan §7 "Brutal"): broken blocks, the 30/31 question limit, hostile input, drag
 * cancelled three ways, and the card under pressure (double and rapid submits, a slow API, a dropped
 * connection, an expired session, two tabs).
 */

for (const name of ["bad-json", "bad-duplicate", "bad-dangling", "bad-empty", "bad-oversized", "bad-unclosed", "quiz-31"]) {
  test(`${name}: refused with ${fixture(name).refusal}, its source shown with the reason`, async ({ page }, testInfo) => {
    const { reply } = await openFixture(page, name);
    await expect(reply.locator("pre").first()).toBeVisible();
    await expect(reply).toContainText(/couldn't be set up|can't be answered/);
    await expect(reply.getByRole("button", { name: COPY.check })).toHaveCount(0);
    await shot(page, testInfo, name);
  });
}

test("blocks that cannot be drawn show their source", async ({ page }) => {
  const { reply } = await openFixture(page, "bad-renderers");
  await expect(reply.locator("pre")).toHaveCount(3);
  await expect(reply.locator(".study-card")).toHaveCount(0);
});

test("30 questions render within budget; answering stays quick", async ({ page }) => {
  const { conversationId } = await openFixture(page, "quiz-30");
  const started = Date.now();
  await page.goto(`/c/${conversationId}`);
  await waitForHydration(page);
  const reply = page.getByRole("article", { name: "Assistant" }).last();
  await expect(card(reply)).toContainText("Question 1 of 30");
  expect(Date.now() - started, "a 30-question test is ready in under 5 s").toBeLessThan(5_000);
  await question(reply).getByRole("radio").first().check();
  const answered = Date.now();
  await reply.getByRole("button", { name: COPY.v1Check }).click();
  await expect(card(reply)).toContainText("Question 2 of 30");
  expect(Date.now() - answered, "an answer round trip under 2 s").toBeLessThan(2_000);
});

test("numeric: '-0' is a try; '1e3', '.5', '5.', non-ASCII digits and a comma are refused before sending", async ({ page }) => {
  const traffic = practiceTraffic(page);
  const { reply } = await openFixture(page, "kind-numeric");
  const field = question(reply).getByRole("textbox");
  for (const value of ["1e3", ".5", "5.", "١٢", "1,5"]) {
    await field.fill(value);
    await reply.getByRole("button", { name: COPY.check }).click();
    await expect(field, value).toHaveAttribute("aria-invalid", "true");
    await expect(card(reply)).toContainText(value === "1,5" ? COPY.comma : COPY.numericHint);
  }
  expect(traffic.sent, "nothing sent for a malformed number").toHaveLength(0);
  await field.fill("-0");
  await reply.getByRole("button", { name: COPY.check }).click();
  await expect(card(reply)).toContainText(COPY.triesLeft(2));
});

test("KaTeX errors draw as text and the question still works", async ({ page }) => {
  const { reply } = await openFixture(page, "katex-error");
  await expect(question(reply)).toBeVisible();
  await expect(card(reply).locator(".katex-error, .katex").first()).toBeVisible();
  await question(reply).getByRole("radio").first().check();
  await reply.getByRole("button", { name: COPY.check }).click();
  await expect(card(reply)).toContainText(COPY.progress(2, 2, 1, 1));
});

test("cli: unicode and an over-long line are wrong tries; tabs between words still match", async ({ page }) => {
  const { reply } = await openFixture(page, "kind-cli");
  const field = question(reply).getByRole("textbox");
  const check = reply.getByRole("button", { name: COPY.check });
  await field.fill("shöw ïp ínt brïef");
  await check.click();
  await expect(card(reply)).toContainText(COPY.triesLeft(2));
  await field.fill(`show ip interface brief ${"x".repeat(400)}`);
  expect((await field.inputValue()).length, "the field stops at 300 characters").toBeLessThanOrEqual(300);
  await check.click();
  await expect(card(reply)).toContainText(COPY.triesLeft(1));
  await field.fill("sh\tip\tint\tbr");
  await check.click();
  await expect(card(reply)).toContainText(COPY.progress(2, 2, 1, 1));
});

async function itemOrder(q: Locator): Promise<string[]> {
  return q.getByRole("listitem").allInnerTexts();
}

async function startDrag(page: Page, q: Locator): Promise<void> {
  const handle = q.locator(DRAG_HANDLE).first();
  const box = await handle.boundingBox();
  if (!box) throw new Error("no drag handle");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 + 30, { steps: 5 });
}

test("order: a drag dropped outside, cancelled by Escape or by pointercancel changes nothing", async ({ page }) => {
  const { reply } = await openFixture(page, "kind-order");
  const q = question(reply);
  await expect(q.getByRole("listitem").first()).toBeVisible(); // the order kind is its own chunk
  const shown = await itemOrder(q);

  await startDrag(page, q);
  await page.mouse.move(5, 5, { steps: 10 });
  await page.mouse.up();
  await expect.poll(() => itemOrder(q), { message: "dropped outside" }).toEqual(shown);

  await startDrag(page, q);
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect.poll(() => itemOrder(q), { message: "Escape" }).toEqual(shown);

  await startDrag(page, q);
  await page.evaluate(() => document.dispatchEvent(new PointerEvent("pointercancel", { bubbles: true })));
  await page.mouse.up();
  await expect.poll(() => itemOrder(q), { message: "pointercancel" }).toEqual(shown);
  await expect.poll(() => runningAnimations(page)).toEqual([]);
});

test("empty, double and rapid submits record exactly one answer each", async ({ page }) => {
  const traffic = practiceTraffic(page);
  const { reply } = await openFixture(page, "kind-single");
  const check = reply.getByRole("button", { name: COPY.check });
  await expect(check, "nothing chosen: nothing to check").toBeDisabled();
  await DRIVERS.single.right(question(reply), page);
  await check.dblclick();
  await expect(card(reply)).toContainText(COPY.progress(2, 2, 1, 1));
  await traffic.settled("answer");
  expect(traffic.actions().filter((action) => action === "answer")).toHaveLength(1);
  await question(reply).getByRole("radio").first().check();
  for (let click = 0; click < 5; click += 1) await check.click({ force: true, timeout: 2_000 }).catch(() => undefined);
  await expect(card(reply).getByText(COPY.score)).toBeVisible();
  await traffic.settled("answer", 2);
  expect(traffic.actions().filter((action) => action === "answer")).toHaveLength(2);
});

test("a slow API shows Saving… and then the result", async ({ page }) => {
  await page.route(RESPONSES, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 3_000)); // a slow network, on purpose
    await route.continue();
  });
  const { reply } = await openFixture(page, "kind-single");
  await DRIVERS.single.right(question(reply), page);
  await reply.getByRole("button", { name: COPY.check }).click();
  await expect(reply.getByRole("button", { name: COPY.saving })).toBeVisible();
  await expect(card(reply)).toContainText(COPY.progress(2, 2, 1, 1), { timeout: 15_000 });
});

test("a dropped connection is reported in the card, and checking again works", async ({ page, pageGuard }) => {
  pageGuard.allow(/ERR_FAILED|Failed to fetch/);
  const { reply } = await openFixture(page, "kind-single");
  await page.route(RESPONSES, (route) => route.abort("failed"), { times: 1 });
  await DRIVERS.single.right(question(reply), page);
  await reply.getByRole("button", { name: COPY.check }).click();
  await expect(card(reply)).toContainText(COPY.networkFailed);
  await expect(card(reply)).toContainText("Question 1 of 2");
  await reply.getByRole("button", { name: COPY.check }).click();
  await expect(card(reply)).toContainText(COPY.progress(2, 2, 1, 1));
});

test("an expired session leads to the sign-in page, never a silent failure", async ({ page, signedIn, pageGuard }) => {
  pageGuard.allow(/status of 401/);
  const { reply } = await openFixture(page, "kind-single");
  await DRIVERS.single.right(question(reply), page);
  await endSessions(signedIn.userId);
  await reply.getByRole("button", { name: COPY.check }).click();
  await expect(page).toHaveURL(/\/login\?reason=expired/, { timeout: 15_000 });
});

test("two tabs: a stale tab's save after a submit gets 409, reloads and says so", async ({ page, context, pageGuard }) => {
  pageGuard.allow(/status of 409/);
  const traffic = practiceTraffic(page);
  const { conversationId, reply } = await openFixture(page, "exam");
  const other = await context.newPage();
  await other.goto(`/c/${conversationId}`);
  await waitForHydration(other);
  const stale = other.getByRole("article", { name: "Assistant" }).last();
  await expect(question(stale)).toBeVisible();

  await question(reply).getByRole("radio").nth(1).check();
  await traffic.settled("save");
  await card(reply).getByRole("button", { name: COPY.submit, exact: true }).click();
  await page.getByRole("dialog", { name: COPY.submitDialog }).getByRole("button", { name: COPY.submit, exact: true }).click();
  await expect(card(reply).getByText(COPY.score)).toBeVisible();

  const refused = other.waitForResponse((response) => response.url().includes("/assessment-responses") && body(response.request()).action === "save");
  await question(stale).getByRole("radio").nth(2).check();
  expect((await refused).status()).toBe(409);
  await expect(card(stale)).toContainText(COPY.changedElsewhere);
  await expect(card(stale).getByText(COPY.score)).toBeVisible();
  const exam = blockOf("exam");
  await expect(card(stale)).toContainText(exam.questions[0].explanation.slice(0, 30));
  await other.close();
});
