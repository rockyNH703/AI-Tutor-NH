import type { Page } from "@playwright/test";

import { describeFocus } from "../support";
import { SLOW_ROUND_SECONDS } from "./env";
import {
  type Person,
  apiBefriend,
  apiJoin,
  axe,
  battleIdOf,
  bff,
  examUi,
  expect,
  expectNoSidewaysScroll,
  focused,
  openFriendZone,
  pick,
  section,
  setItem,
  shot,
  startExam,
  submitExam,
  tabTo,
  test,
  timerSeconds,
} from "./support";

/**
 * Accessibility (Friend Zone plan section 11, U10; Friend Challenge plan Rev 2 section 6): keyboard-only paths with a
 * visible focus indicator, the exam's shortcut guards and focus moves, no sideways scroll at 320, 390, 768 and
 * 1440 px, reduced motion (no timer bar at all; the countdown never goes up), the 1-minute warning said once, and axe
 * in light and dark with the Questions sheet and the submit dialog open.
 */

async function expectVisibleFocus(page: Page): Promise<void> {
  const focus = await describeFocus(page);
  expect(focus.indicated, `a visible focus indicator on "${focus.label}"`).toBe(true);
  expect(focus.onScreen, `"${focus.label}" is on screen`).toBe(true);
}

/** Keyboard only: Tab to the control, see its focus, press the key. */
async function press(page: Page, target: ReturnType<Page["getByRole"]>, key = "Enter"): Promise<void> {
  await tabTo(page, target, 150);
  await expectVisibleFocus(page);
  await page.keyboard.press(key);
}

async function friendsPair(people: { add: (name: string, options?: object) => Promise<Person> }, ashaOptions: object = {}, bilalOptions: object = {}) {
  const asha = await people.add("Asha Menon", ashaOptions);
  const bilal = await people.add("Bilal Khan", bilalOptions);
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  return { asha, bilal };
}

const checked = async (page: Page): Promise<string[]> => {
  const result: string[] = [];
  for (const key of ["A", "B", "C", "D"]) if (await examUi.option(page, key).isChecked()) result.push(key);
  return result;
};

