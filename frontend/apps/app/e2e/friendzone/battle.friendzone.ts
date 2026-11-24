import type { Page } from "@playwright/test";

import { ROUND_SECONDS, SLOW_ROUND_SECONDS } from "./env";
import {
  type People,
  type Person,
  type Stack,
  apiBefriend,
  apiJoin,
  axe,
  battleIdOf,
  bff,
  correctKey,
  expect,
  focused,
  openFriendZone,
  section,
  shot,
  test,
} from "./support";

/**
 * Phase 1 journeys J4-J6 (plan sections 4, 9-11): a live battle between two signed-in browsers on the
 * FRIEND_ZONE_MODE=qa stack, rounds shortened to ROUND_SECONDS. The server is the only judge: keys come from the
 * database only to choose a right or a wrong answer on purpose.
 */

const KEYS = ["A", "B", "C", "D"];
const wrongOf = (key: string) => KEYS.find((other) => other !== key) ?? "A";

type How = "click" | "digit" | "letter";

/** Answers the open round as a member does: a click on the option, or its shortcut (1-8 or A-H) on the keyboard. */
async function answer(page: Page, round: number, choice: string, how: How): Promise<void> {
  const battle = page.locator("section").filter({ has: page.getByRole("heading", { name: /^Battle with/ }) });
  await expect(battle.getByRole("heading", { name: `Question ${round} of 5`, level: 4 })).toBeVisible({ timeout: 20_000 });
  const option = battle.getByRole("button", { name: new RegExp(`^${choice}\\. `) });
  if (how === "click") await option.click();
  else await page.keyboard.press(how === "digit" ? String(KEYS.indexOf(choice) + 1) : choice.toLowerCase());
  await expect(option).toHaveAttribute("aria-pressed", "true");
  await expect(battle.getByText(`Answer locked: ${choice}. The answer shows when the round closes.`)).toBeVisible();
}

async function startedBattle(people: People, stack: Stack = "qa") {
  const asha = await people.add("Asha Menon", { stack });
  const bilal = await people.add("Bilal Khan", { stack });
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  return { asha, bilal };
}

