import type { Locator, Page, TestInfo } from "@playwright/test";

import { composer, expectNoHorizontalOverflow, lastReply, waitForHydration, waitForIdle } from "../support";
import { DRIVERS, type Driver, KINDS } from "./drivers";
import { type PageGuard, blockOf, expect, openFixture, sendTurn, test } from "./support";
import {
  COPY,
  RESPONSES,
  type Width,
  at,
  body,
  card,
  expectAccessible22,
  expectNothingClipped,
  expectTargets,
  formState,
  practiceTraffic,
  question,
  shot,
  switchChat,
  tabTo,
} from "./ui";

/**
 * The 12 question types (plan §7, "Each of the 12 types"), each in a practice card: full flows at
 * 1440/390/320 (render, client and server validation with the 422 in the card, change, check, feedback,
 * explanation, Get feedback), keyboard only with the focus table at the same widths, touch targets at 390,
 * render without overflow at 834, and persistence (a practice draft across a chat switch, an exam draft
 * across a reload). The second question of every kind fixture is a plain single choice (ARP).
 */

const FOLLOW = "ARP";

async function checkIt(page: Page, reply: Locator, driver: Driver, keyboard: boolean): Promise<void> {
  if (driver.submit === "button" || !keyboard) {
    const check = reply.getByRole("button", { name: COPY.check });
    if (keyboard) {
      await tabTo(page, check, 40);
      await page.keyboard.press("Enter");
    } else {
      await check.click();
    }
    return;
  }
  await page.keyboard.press(driver.submit);
}

async function fullFlow(page: Page, pageGuard: PageGuard, driver: Driver, testInfo: TestInfo): Promise<void> {
  const { reply } = await openFixture(page, driver.fixture);
  const q = question(reply);

  await test.step("render", async () => {
    await expect(q).toBeVisible();
    await expect(card(reply)).toContainText("Question 1 of 2");
    if (driver.kind === "scenario") {
      const scenario = blockOf(driver.fixture).scenario;
      await expect(card(reply)).toContainText(scenario.summary);
      for (const symptom of scenario.symptoms) await expect(card(reply)).toContainText(symptom);
      await expect(card(reply).getByRole("region", { name: /Branch router/ })).toBeVisible();
    }
    await expectNoHorizontalOverflow(page);
    await expectAccessible22(page);
    await shot(page, testInfo, "1-render");
  });

  if (driver.client) {
    const client = driver.client;
    await test.step("client validation: refused before anything is sent", async () => {
      const traffic = practiceTraffic(page);
      const field = q.getByRole("textbox");
      await field.fill(client.value);
      await checkIt(page, reply, driver, false);
      await expect(card(reply).getByText(client.message)).toBeVisible();
      await expect(field).toHaveAttribute("aria-invalid", "true");
      expect(traffic.sent, "nothing sent").toHaveLength(0);
    });
  }

  await test.step("server validation: a 422 shows in the card and records nothing", async () => {
    pageGuard.allow(/status of 422/);
    await driver.right(q, page);
    await page.route(
      RESPONSES,
      (route) => route.continue({ postData: JSON.stringify({ ...body(route.request()), value: driver.invalid }) }),
      { times: 1 },
    );
    const refused = page.waitForResponse((response) => response.url().includes("/assessment-responses"));
    await checkIt(page, reply, driver, false);
    expect((await refused).status(), "the server refuses the rewritten value").toBe(422);
    // The field's error first; the card's status line (sr-only) says it too.
    await expect(card(reply).getByText(COPY.rejected).first()).toBeVisible();
    await expect(card(reply)).toContainText("Question 1 of 2");
    await shot(page, testInfo, "2-refused");
  });

  await test.step("change the answer, then check it", async () => {
    await driver.wrong(q, page);
    await driver.right(q, page);
    if (driver.kind === "long") {
      const preview = q.getByRole("button", { name: COPY.preview });
      await preview.click();
      await expect(preview).toHaveAttribute("aria-pressed", "true");
      await q.getByRole("button", { name: COPY.write }).click();
      await expect(q.getByRole("textbox")).toBeVisible();
    }
    await checkIt(page, reply, driver, false);
    await expect(card(reply)).toContainText(COPY.progress(2, 2, driver.points, driver.points));
  });

  await test.step("feedback and explanation", async () => {
    await expect(card(reply)).toContainText(driver.explanation);
    await expectAccessible22(page);
    await shot(page, testInfo, "3-feedback");
  });

  await test.step("the last question, then the score", async () => {
    await question(reply).getByRole("radio", { name: new RegExp(FOLLOW) }).check();
    await checkIt(page, reply, DRIVERS.single, false);
    await expect(card(reply).getByText(COPY.score)).toContainText(`You scored ${driver.points + 1} of ${driver.points + 1} points`);
  });

  // Get feedback is for written answers (owner decision 3; review.tsx canAskFeedback): no other kind offers it.
  if (driver.kind !== "short" && driver.kind !== "long") {
    await expect(card(reply).getByRole("button", { name: COPY.getFeedback })).toHaveCount(0);
    return;
  }
  await test.step("Get feedback asks in the same chat and keeps the typed draft", async () => {
    await composer(page).fill("a draft I am still typing");
    await card(reply).getByRole("button", { name: COPY.getFeedback }).first().click();
    await expect(lastReply(page)).toContainText("Review my answer to Q1", { timeout: 30_000 });
    await waitForIdle(page);
    await expect(composer(page)).toHaveValue("a draft I am still typing");
    await shot(page, testInfo, "4-get-feedback");
  });
}