test("keyboard only: join, find, request, accept, a dialog closed with Escape, challenge, accept, Ready and a pick by shortcut", async ({ people }, testInfo) => {
  test.setTimeout(240_000);
  const asha = await people.add("Asha Menon");
  const bilal = await people.add("Bilal Khan");
  await apiJoin(bilal, true);
  const { page } = asha;
  await openFriendZone(asha);

  // Join: name, the findable switch (Space), Join.
  await tabTo(page, page.getByLabel("Friend Zone name"), 150);
  await expectVisibleFocus(page);
  await page.keyboard.type("Asha Menon");
  await press(page, page.getByRole("switch", { name: "Let classmates find me by invoice number" }), "Space");
  await expect(page.getByRole("switch", { name: "Let classmates find me by invoice number" })).toHaveAttribute("aria-checked", "true");
  await press(page, page.getByRole("button", { name: "Join Friend Zone" }));
  await expect(page.getByText("You've joined Friend Zone.")).toBeVisible();

  // Find Bilal (Enter submits) and send the request.
  const add = section(page, "Add a friend");
  await tabTo(page, add.getByLabel("Classmate's invoice number"));
  await page.keyboard.type(bilal.invoice);
  await page.keyboard.press("Enter");
  await press(page, add.getByRole("button", { name: "Send friend request" }));
  await expect(add.getByText("Request sent to Bilal Khan.")).toBeVisible();

  // Bilal accepts by keyboard.
  await openFriendZone(bilal);
  await press(bilal.page, section(bilal.page, "Requests").getByRole("button", { name: "Accept" }));
  await expect(bilal.page.getByText("You're now friends with Asha Menon.")).toBeVisible();

  // Asha: the friend's row opens with Enter; Remove friend's dialog closes with Escape and gives focus back.
  await openFriendZone(asha);
  const friends = section(page, /^Friends/);
  await press(page, friends.getByRole("button", { name: "Bilal Khan", exact: true }));
  await expect(friends.getByRole("button", { name: "Bilal Khan", exact: true })).toHaveAttribute("aria-expanded", "true");
  const remove = friends.getByRole("button", { name: "Remove friend" });
  await press(page, remove);
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect.poll(() => focused(remove)).toBe(true);

  // Challenge by keyboard: focus starts inside the dialog; the length is a radio group (arrow keys); Send challenge.
  await press(page, friends.getByRole("button", { name: "Challenge" }));
  const dialog = page.getByRole("dialog", { name: "Challenge a friend" });
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  await expect(dialog.getByLabel(/^Topic/)).toBeEnabled();
  await press(page, dialog.getByRole("button", { name: "Send challenge" }));
  await expect(page.getByRole("heading", { name: "Battle with Bilal Khan" })).toBeVisible();

  await openFriendZone(bilal);
  await press(bilal.page, section(bilal.page, "Battles").getByRole("button", { name: "Accept and play" }));
  await expect(page.getByRole("button", { name: "Ready", exact: true })).toBeVisible({ timeout: 10_000 });
  await press(page, page.getByRole("button", { name: "Ready", exact: true }));

  // The exam: focus on question 1 for both, the help line names the keys; Asha presses 1, Bilal presses b.
  for (const [who, key, choice] of [
    [asha, "1", "A"],
    [bilal, "b", "B"],
  ] as const) {
    const heading = examUi.heading(who.page, 1, 10);
    await expect(heading).toBeVisible({ timeout: 20_000 });
    await expect.poll(() => focused(heading)).toBe(true);
    await expect(who.page.getByText(/^Keys 1–4 or A–D choose an option/)).toBeVisible();
    await who.page.keyboard.press(key);
    await expect(examUi.option(who.page, choice)).toBeChecked();
  }
  await shot(page, testInfo, "keyboard-exam-start");
});

