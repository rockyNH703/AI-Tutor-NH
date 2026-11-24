import type { Page } from "@playwright/test";

import {
  type People,
  type Person,
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
  test,
} from "./support";

/**
 * How a battle ends when it isn't played out (plan sections 9 and R-B1, R-B3; I21), drawn in the UI: a forfeit after
 * three missed rounds in a row, a void when both miss three, and a void when a block ends it mid-battle, which reads
 * as "ended" on both sides. Live accounts on the FRIEND_ZONE_MODE=on stack, so the XP rules (no XP for the forfeiter,
 * none for a void) can be seen.
 */

const LIVE = { qa: false, stack: "on" as const };

type Score = { reason: string; amount: number };

async function xpOf(people: People, person: Person): Promise<Score[]> {
  return people.sql<Score>("SELECT reason, amount FROM fz_xp_events WHERE user_id = $1 ORDER BY reason", [person.userId]);
}

/** A live pair, friends, in an active battle, both watching it. */
async function battling(people: People): Promise<{ asha: Person; bilal: Person; battleId: string }> {
  const asha = await people.add("Asha Menon", LIVE);
  const bilal = await people.add("Bilal Khan", LIVE);
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  const created = await bff(asha, "POST", "/battles", { opponentId: bilal.playerId, program: "networking-ai", month: 1, length: 5, requestId: crypto.randomUUID() });
  expect(created.status(), await created.text()).toBe(201);
  const { battleId } = (await created.json()) as { battleId: string };
  expect((await bff(bilal, "POST", `/battles/${battleId}/accept`)).status()).toBe(200);
  expect((await bff(asha, "POST", `/battles/${battleId}/ready`)).status()).toBe(200);
  for (const person of [asha, bilal]) await openFriendZone(person, `&battle=${battleId}`);
  return { asha, bilal, battleId };
}

const question = (page: Page, round: number) => page.getByRole("heading", { name: `Question ${round} of 5`, level: 4 });
const ending = (page: Page, headline: string) => page.getByRole("heading", { name: headline, level: 4 });

/** Back to Friend Zone: the battle's line in Past battles, and what Your progress says. */
async function historyLine(page: Page, line: string): Promise<void> {
  await page.getByRole("button", { name: "Back to Friend Zone" }).click();
  await expect(section(page, "Battles").getByText(line)).toBeVisible();
}

test("forfeit: three missed rounds in a row end the battle; each side is told why; the forfeiter gets no XP", async ({ people }, testInfo) => {
  test.setTimeout(240_000);
  const { asha, bilal, battleId } = await battling(people);
  for (let round = 1; round <= 3; round++) {
    await expect(question(asha.page, round)).toBeVisible({ timeout: 20_000 });
    const key = await correctKey(people, battleId, round);
    await asha.page.getByRole("button", { name: new RegExp(`^${key}\\. `) }).click(); // Bilal never answers
  }

  const won = ending(asha.page, "You won");
  const lost = ending(bilal.page, "You lost");
  await expect(won).toBeVisible({ timeout: 30_000 });
  await expect(lost).toBeVisible({ timeout: 30_000 });
  await expect(asha.page.getByText("Bilal Khan missed three rounds in a row.", { exact: true })).toBeVisible();
  await expect(bilal.page.getByText("You missed three rounds in a row, so the battle ended.", { exact: true })).toBeVisible();
  await expect.poll(() => focused(won)).toBe(true);
  await expect.poll(() => focused(lost)).toBe(true);
  await axe(asha.page, testInfo, "forfeit-won");
  await axe(bilal.page, testInfo, "forfeit-lost");
  const state = (await (await bff(asha, "GET", `/battles/${battleId}`)).json()) as { status: string; result: string; endReason: string };
  expect(state).toMatchObject({ status: "completed", result: "forfeit", endReason: "forfeit" });

  // The forfeiter: nothing. The remaining player: completion XP only (no win XP for a forfeit), and First Battle.
  expect(await xpOf(people, bilal)).toEqual([]);
  expect(await xpOf(people, asha)).toEqual([{ reason: "battle", amount: 20 }]);
  await historyLine(asha.page, "Won by forfeit · Month 1 · 5 questions");
  await historyLine(bilal.page, "Lost by forfeit · Month 1 · 5 questions");
  const theirs = section(bilal.page, "Your progress");
  await expect(theirs.getByText("No XP yet.", { exact: false })).toBeVisible();
  await expect(theirs.getByText("No badges yet.", { exact: false })).toBeVisible();
  const mine = section(asha.page, "Your progress");
  await expect(mine.getByText("Battle completed")).toHaveCount(1);
  await expect(mine.getByText("Battle won")).toHaveCount(0);
  await expect(mine.getByText("First Battle")).toBeVisible();
});