test("J4-J6 a battle end to end in two browsers: challenge, accept, ready, countdown, answers by click and by key, reveal, result and review", async ({
  people,
}, testInfo) => {
  test.setTimeout(300_000);
  const seconds = ROUND_SECONDS;
  const { asha, bilal } = await startedBattle(people);

  // J4: Asha challenges Bilal from her Friends list.
  await openFriendZone(asha);
  const friends = section(asha.page, /^Friends/);
  await friends.getByRole("button", { name: "Bilal Khan", exact: true }).click();
  await friends.getByRole("button", { name: "Challenge" }).click();
  const dialog = asha.page.getByRole("dialog", { name: "Challenge a friend" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Month")).toHaveValue("1");
  await expect(dialog.getByLabel(/^Topic/)).toBeEnabled(); // the month's topics have loaded
  await expect(dialog.getByRole("radio", { name: "Mixed" })).toBeChecked();
  await expect(dialog.getByRole("radio", { name: "5 questions" })).toBeChecked();
  await axe(asha.page, testInfo, "challenge-dialog", "[role='dialog']");
  await dialog.getByRole("button", { name: "Send challenge" }).click();
  await expect(asha.page.getByRole("heading", { name: "Battle with Bilal Khan" })).toBeVisible();
  await expect(asha.page.getByText("Waiting for Bilal Khan to accept.", { exact: false })).toBeVisible();
  const battleId = battleIdOf(asha.page);
  await axe(asha.page, testInfo, "battle-invited-out");

  // J5: Bilal accepts from his Battles list (accepting is his Ready).
  await openFriendZone(bilal);
  const battles = section(bilal.page, "Battles");
  await expect(battles.getByText("Challenged you · Month 1 · 5 questions")).toBeVisible();
  await axe(bilal.page, testInfo, "battles-invited-in");
  await battles.getByRole("button", { name: "Accept and play" }).click();
  await expect(bilal.page.getByText("You're in. Waiting for Asha Menon to start the battle.", { exact: false })).toBeVisible();
  expect(battleIdOf(bilal.page)).toBe(battleId);
  await axe(bilal.page, testInfo, "battle-waiting-start");

  // Asha's screen follows by polling; she presses Ready; a 3-second countdown.
  await expect(asha.page.getByText("Bilal Khan accepted. Press Ready to start", { exact: false })).toBeVisible({ timeout: 10_000 });
  await axe(asha.page, testInfo, "battle-ready");
  await asha.page.getByRole("button", { name: "Ready", exact: true }).click();
  await expect(asha.page.getByText("Get ready")).toBeVisible();
  await shot(asha.page, testInfo, "countdown");

  // Five rounds. Asha: right every time (click, digit, letter, click, digit). Bilal: wrong by digit, wrong by click,
  // no answer (time runs out), right by letter, right by click.
  const plan: { asha: How; bilal: How | null; bilalRight: boolean }[] = [
    { asha: "click", bilal: "digit", bilalRight: false },
    { asha: "digit", bilal: "click", bilalRight: false },
    { asha: "letter", bilal: null, bilalRight: false },
    { asha: "click", bilal: "letter", bilalRight: true },
    { asha: "digit", bilal: "click", bilalRight: true },
  ];
  for (const [index, step] of plan.entries()) {
    const round = index + 1;
    const heading = (page: Page) => page.getByRole("heading", { name: `Question ${round} of 5`, level: 4 });
    await expect(heading(asha.page)).toBeVisible({ timeout: 20_000 });
    await expect(heading(bilal.page)).toBeVisible({ timeout: 20_000 });
    // Focus moves to the new question for both players (plan section 11).
    await expect.poll(() => focused(heading(asha.page))).toBe(true);
    await expect.poll(() => focused(heading(bilal.page))).toBe(true);
    // The open round carries no key, explanation or opponent pick.
    const state = (await (await bff(asha, "GET", `/battles/${battleId}`)).json()) as { question: Record<string, unknown>; review: unknown };
    expect(Object.keys(state.question).sort()).toEqual(["closesAt", "myChoice", "opensAt", "options", "round", "seconds", "stem"]);
    expect(state.question.seconds).toBe(seconds);
    expect(state.review ?? null).toBeNull();

    const key = await correctKey(people, battleId, round);
    const bilalChoice = step.bilalRight ? key : wrongOf(key);
    await answer(asha.page, round, key, step.asha);
    // Inside the 8-second round: axe on Bilal's open question before he answers, then on Asha's locked one.
    const inRound = round === 1;
    if (inRound) await axe(bilal.page, testInfo, "battle-question", undefined, false);
    if (step.bilal) await answer(bilal.page, round, bilalChoice, step.bilal);
    if (inRound) await axe(asha.page, testInfo, "battle-question-locked", undefined, false);

    // The reveal: what each chose, the key, the points; focus stays where it was.
    const reveal = new RegExp(`^Correct answer: ${key}\\. You chose ${key}, \\d+ points\\. Bilal Khan ${step.bilal ? `chose ${bilalChoice}, ${step.bilalRight ? "\\d+" : "0"} points` : "didn't answer"}\\.$`);
    if (round === plan.length) break; // after the last round the result comes at once (no reveal); its review shows the round
    await expect(asha.page.getByRole("heading", { name: `Round ${round} closed` })).toBeVisible({ timeout: (seconds + 5) * 1000 });
    // Shown, and said once in the battle's one polite live region (plan section 11).
    await expect(asha.page.locator("p:not([role='status'])").filter({ hasText: reveal })).toBeVisible();
    await expect(asha.page.getByRole("region", { name: "Battle with Bilal Khan" }).getByRole("status")).toHaveText(reveal);
    if (round === 1) {
      await shot(bilal.page, testInfo, "reveal-bilal");
      await axe(asha.page, testInfo, "battle-reveal");
    }
  }

  // J6: the result, focused, with the comparison and a review with "Why".
  const won = asha.page.getByRole("heading", { name: "You won", level: 4 });
  const lost = bilal.page.getByRole("heading", { name: "You lost", level: 4 });
  await expect(won).toBeVisible({ timeout: 20_000 });
  await expect(lost).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => focused(won)).toBe(true);
  await expect.poll(() => focused(lost)).toBe(true);
  const scores = asha.page.getByRole("table", { name: "Scores" });
  await expect(scores.getByRole("row", { name: /^Correct/ })).toContainText("5 of 5");
  await expect(scores.getByRole("row", { name: /^Correct/ })).toContainText("2 of 4");
  await expect(scores.getByRole("row", { name: /^Accuracy/ })).toContainText("100%");
  const review = asha.page.getByRole("region", { name: "Review" });
  await expect(review.getByRole("listitem").filter({ hasText: /^Question \d/ })).toHaveCount(5);
  await expect(review.getByText("Why:")).toHaveCount(5);
  await expect(review.getByText("Correct answer", { exact: false }).first()).toBeVisible();
  await axe(asha.page, testInfo, "battle-result-won");
  await axe(bilal.page, testInfo, "battle-result-lost");
  const [row] = await people.sql<{ status: string; result: string }>("SELECT status, result FROM fz_battles WHERE id = $1", [battleId]);
  expect(row).toEqual({ status: "completed", result: "points" });

  // Back to Friend Zone: focus on Battles, and the battle in the history.
  await asha.page.getByRole("button", { name: "Back to Friend Zone" }).click();
  await expect.poll(() => focused(asha.page.getByRole("heading", { name: "Battles", level: 3 }))).toBe(true);
  await expect(section(asha.page, "Battles").getByText("Won · Month 1 · 5 questions")).toBeVisible();
  await axe(asha.page, testInfo, "battles-history");

  // A QA account never earns XP or badges (plan section 6; the database refuses them too).
  await expect(section(asha.page, "Your progress").getByText("No XP yet.", { exact: false })).toBeVisible();
  expect(await people.sql("SELECT 1 FROM fz_xp_events WHERE user_id = ANY($1)", [[asha.userId, bilal.userId]])).toEqual([]);
});

test("J5 an invitation can be declined, and a challenge cancelled, with the ending shown to both", { tag: "@webkit" }, async ({ people }, testInfo) => {
  const { asha, bilal } = await startedBattle(people);
  const challenge = async () => {
    const response = await bff(asha, "POST", "/battles", { opponentId: bilal.playerId, program: "networking-ai", month: 1, length: 5, requestId: crypto.randomUUID() });
    expect(response.status(), await response.text()).toBe(201);
    return ((await response.json()) as { battleId: string }).battleId;
  };

  const declined = await challenge();
  await openFriendZone(bilal, `&battle=${declined}`);
  await expect(bilal.page.getByText("Asha Menon challenged you to a live battle of 5 questions.", { exact: false })).toBeVisible();
  await axe(bilal.page, testInfo, "battle-invited-in");
  await bilal.page.getByRole("button", { name: "Decline" }).click();
  await expect(bilal.page.getByRole("heading", { name: "Declined", level: 4 })).toBeVisible();
  await openFriendZone(asha, `&battle=${declined}`);
  await expect(asha.page.getByRole("heading", { name: "Not accepted", level: 4 })).toBeVisible();
  await axe(asha.page, testInfo, "battle-not-accepted");

  const cancelled = await challenge();
  await openFriendZone(asha, `&battle=${cancelled}`);
  await asha.page.getByRole("button", { name: "Cancel challenge" }).click();
  await expect(asha.page.getByRole("heading", { name: "Cancelled", level: 4 })).toBeVisible();
  await openFriendZone(bilal, `&battle=${cancelled}`);
  await expect(bilal.page.getByText("Asha Menon cancelled this challenge.", { exact: true })).toBeVisible();
  await axe(bilal.page, testInfo, "battle-cancelled");
});

test("J5-J6 every battle phase drawn from the server: countdown, question, the lock, reveal, result and review (answers sent through the API)", { tag: "@webkit" }, async ({
  people,
}, testInfo) => {
  // In WebKit the test's own steps take several seconds each, so the answers go through the API (the click and key
  // paths are J4-J6 in Chromium) and the battle runs on the 12-second stack; the screen is what is checked.
  test.setTimeout(300_000);
  const { asha, bilal } = await startedBattle(people, "slow");
  await bilal.page.close(); // one window on screen: a covered window counts as hidden and polls every 10 s
  const created = await bff(asha, "POST", "/battles", { opponentId: bilal.playerId, program: "networking-ai", month: 1, length: 5, requestId: crypto.randomUUID() });
  expect(created.status(), await created.text()).toBe(201);
  const { battleId } = (await created.json()) as { battleId: string };
  expect((await bff(bilal, "POST", `/battles/${battleId}/accept`)).status()).toBe(200);
  expect((await bff(asha, "POST", `/battles/${battleId}/ready`)).status()).toBe(200);
  await openFriendZone(asha, `&battle=${battleId}`);
  const { page } = asha;
  for (let round = 1; round <= 5; round++) {
    const heading = page.getByRole("heading", { name: `Question ${round} of 5`, level: 4 });
    await expect(heading).toBeVisible({ timeout: 30_000 });
    const key = await correctKey(people, battleId, round);
    expect((await bff(asha, "POST", `/battles/${battleId}/answers`, { round, choice: key })).status()).toBe(200);
    expect((await bff(bilal, "POST", `/battles/${battleId}/answers`, { round, choice: wrongOf(key) })).status()).toBe(200);
    // The answer made elsewhere shows here on the next read: locked, and the option marked.
    await expect(page.getByText(`Answer locked: ${key}. The answer shows when the round closes.`)).toBeVisible({ timeout: 10_000 });
    if (round === 1) {
      await expect.poll(() => focused(heading)).toBe(true);
      await shot(page, testInfo, "question-locked");
    }
    if (round < 5) await expect(page.getByRole("heading", { name: `Round ${round} closed` })).toBeVisible({ timeout: (SLOW_ROUND_SECONDS + 10) * 1000 });
  }
  const won = page.getByRole("heading", { name: "You won", level: 4 });
  await expect(won).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => focused(won)).toBe(true);
  await expect(page.getByRole("table", { name: "Scores" }).getByRole("row", { name: /^Correct/ })).toContainText("5 of 5");
  await expect(page.getByRole("region", { name: "Review" }).getByText("Why:")).toHaveCount(5);
  await axe(page, testInfo, "result-api-played");
});