test("keyboard only in the exam: the shortcut guards, a pick inside the Questions sheet, focus after it, and Submit", async ({ people }, testInfo) => {
  test.setTimeout(240_000);
  const total = 20;
  const { asha, bilal } = await friendsPair(people, { viewport: { width: 1024, height: 800 } }); // below xl: the palette is a sheet
  await bilal.page.close();
  const battleId = await startExam(asha, bilal, total);
  await openFriendZone(asha, `&battle=${battleId}`);
  const { page } = asha;
  const first = examUi.heading(page, 1, total);
  await expect(first).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => focused(first)).toBe(true);

  // A digit picks; a modifier, a held (repeating) key and a key with nothing to pick do not.
  await page.keyboard.press("2");
  await expect(examUi.option(page, "B")).toBeChecked();
  await page.keyboard.press("Shift+C");
  expect(await checked(page), "Shift held: no pick").toEqual(["B"]);
  await page.keyboard.press("9");
  expect(await checked(page), "no ninth option").toEqual(["B"]);
  await page.keyboard.down("3"); // first press: picks C
  await expect(examUi.option(page, "C")).toBeChecked();
  await press(page, examUi.action(page, "Clear Response"));
  expect(await checked(page)).toEqual([]);
  await page.keyboard.down("3"); // the same key still held: an auto-repeat, ignored
  expect(await checked(page), "a repeating key: no pick").toEqual([]);
  await page.keyboard.up("3");

  // A focused radio still takes the shortcut (and arrow keys move the native radio group).
  await tabTo(page, examUi.option(page, "A"), 150);
  await expectVisibleFocus(page);
  await page.keyboard.press("4");
  await expect(examUi.option(page, "D")).toBeChecked();
  await page.keyboard.press("ArrowDown"); // from the focused A
  await expect(examUi.option(page, "B")).toBeChecked();

  // The Questions sheet: open by keyboard; a shortcut inside it never picks; a pill moves to that question, the sheet
  // closes and focus lands on the question's heading.
  await press(page, examUi.action(page, "Questions"));
  const sheet = examUi.sheet(page);
  await expect(sheet).toBeVisible();
  expect(await sheet.evaluate((element) => element.contains(document.activeElement)), "focus moved into the sheet").toBe(true);
  await page.keyboard.press("1");
  await press(page, examUi.pill(page, 5));
  await expect(sheet).toHaveCount(0);
  const fifth = examUi.heading(page, 5, total);
  await expect(fifth).toBeVisible();
  await expect.poll(() => focused(fifth)).toBe(true);
  await press(page, examUi.action(page, "Questions"));
  await expect(examUi.pill(page, 1)).toHaveAttribute("aria-label", "Question 1, answered"); // still B: the 1 in the sheet picked nothing
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await expect.poll(() => focused(examUi.action(page, "Questions")), { message: "Escape gives focus back to the Questions button" }).toBe(true);

  // From 1280 px the palette sits beside the question: a digit pressed on a pill picks nothing either.
  await page.setViewportSize({ width: 1440, height: 900 });
  await tabTo(page, examUi.pill(page, 2), 150);
  await page.keyboard.press("1");
  expect(await checked(page), "a key on the palette: no pick on question 5").toEqual([]);

  // Submit by keyboard: Keep answering has focus first; Escape closes; then Submit in the dialog.
  await press(page, examUi.action(page, "Submit"));
  const confirm = examUi.submitDialog(page);
  await expect(confirm).toBeVisible();
  await expect.poll(() => focused(confirm.getByRole("button", { name: "Keep answering" }))).toBe(true);
  await expectVisibleFocus(page);
  await page.keyboard.press("Escape");
  await expect(confirm).toHaveCount(0);
  await press(page, examUi.action(page, "Submit"));
  await expect(confirm).toBeVisible();
  await press(page, confirm.getByRole("button", { name: "Submit", exact: true }));
  const waiting = examUi.waiting(page);
  await expect(waiting).toBeVisible();
  await expect.poll(() => focused(waiting)).toBe(true);
  expect(await people.sql("SELECT submitted_at IS NOT NULL AS done FROM fz_battle_players WHERE battle_id = $1 AND role = 'challenger'", [battleId])).toEqual([
    { done: true },
  ]);
  expect((await submitExam(bilal, battleId)).status()).toBe(200);
  // Question 1's last pick may be right or wrong (+4 or -1 against nothing): either way a result, focused.
  const result = page.getByRole("heading", { name: /^(You won|You lost)$/, level: 4 });
  await expect(result).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => focused(result)).toBe(true);
  await shot(page, testInfo, "keyboard-exam-result");
});

