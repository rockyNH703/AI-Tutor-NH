import type { Locator } from "@playwright/test";

import { APP_URL } from "./env";
import { expectNoHorizontalOverflow, waitForHydration } from "../support";
import { DRIVERS } from "./drivers";
import { blockOf, expect, openFixture, test } from "./support";
import {
  COPY,
  RESPONSES,
  body,
  card,
  expectAccessible22,
  expectNothingClipped,
  formState,
  practiceTraffic,
  question,
  shot,
} from "./ui";

/**
 * Practice and mock-exam modes (plan §7, "Modes"): practice tries and Show answer; an exam navigated, saved,
 * changed and cleared, a save still in flight when Submit is pressed, a save refused with 429 until
 * "Save again", the unanswered count, Submit with normal motion and focus on the score, and the review at
 * 320 px with the longest choice and a 300-character command in full.
 */

test.describe("practice", () => {
  test("numeric: wrong tries, a replay changes nothing, the third try is final", async ({ page }, testInfo) => {
    const traffic = practiceTraffic(page);
    const { reply } = await openFixture(page, "kind-numeric");
    const field = question(reply).getByRole("textbox");
    const check = reply.getByRole("button", { name: COPY.check });

    await field.fill("-70");
    await check.click();
    await expect(card(reply)).toContainText(COPY.triesLeft(2));
    await traffic.settled("answer");
    const first = traffic.sent[0];
    if (!first) throw new Error("no try was sent");

    await test.step("the same try sent again is the same success: nothing changes", async () => {
      const replay = await page.request.post(first.url(), { data: body(first), headers: { Origin: APP_URL } });
      expect(replay.status()).toBe(200);
      const record = (await replay.json()) as { pending?: { tries: string[] } | null };
      expect(record.pending?.tries).toEqual(["-70"]);
    });

    await test.step("a reload keeps the tries (the server's pending), revealing nothing", async () => {
      await page.reload();
      await waitForHydration(page);
      await expect(card(reply)).toContainText(COPY.triesLeft(2));
      await expect(card(reply)).not.toContainText("-67");
    });

    await field.fill("-60");
    await check.click();
    await expect(card(reply)).toContainText(COPY.triesLeft(1));
    await field.fill("-75");
    await check.click();
    await expect(card(reply)).toContainText(COPY.progress(2, 2, 0, 1));
    await expect(card(reply)).toContainText("-67"); // revealed once final, with the tries
    await expect(card(reply)).toContainText(DRIVERS.numeric.explanation);
    await shot(page, testInfo, "numeric-final");
  });

  for (const kind of ["cli", "match"] as const) {
    test(`${kind}: Show answer is final with 0 points and reveals the key`, async ({ page }, testInfo) => {
      const { reply } = await openFixture(page, `kind-${kind}`);
      await reply.getByRole("button", { name: COPY.showAnswer }).click();
      await expect(card(reply)).toContainText(COPY.progress(2, 2, 0, DRIVERS[kind].points));
      const block = blockOf(`kind-${kind}`).questions[0];
      const revealed: string = kind === "cli" ? block.answers[0] : block.pairs[0][1];
      await expect(card(reply)).toContainText(revealed);
      await expectAccessible22(page);
      await shot(page, testInfo, `${kind}-shown`);
    });
  }
});

