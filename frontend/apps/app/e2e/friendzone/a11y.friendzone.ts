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
  expect,
  expectNoSidewaysScroll,
  focused,
  openFriendZone,
  section,
  shot,
  tabTo,
  test,
} from "./support";

/**
 * Accessibility (plan section 11; U10 approved): keyboard-only paths with a visible focus indicator, focus moves,
 * no sideways scroll at 320 and 390 px, and reduced motion (the timer bar does not move; only the number changes).
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

async function battleUnderWay(people: { add: (name: string, options?: object) => Promise<Person> }, ashaOptions: object, bilalOptions: object) {
  const asha = await people.add("Asha Menon", ashaOptions);
  const bilal = await people.add("Bilal Khan", bilalOptions);
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  return { asha, bilal };
}

async function startBattle(asha: Person, bilal: Person): Promise<string> {
  const created = await bff(asha, "POST", "/battles", { opponentId: bilal.playerId, program: "networking-ai", month: 1, length: 5, requestId: crypto.randomUUID() });
  expect(created.status(), await created.text()).toBe(201);
  const { battleId } = (await created.json()) as { battleId: string };
  expect((await bff(bilal, "POST", `/battles/${battleId}/accept`)).status()).toBe(200);
  expect((await bff(asha, "POST", `/battles/${battleId}/ready`)).status()).toBe(200);
  return battleId;
}

test("keyboard only: join, find, request, accept, a dialog closed with Escape, challenge, accept, Ready and answers", async ({ people }, testInfo) => {
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

  // Challenge by keyboard: focus starts inside the dialog; Send challenge.
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

  // Round 1 by shortcut keys only: Asha presses 1, Bilal presses b.
  for (const [who, key, choice] of [
    [asha, "1", "A"],
    [bilal, "b", "B"],
  ] as const) {
    const heading = who.page.getByRole("heading", { name: "Question 1 of 5", level: 4 });
    await expect(heading).toBeVisible({ timeout: 20_000 });
    await expect.poll(() => focused(heading)).toBe(true);
    await expect(who.page.getByText(/^Keys: press 1–\d or A–[A-H] to answer while focus is in the battle\.$/)).toBeVisible();
    await who.page.keyboard.press(key);
    await expect(who.page.getByText(`Answer locked: ${choice}. The answer shows when the round closes.`)).toBeVisible();
  }
  await shot(page, testInfo, "keyboard-round-1");
});

test("no sideways scroll at 320 and 390 px: join, the hub with requests and friends, the challenge dialog, a question, the reveal and the result", async ({
  people,
}, testInfo) => {
  test.setTimeout(240_000);
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

  const battleId = await startBattle(asha, bilal);
  for (const person of [asha, bilal]) await openFriendZone(person, `&battle=${battleId}`);
  for (const person of [asha, bilal]) {
    await expect(person.page.getByRole("heading", { name: "Question 1 of 5" })).toBeVisible({ timeout: 20_000 });
    await expectNoSidewaysScroll(person.page);
    await shot(person.page, testInfo, `question-${person.page.viewportSize()?.width}`);
  }
  for (let round = 1; round <= 5; round++) {
    for (const person of [asha, bilal]) {
      await expect(person.page.getByRole("heading", { name: `Question ${round} of 5` })).toBeVisible({ timeout: 20_000 });
      const answered = await bff(person, "POST", `/battles/${battleId}/answers`, { round, choice: "A" });
      expect(answered.status(), await answered.text()).toBe(200);
    }
    if (round === 1) {
      await axe(asha.page, testInfo, "question-locked-320", undefined, false);
      for (const person of [asha, bilal]) {
        await expect(person.page.getByRole("heading", { name: "Round 1 closed" })).toBeVisible({ timeout: 15_000 });
        await expectNoSidewaysScroll(person.page);
      }
    }
  }
  for (const person of [asha, bilal]) {
    await expect(person.page.getByRole("table", { name: "Scores" })).toBeVisible({ timeout: 20_000 });
    await expectNoSidewaysScroll(person.page);
    await shot(person.page, testInfo, `result-${person.page.viewportSize()?.width}`);
  }
  await axe(asha.page, testInfo, "result-320");
  expect(battleIdOf(asha.page)).toBe(battleId);
});

test("reduced motion: the timer bar does not move (device setting or the app's), only the number changes", async ({ people }, testInfo) => {
  const { asha, bilal } = await battleUnderWay(people, { reducedMotion: "reduce" }, { reducedMotion: "no-preference" });
  const battleId = await startBattle(asha, bilal);
  for (const person of [asha, bilal]) await openFriendZone(person, `&battle=${battleId}`);
  const bar = (page: Page) => page.locator("[aria-hidden='true'].motion-reduce\\:hidden");
  const clock = (page: Page) => page.getByText(/^(\d+ s|\d+ seconds left)$/);

  for (const person of [asha, bilal]) {
    await expect(person.page.getByRole("heading", { name: "Question 1 of 5" })).toBeVisible({ timeout: 20_000 });
  }
  // Device setting off: the bar is drawn and shrinks.
  await expect(bar(bilal.page)).toBeVisible();
  const scale = () => bar(bilal.page).locator("div").evaluate((element) => (element as HTMLElement).style.transform);
  const before = await scale();
  await expect.poll(scale, { timeout: 3_000 }).not.toBe(before);

  // Device setting on: no bar, nothing animating in the battle; the number still counts down.
  await expect(bar(asha.page)).toBeHidden();
  const first = await clock(asha.page).textContent();
  await expect.poll(() => clock(asha.page).textContent(), { timeout: 3_000 }).not.toBe(first);
  const moving = await asha.page.evaluate(() =>
    document
      .getAnimations()
      .filter((animation) => animation.playState === "running")
      .map((animation) => (animation.effect as KeyframeEffect | null)?.target)
      .filter((target) => target instanceof Element && target.closest("section")?.querySelector("h3")?.textContent?.startsWith("Battle with")).length,
  );
  expect(moving, "nothing animates in the battle under reduced motion").toBe(0);
  await shot(asha.page, testInfo, "reduced-motion-question");

  // The app's own Reduce motion (Settings > Appearance sets <html data-motion="reduce">): the bar is not drawn.
  await bilal.page.evaluate(() => {
    document.documentElement.dataset.motion = "reduce";
  });
  await expect(bar(bilal.page)).toHaveCount(0);
  await expect(clock(bilal.page)).toBeVisible();
});

test("the 10-second and 5-second warnings are shown with an icon and said once each in the live region, never every second", async ({ people }, testInfo) => {
  // The slow stack's rounds last SLOW_ROUND_SECONDS (12), so a round starts above the 10-second warning.
  const asha = await people.add("Asha Menon", { stack: "slow" });
  const bilal = await people.add("Bilal Khan", { stack: "slow" });
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  // Every value the battle's live region takes, in order (sampled every 50 ms from the first paint).
  await asha.context.addInitScript(() => {
    const said: string[] = [];
    (window as unknown as { __said: string[] }).__said = said;
    setInterval(() => {
      const region = [...document.querySelectorAll("p.sr-only[role='status']")].find((node) =>
        node.closest("section")?.querySelector("h3")?.textContent?.startsWith("Battle with"),
      );
      const text = region?.textContent ?? "";
      if (text && said[said.length - 1] !== text) said.push(text);
    }, 50);
  });
  const battleId = await startBattle(asha, bilal);
  await openFriendZone(asha, `&battle=${battleId}`);
  const { page } = asha;

  await expect(page.getByRole("heading", { name: "Question 1 of 5", level: 4 })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/^1[12] s$/)).toBeVisible(); // above 10 s: the plain number, no warning
  const ten = page.locator("p").filter({ hasText: /^10 seconds left$/ });
  await expect(ten).toBeVisible({ timeout: 5_000 });
  await expect(ten.locator("svg")).toHaveCount(1); // not colour alone: the words and an icon
  await shot(page, testInfo, "warning-10");
  await expect(page.locator("p").filter({ hasText: /^5 seconds left$/ })).toBeVisible({ timeout: 8_000 });
  await shot(page, testInfo, "warning-5");
  await expect(page.getByRole("heading", { name: "Round 1 closed" })).toBeVisible({ timeout: 15_000 });

  const said = await page.evaluate(() => (window as unknown as { __said: string[] }).__said);
  const start = said.indexOf(`Question 1 of 5, ${SLOW_ROUND_SECONDS} seconds.`);
  expect(start, `the round start was announced: ${JSON.stringify(said)}`).toBeGreaterThanOrEqual(0);
  const end = said.findIndex((text) => text.startsWith("Correct answer:"));
  const round = said.slice(start, end + 1);
  // Said once each, in order, and nothing in between: never a count every second.
  expect(round.slice(0, 3), `announcements in round 1: ${JSON.stringify(said)}`).toEqual([
    `Question 1 of 5, ${SLOW_ROUND_SECONDS} seconds.`,
    "10 seconds left.",
    "5 seconds left.",
  ]);
  expect(round).toHaveLength(4);
  expect(round[3]).toMatch(/^Correct answer: [A-D]\. You didn't answer\. Bilal Khan didn't answer\.$/);
});

test("dark mode: axe on join, the hub, a confirm and a challenge dialog, a battle question, the reveal, the result and the daily", async ({
  people,
}, testInfo) => {
  test.setTimeout(240_000);
  const dark = { colorScheme: "dark" as const };
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

  const battleId = await startBattle(asha, bilal);
  await openFriendZone(asha, `&battle=${battleId}`);
  await expect(page.getByRole("heading", { name: "Question 1 of 5", level: 4 })).toBeVisible({ timeout: 20_000 });
  await axe(page, testInfo, "dark-question", undefined, false);
  for (let round = 1; round <= 5; round++) {
    await expect(page.getByRole("heading", { name: `Question ${round} of 5`, level: 4 })).toBeVisible({ timeout: 20_000 });
    for (const person of [asha, bilal]) {
      expect((await bff(person, "POST", `/battles/${battleId}/answers`, { round, choice: "A" })).status()).toBe(200);
    }
    if (round === 1) {
      await expect(page.getByRole("heading", { name: "Round 1 closed" })).toBeVisible({ timeout: 15_000 });
      await axe(page, testInfo, "dark-reveal", undefined, false);
    }
  }
  await expect(page.getByRole("table", { name: "Scores" })).toBeVisible({ timeout: 20_000 });
  await axe(page, testInfo, "dark-result");

  await openFriendZone(asha);
  await section(page, "Daily challenge").getByRole("button", { name: "Start today's challenge" }).click();
  await expect(section(page, "Daily challenge").getByRole("heading", { name: "Question 1 of 10" })).toBeVisible({ timeout: 15_000 });
  await axe(page, testInfo, "dark-daily-question", undefined, false);
});