test("no sideways scroll at 320, 390, 768 and 1440 px, and axe in light mode with the Questions sheet and the submit dialog open", async ({ people }, testInfo) => {
  test.setTimeout(300_000);
  const phone = { width: 320, height: 800 };
  const wider = { width: 390, height: 844 };
  const asha = await people.add("Asha Menon", { viewport: phone });
  const bilal = await people.add("Bilal Khan", { viewport: wider });
  const carol = await people.add("Carolina Fernandes-Iyer");
  for (const person of [asha, bilal]) {
    await openFriendZone(person);
    await expect(person.page.getByRole("heading", { name: "Join Friend Zone" })).toBeVisible();
    await expectNoSidewaysScroll(person.page);
    await shot(person.page, testInfo, `join-${person.page.viewportSize()?.width}`);
  }
  await axe(asha.page, testInfo, "join-320");
  for (const person of [asha, bilal, carol]) await apiJoin(person, true);
  await apiBefriend(asha, bilal);
  expect((await bff(carol, "POST", "/requests", { playerId: asha.playerId })).status()).toBe(201);

  for (const person of [asha, bilal]) {
    await openFriendZone(person);
    await expect(section(person.page, /^Friends/).getByRole("button", { name: /Asha Menon|Bilal Khan/ })).toBeVisible();
    await section(person.page, /^Friends/).getByRole("button", { name: /Asha Menon|Bilal Khan/ }).click();
    await expectNoSidewaysScroll(person.page);
    await shot(person.page, testInfo, `hub-${person.page.viewportSize()?.width}`);
  }
  await expect(section(asha.page, "Requests").getByText("Carolina Fernandes-Iyer")).toBeVisible();
  await axe(asha.page, testInfo, "hub-320");
  await section(asha.page, /^Friends/).getByRole("button", { name: "Challenge" }).click();
  await expect(asha.page.getByRole("dialog", { name: "Challenge a friend" })).toBeVisible();
  await expectNoSidewaysScroll(asha.page);
  await axe(asha.page, testInfo, "challenge-dialog-320", "[role='dialog']");
  await asha.page.keyboard.press("Escape");

  const total = 20;
  const battleId = await startExam(asha, bilal, total);
  for (const person of [asha, bilal]) await openFriendZone(person, `&battle=${battleId}`);
  for (const person of [asha, bilal]) {
    await expect(examUi.heading(person.page, 1, total)).toBeVisible({ timeout: 20_000 });
    await pick(person.page, "A");
    await expect(examUi.saved(person.page)).toBeVisible();
    await expectNoSidewaysScroll(person.page);
    await shot(person.page, testInfo, `exam-${person.page.viewportSize()?.width}`);
  }
  // The action bar: four buttons, each at least 44 px tall, inside the screen at 320 px.
  const page = asha.page;
  for (const name of ["Previous", "Clear Response", "Mark for Review & Next", "Save & Next"] as const) {
    const box = await examUi.action(page, name).boundingBox();
    expect(box && box.height >= 44 && box.x >= 0 && box.x + box.width <= 320, `${name} fits at 320 px (${JSON.stringify(box)})`).toBe(true);
  }
  await axe(page, testInfo, "exam-question-320");

  // The Questions sheet at 320 px: every pill reachable, nothing sideways, axe in light mode.
  await examUi.action(page, "Questions").click();
  await expect(examUi.sheet(page)).toBeVisible();
  await expect(examUi.pill(page, total)).toBeVisible();
  await expectNoSidewaysScroll(page);
  await shot(page, testInfo, "exam-sheet-320");
  await axe(page, testInfo, "exam-sheet-320-light");
  await page.keyboard.press("Escape");

  // The submit dialog at 320 px.
  await examUi.action(page, "Submit").click();
  await expect(examUi.submitDialog(page)).toBeVisible();
  await expectNoSidewaysScroll(page);
  await shot(page, testInfo, "exam-submit-320");
  await axe(page, testInfo, "exam-submit-dialog-320-light");
  await examUi.submitDialog(page).getByRole("button", { name: "Keep answering" }).click();

  // The same exam at 768 and 1440 px (the palette beside the question from 1280).
  for (const width of [768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(examUi.heading(page, 1, total)).toBeVisible();
    await expectNoSidewaysScroll(page);
    await shot(page, testInfo, `exam-${width}`);
  }
  await expect(examUi.palette(page)).toBeVisible();
  await page.setViewportSize(phone);

  // Submitted (waiting) and the result, at 320 and 390 px.
  await examUi.action(page, "Submit").click();
  await examUi.submitDialog(page).getByRole("button", { name: "Submit", exact: true }).click();
  await expect(examUi.waiting(page)).toBeVisible();
  await expectNoSidewaysScroll(page);
  await shot(page, testInfo, "exam-waiting-320");
  await axe(page, testInfo, "exam-waiting-320");
  expect((await submitExam(bilal, battleId, [setItem(2, "A")])).status()).toBe(200);
  for (const person of [asha, bilal]) {
    await expect(person.page.getByRole("table", { name: "Scores" })).toBeVisible({ timeout: 20_000 });
    await expectNoSidewaysScroll(person.page);
    await shot(person.page, testInfo, `result-${person.page.viewportSize()?.width}`);
  }
  await axe(page, testInfo, "result-320");
  expect(battleIdOf(page)).toBe(battleId);
});

test("reduced motion: no timer bar and nothing animating in the exam; the countdown still counts and never goes up", async ({ people }, testInfo) => {
  const { asha, bilal } = await friendsPair(people, { reducedMotion: "reduce" }, { reducedMotion: "no-preference" });
  const battleId = await startExam(asha, bilal);
  for (const person of [asha, bilal]) await openFriendZone(person, `&battle=${battleId}`);
  for (const person of [asha, bilal]) await expect(examUi.heading(person.page, 1, 10)).toBeVisible({ timeout: 20_000 });

  // The countdown, sampled for 4 s on both screens: it moves, and never up (plan section 6).
  for (const person of [asha, bilal]) {
    const seen: number[] = [];
    for (let sample = 0; sample < 16; sample++) {
      seen.push(await timerSeconds(person.page));
      await person.page.waitForTimeout(250);
    }
    expect(seen.every((value, index) => index === 0 || value <= (seen[index - 1] ?? value)), `never up: ${seen.join(",")}`).toBe(true);
    expect(seen[seen.length - 1], "it counts down").toBeLessThan(seen[0] ?? 0);
  }
  // No timer bar under reduced motion: none is drawn at all (the exam has only the number).
  await expect(asha.page.locator("[aria-hidden='true'].motion-reduce\\:hidden")).toHaveCount(0);
  const moving = await asha.page.evaluate(() => {
    const exam = document.querySelector("[role='timer']")?.closest("section");
    return document
      .getAnimations()
      .filter((animation) => animation.playState === "running")
      .map((animation) => (animation.effect as KeyframeEffect | null)?.target)
      .filter((target) => target instanceof Element && exam?.contains(target)).length;
  });
  expect(moving, "nothing animates in the exam under reduced motion").toBe(0);
  await shot(asha.page, testInfo, "reduced-motion-exam");
  for (const person of [asha, bilal]) expect((await submitExam(person, battleId)).status()).toBe(200);
});

test("the 1-minute warning: the countdown turns to the warning style and '1 minute left.' is said once in the live region, never every second", async ({ people }, testInfo) => {
  test.setTimeout(240_000);
  // The slow stack: SLOW_ROUND_SECONDS (12) per question, so a 10-question exam starts at 2 minutes, above the warning.
  const { asha, bilal } = await friendsPair(people, { stack: "slow" }, { stack: "slow" });
  // Every value the exam's live region takes, in order (sampled every 50 ms from the first paint): the region is the
  // polite status line beside the exam (the one whose parent holds the timer).
  await asha.context.addInitScript(() => {
    const said: string[] = [];
    (window as unknown as { __said: string[] }).__said = said;
    setInterval(() => {
      const region = [...document.querySelectorAll("p.sr-only[role='status']")].find((node) => node.parentElement?.querySelector("[role='timer']"));
      const text = region?.textContent ?? "";
      if (text && said[said.length - 1] !== text) said.push(text);
    }, 50);
  });
  const battleId = await startExam(asha, bilal);
  await openFriendZone(asha, `&battle=${battleId}`);
  const { page } = asha;
  await expect(examUi.heading(page, 1, 10)).toBeVisible({ timeout: 20_000 });
  expect(await timerSeconds(page)).toBeGreaterThan(60);
  await expect(examUi.timer(page)).not.toHaveClass(/text-warning/);
  await expect.poll(() => timerSeconds(page), { timeout: 70_000, intervals: [250] }).toBeLessThanOrEqual(60);
  await expect(examUi.timer(page)).toHaveClass(/text-warning/);
  await expect(examUi.timer(page).locator("svg")).toHaveCount(1); // the clock icon beside the number
  await expect.poll(() => page.evaluate(() => (window as unknown as { __said: string[] }).__said.includes("1 minute left.")), { timeout: 5_000 }).toBe(true);
  await shot(page, testInfo, "warning-1-minute");
  await page.waitForTimeout(3_000); // three more seconds: nothing more is said

  const said = await page.evaluate(() => (window as unknown as { __said: string[] }).__said);
  const start = said.indexOf(`The exam has started: 10 questions, ${(10 * SLOW_ROUND_SECONDS) / 60} minutes.`);
  expect(start, `the start was announced: ${JSON.stringify(said)}`).toBeGreaterThanOrEqual(0);
  expect(said.slice(start), `announcements: ${JSON.stringify(said)}`).toEqual([`The exam has started: 10 questions, ${(10 * SLOW_ROUND_SECONDS) / 60} minutes.`, "1 minute left."]);
  for (const person of [asha, bilal]) expect((await submitExam(person, battleId)).status()).toBe(200);
});

test("dark mode: axe on join, the hub, a confirm and a challenge dialog, the exam question, the Questions sheet, the submit dialog, waiting, the result and the daily", async ({
  people,
}, testInfo) => {
  test.setTimeout(240_000);
  const dark = { colorScheme: "dark" as const, viewport: { width: 1024, height: 800 } };
  const asha = await people.add("Asha Menon", dark);
  const bilal = await people.add("Bilal Khan", dark);
  const { page } = asha;
  await openFriendZone(asha);
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  await expect(page.getByRole("heading", { name: "Join Friend Zone" })).toBeVisible();
  await axe(page, testInfo, "dark-join");

  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  await openFriendZone(asha);
  const friends = section(page, /^Friends/);
  await friends.getByRole("button", { name: "Bilal Khan", exact: true }).click();
  await expect(section(page, "Daily challenge").getByRole("button", { name: "Start today's challenge" })).toBeVisible();
  await axe(page, testInfo, "dark-hub");
  await friends.getByRole("button", { name: "Remove friend" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await axe(page, testInfo, "dark-remove-confirm", "[role='alertdialog']");
  await page.getByRole("alertdialog").getByRole("button", { name: "Cancel" }).click();
  await friends.getByRole("button", { name: "Challenge" }).click();
  await expect(page.getByRole("dialog", { name: "Challenge a friend" }).getByLabel(/^Topic/)).toBeEnabled();
  await axe(page, testInfo, "dark-challenge-dialog", "[role='dialog']");
  await page.keyboard.press("Escape");

  const battleId = await startExam(asha, bilal);
  await openFriendZone(asha, `&battle=${battleId}`);
  await expect(examUi.heading(page, 1, 10)).toBeVisible({ timeout: 20_000 });
  await pick(page, "A");
  await examUi.action(page, "Mark for Review & Next").click();
  await expect(examUi.saved(page)).toBeVisible();
  await axe(page, testInfo, "dark-exam-question");
  await examUi.action(page, "Questions").click();
  await expect(examUi.sheet(page)).toBeVisible();
  await axe(page, testInfo, "dark-exam-sheet");
  await page.keyboard.press("Escape");
  await examUi.action(page, "Submit").click();
  await expect(examUi.submitDialog(page)).toBeVisible();
  await axe(page, testInfo, "dark-exam-submit-dialog");
  await examUi.submitDialog(page).getByRole("button", { name: "Submit", exact: true }).click();
  await expect(examUi.waiting(page)).toBeVisible();
  await axe(page, testInfo, "dark-exam-waiting");
  expect((await submitExam(bilal, battleId, [setItem(1, "B")])).status()).toBe(200);
  await expect(page.getByRole("table", { name: "Scores" })).toBeVisible({ timeout: 20_000 });
  await axe(page, testInfo, "dark-result");

  await openFriendZone(asha);
  await section(page, "Daily challenge").getByRole("button", { name: "Start today's challenge" }).click();
  await expect(section(page, "Daily challenge").getByRole("heading", { name: "Question 1 of 10" })).toBeVisible({ timeout: 15_000 });
  await axe(page, testInfo, "dark-daily-question", undefined, false);
});