test.describe("mock exam", () => {
  const exam = blockOf("exam");
  // The review of a single choice shows the option chosen (B, 396 characters; every option is 394-397).
  const longest: string = exam.questions[0].options[1];
  const command: string = exam.questions[3].answers[0];
  const pill = (scope: Locator, number: number) => scope.getByRole("button", { name: new RegExp(`^Question ${number}\\b`) });

  test("navigate, save, change, clear, 429, a save in flight at Submit, submit, review at 320", async ({ page, pageGuard }, testInfo) => {
    test.slow();
    const traffic = practiceTraffic(page);
    const { reply } = await openFixture(page, "exam");
    const examCard = card(reply);
    await expect(examCard).toContainText(exam.questions[0].question.slice(0, 40));

    await test.step("pills and Previous/Next move between questions and keep focus", async () => {
      await pill(examCard, 3).click();
      await expect(question(reply)).toHaveAccessibleName(/^Q3\./);
      await expect(pill(examCard, 3)).toBeFocused();
      await examCard.getByRole("button", { name: COPY.next }).click();
      await expect(question(reply)).toHaveAccessibleName(/^Q4\./);
      await expect(examCard.getByRole("button", { name: COPY.next })).toBeFocused();
      await examCard.getByRole("button", { name: COPY.previous }).click();
      await expect(question(reply)).toHaveAccessibleName(/^Q3\./);
      await expect(examCard.getByRole("button", { name: COPY.previous })).toBeFocused();
      await pill(examCard, 1).click();
    });

    await test.step("save, change and clear a draft (any order, nothing graded)", async () => {
      const options = question(reply).getByRole("radio");
      await options.nth(1).check();
      await traffic.settled("save", 1);
      await options.nth(2).check();
      await traffic.settled("save", 2);
      await examCard.getByRole("button", { name: COPY.clear }).click();
      await traffic.settled("clear", 1);
      await expect(question(reply).getByRole("radio", { checked: true })).toHaveCount(0);
      await options.nth(1).check();
      await traffic.settled("save", 3);
      await expect(examCard).not.toContainText(exam.questions[0].explanation.slice(0, 30)); // no key before submit
    });

    await test.step("Regenerate over saved answers asks first", async () => {
      await reply.hover();
      await reply.getByRole("button", { name: /^Regenerate/ }).click();
      const dialog = page.getByRole("dialog", { name: "Regenerate and lose your saved answers?" });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused(); // destructive: Cancel first
      await dialog.getByRole("button", { name: "Cancel" }).click();
      await expect(dialog).toBeHidden();
    });

    await test.step("a save refused with 429 is retried twice, then marked, then saved again", async () => {
      pageGuard.allow(/status of 429/);
      await page.route(RESPONSES, (route) => route.fulfill({ status: 429, contentType: "application/json", body: JSON.stringify({ error: { code: "rate_limited", message: "Too many requests", requestId: "e2e" } }) }), { times: 3 });
      await pill(examCard, 2).click();
      await DRIVERS.multi.right(question(reply), page);
      await expect(pill(examCard, 2)).toHaveAccessibleName(COPY.notSaved(2), { timeout: 30_000 });
      expect(traffic.sent.filter((request) => body(request).action === "save" && body(request).question === 1).length, "the first try and two retries").toBeGreaterThanOrEqual(3);
      await expect(examCard.getByRole("button", { name: COPY.saveAgain })).toBeVisible();
      await examCard.getByRole("button", { name: COPY.saveAgain }).click();
      await expect(pill(examCard, 2)).not.toHaveAccessibleName(COPY.notSaved(2), { timeout: 15_000 });
      await shot(page, testInfo, "saved-again");
    });

    await test.step("the cli answer: a 300-character command", async () => {
      await pill(examCard, 4).click();
      await question(reply).getByRole("textbox").fill(command);
      await page.keyboard.press("Tab");
      await traffic.settled("save");
    });

    await test.step("a written answer (Q6), so the review can ask the tutor about it", async () => {
      await pill(examCard, 6).click();
      await DRIVERS.short.right(question(reply), page);
      await page.keyboard.press("Tab");
      await traffic.settled("save");
    });

    await test.step("a reload keeps every saved draft", async () => {
      await pill(examCard, 1).click();
      const before = await formState(question(reply));
      await page.reload();
      await waitForHydration(page);
      await expect.poll(() => formState(question(reply))).toEqual(before);
    });

    const order: string[] = [];
    await test.step("a save still in flight when Submit is pressed goes first", async () => {
      await page.route(RESPONSES, async (route) => {
        const sent = body(route.request());
        if (sent.action !== "save") return route.continue();
        order.push("save sent");
        await new Promise((resolve) => setTimeout(resolve, 3_000)); // a slow network, on purpose
        await route.continue();
        order.push("save answered");
      });
      page.on("request", (request) => {
        if (request.url().includes("/assessment-responses") && body(request).action === "submit") order.push("submit sent");
      });
      await pill(examCard, 3).click();
      await question(reply).getByRole("textbox").fill("-67");
      await page.keyboard.press("Tab");
      await examCard.getByRole("button", { name: COPY.submit, exact: true }).click();
      const dialog = page.getByRole("dialog", { name: COPY.submitDialog });
      await expect(dialog).toBeVisible();
      // Counted from the server's drafts once the queue is idle: Q5 (match) is unanswered.
      await expect(dialog).toContainText(COPY.unanswered, { timeout: 15_000 });
      expect(COPY.unanswered.exec((await dialog.textContent()) ?? "")?.[1]).toBe("1");
      await page.emulateMedia({ reducedMotion: "no-preference" });
      await dialog.getByRole("button", { name: COPY.submit, exact: true }).click();
      await expect(dialog.getByRole("button", { name: COPY.working })).toBeVisible();
      await expect(dialog).toBeHidden({ timeout: 30_000 });
      expect(order.indexOf("save answered"), "the pending save was answered before Submit was sent").toBeLessThan(order.indexOf("submit sent"));
      expect(traffic.sent.filter((request) => body(request).action === "submit").at(-1)?.postDataJSON()?.revision, "submit names a revision").toEqual(expect.any(Number));
      await page.unroute(RESPONSES);
    });

    await test.step("focus lands on the review score; the review shows every answer and Why", async () => {
      const score = examCard.getByText(COPY.score);
      await expect(score).toBeFocused();
      await expect(examCard).toContainText(command);
      await expect(examCard).toContainText(exam.questions[0].explanation.slice(0, 30));
      await expect(examCard.getByRole("button", { name: COPY.getFeedback }).first()).toBeVisible();
      await expectAccessible22(page);
      await shot(page, testInfo, "review-1440");
    });

    await test.step("the review at 320 px: the longest choice and the command in full", async () => {
      await page.setViewportSize({ width: 320, height: 800 });
      await expect(examCard).toContainText(longest);
      await expect(examCard).toContainText(command);
      await expectNothingClipped(examCard);
      await expectNoHorizontalOverflow(page);
      await shot(page, testInfo, "review-320");
    });

    await test.step("after submit, a save is refused (409)", async () => {
      const first = traffic.sent[0];
      if (!first) throw new Error("no request was sent");
      const refused = await page.request.post(first.url(), {
        data: { position: 0, hash: body(first).hash, question: 4, action: "save", value: [0, 1, 2, 3] },
        headers: { Origin: APP_URL },
      });
      expect(refused.status()).toBe(409);
    });
  });

  test("an empty exam submits as all skipped, and a second Submit changes nothing", async ({ page }) => {
    const traffic = practiceTraffic(page);
    const { reply } = await openFixture(page, "exam");
    await card(reply).getByRole("button", { name: COPY.submit, exact: true }).click();
    const dialog = page.getByRole("dialog", { name: COPY.submitDialog });
    await expect(dialog).toContainText(COPY.unanswered);
    expect(COPY.unanswered.exec((await dialog.textContent()) ?? "")?.[1]).toBe(String(exam.questions.length));
    await dialog.getByRole("button", { name: COPY.submit, exact: true }).click();
    await expect(card(reply).getByText(COPY.score)).toContainText(/^You scored 0 of/);
    await traffic.settled("submit");
    const submitted = traffic.sent.find((request) => body(request).action === "submit");
    if (!submitted) throw new Error("no submit was sent");
    const again = await page.request.post(submitted.url(), { data: body(submitted), headers: { Origin: APP_URL } });
    expect(again.status(), "a submit after submit is the same success").toBe(200);
  });
});