async function keyboardOnly(page: Page, driver: Driver, testInfo: TestInfo): Promise<void> {
  const { reply } = await openFixture(page, driver.fixture);
  const q = question(reply);
  await q.focus(); // where the card puts focus for a new question (tabIndex -1); keys only from here

  if (driver.tries > 1) {
    await test.step("a wrong try keeps focus in the field, its text selected", async () => {
      const field = q.getByRole("textbox");
      await tabTo(page, field);
      await page.keyboard.type(driver.kind === "numeric" ? "-70" : "show ip route");
      await page.keyboard.press("Enter");
      await expect(card(reply)).toContainText(COPY.triesLeft(driver.tries - 1));
      await expect(field).toBeFocused();
      const selection = await field.evaluate((input: HTMLInputElement) => [input.selectionStart, input.selectionEnd, input.value.length]);
      expect(selection, "the whole wrong value is selected").toEqual([0, selection[2], selection[2]]);
      await page.keyboard.type(driver.kind === "numeric" ? "-67" : "sh ip int br"); // replaces the selection
      await page.keyboard.press("Enter");
    });
  } else {
    await test.step("answer with keys only", async () => {
      await driver.keys(q, page);
      await checkIt(page, reply, driver, true);
    });
  }

  await test.step("focus moves to the next question's fieldset", async () => {
    const next = question(reply);
    await expect(next).toHaveAccessibleName(/^Q2\./);
    await expect(next).toBeFocused();
    await expect(card(reply)).toContainText(COPY.progress(2, 2, driver.points, driver.points));
  });

  await test.step("after the last question, focus moves to the score", async () => {
    const next = question(reply);
    await tabTo(page, next.getByRole("radio").first());
    await page.keyboard.press("Space");
    await checkIt(page, reply, DRIVERS.single, true);
    await expect(card(reply).getByText(COPY.score)).toBeFocused();
    await shot(page, testInfo, "keyboard-done");
  });
}

for (const kind of KINDS) {
  const driver = DRIVERS[kind];
  test.describe(kind, () => {
    for (const width of [1440, 390, 320] as Width[]) {
      test.describe(`${width}px`, () => {
        test.use(at(width));
        test("full flow", { tag: width === 1440 ? ["@demo"] : [] }, ({ page, pageGuard }, testInfo) =>
          fullFlow(page, pageGuard, driver, testInfo),
        );
        test("keyboard only, with the focus table", ({ page }, testInfo) => keyboardOnly(page, driver, testInfo));
      });
    }

    test.describe("390px", () => {
      test.use(at(390));
      test("touch targets", async ({ page }, testInfo) => {
        const { reply } = await openFixture(page, driver.fixture);
        await expect(question(reply)).toBeVisible();
        await expectTargets(card(reply));
        if (kind === "lines") await expectTargets(card(reply), 24, "[role=option]");
        await shot(page, testInfo, "targets");
      });
    });

    test.describe("834px", () => {
      test.use(at(834));
      test("renders without overflow", async ({ page }, testInfo) => {
        const { reply } = await openFixture(page, driver.fixture);
        await expect(question(reply)).toBeVisible();
        await expectNoHorizontalOverflow(page);
        await shot(page, testInfo, "834");
      });
    });

    test("persistence: a practice draft survives a chat switch, an exam draft a reload", async ({ page }) => {
      const other = await sendTurn(page, "A second chat to switch to.");
      const practice = await openFixture(page, driver.fixture);
      await test.step("practice: switch chats mid-answer and back", async () => {
        await driver.wrong(question(practice.reply), page);
        const typed = await formState(question(practice.reply));
        await switchChat(page, other.conversationId);
        await switchChat(page, practice.conversationId);
        await expect.poll(() => formState(question(lastReply(page)))).toEqual(typed);
      });
      await test.step("exam: reload mid-answer", async () => {
        const traffic = practiceTraffic(page);
        const exam = await openFixture(page, driver.fixture, { mode: "exam" });
        await driver.wrong(question(exam.reply), page);
        await page.keyboard.press("Tab"); // leaving a field flushes its pending save
        await traffic.settled("save");
        const saved = await formState(question(exam.reply));
        await page.reload();
        await waitForHydration(page);
        await expect.poll(() => formState(question(lastReply(page)))).toEqual(saved);
      });
    });
  });
}

test.describe("400-character options", () => {
  test.use(at(320));
  test("wrap in full at 320 px, before and after answering", async ({ page }, testInfo) => {
    const { reply } = await openFixture(page, "long-options");
    const options: string[] = blockOf("long-options").questions[0].options;
    for (const option of options) await expect(card(reply)).toContainText(option);
    await expectNothingClipped(card(reply));
    await expectNoHorizontalOverflow(page);
    await shot(page, testInfo, "long-options");
    await question(reply).getByRole("radio").nth(1).check();
    await reply.getByRole("button", { name: COPY.check }).click();
    await expect(card(reply)).toContainText(COPY.progress(2, 2, 1, 1));
    await expect(card(reply)).toContainText(options[1] as string);
    await expectNothingClipped(card(reply));
    await expectNoHorizontalOverflow(page);
    await shot(page, testInfo, "long-options-answered");
  });
});