test("void by absence: both miss three rounds in a row: No result for both, no Rematch, no XP", async ({ people }, testInfo) => {
  test.setTimeout(240_000);
  const { asha, bilal, battleId } = await battling(people);
  await expect(question(asha.page, 1)).toBeVisible({ timeout: 20_000 });
  for (const person of [asha, bilal]) {
    await expect(ending(person.page, "No result")).toBeVisible({ timeout: 60_000 });
    await expect(person.page.getByText("Both of you missed three rounds in a row.", { exact: true })).toBeVisible();
    await expect(person.page.getByRole("button", { name: "Rematch" })).toHaveCount(0);
  }
  await axe(asha.page, testInfo, "void-absent");
  const state = (await (await bff(bilal, "GET", `/battles/${battleId}`)).json()) as { result: string; endReason: string };
  expect(state).toMatchObject({ result: "void", endReason: "absent" });
  expect(await xpOf(people, asha)).toEqual([]);
  expect(await xpOf(people, bilal)).toEqual([]);
  await historyLine(asha.page, "No result · Month 1 · 5 questions");
});

test("void by a block mid-battle: both screens read the same 'ended early', no winner, no XP, and the block stays silent", async ({ people }, testInfo) => {
  const { asha, bilal, battleId } = await battling(people);
  await expect(question(asha.page, 1)).toBeVisible({ timeout: 20_000 });
  const key = await correctKey(people, battleId, 1);
  await asha.page.getByRole("button", { name: new RegExp(`^${key}\\. `) }).click();

  // Asha blocks Bilal from Friend Zone in a second tab, confirmed in the alertdialog.
  const tab = await asha.context.newPage();
  await tab.goto("/programs/networking-ai/quizzes?zone=friends&month=1");
  const friends = section(tab, /^Friends/);
  await friends.getByRole("button", { name: "Bilal Khan", exact: true }).click();
  await friends.getByRole("button", { name: "Block" }).click();
  await tab.getByRole("alertdialog").getByRole("button", { name: "Block" }).click();
  await expect(section(tab, "Blocked").getByText("Bilal Khan", { exact: true })).toBeVisible();
  await tab.close();

  // Both battle screens: the same words; neither can tell a block from any other early end (I21).
  for (const person of [asha, bilal]) {
    await expect(ending(person.page, "No result")).toBeVisible({ timeout: 15_000 });
    await expect(person.page.getByText("This battle ended early, so it doesn't count.", { exact: true })).toBeVisible();
  }
  await axe(bilal.page, testInfo, "void-ended-blocked-side");
  for (const person of [asha, bilal]) {
    const state = (await (await bff(person, "GET", `/battles/${battleId}`)).json()) as { status: string; result: string; endReason: string };
    expect(state).toMatchObject({ status: "completed", result: "void", endReason: "ended" });
  }
  expect(await xpOf(people, asha)).toEqual([]);
  expect(await xpOf(people, bilal)).toEqual([]);
  const told = await people.sql("SELECT kind FROM user_notifications WHERE user_id = $1 AND kind = 'battle_finished'", [bilal.userId]);
  expect(told, "a void battle sends no result notice").toEqual([]);
  expect(battleIdOf(bilal.page)).toBe(battleId);
});
