import type { Page } from "@playwright/test";

import { ROUND_SECONDS } from "./env";
import {
  type People,
  type Person,
  apiBefriend,
  apiJoin,
  axe,
  battleIdOf,
  battleState,
  correctKeys,
  examUi,
  expect,
  focused,
  openFriendZone,
  pick,
  section,
  startExam,
  test,
} from "./support";

/**
 * How an exam battle ends when it isn't played out (Friend Challenge plan Rev 2 sections 3 and 8; I21), drawn in the
 * UI: absent (neither answered), a forfeit when the opponent's account is gone, and a void when a block ends it
 * mid-exam, which reads as "ended" on both sides. Live accounts on the FRIEND_ZONE_MODE=on stack, so the XP rules
 * (none for a void, +20 and never +30 for a forfeit) can be seen.
 */

const LIVE = { qa: false, stack: "on" as const };
const TOTAL = 10;

type Score = { reason: string; amount: number };

async function xpOf(people: People, person: Person): Promise<Score[]> {
  return people.sql<Score>("SELECT reason, amount FROM fz_xp_events WHERE user_id = $1 ORDER BY reason", [person.userId]);
}

/** A live pair, friends, in a running 10-question exam, both watching it. */
async function examining(people: People): Promise<{ asha: Person; bilal: Person; battleId: string }> {
  const asha = await people.add("Asha Menon", LIVE);
  const bilal = await people.add("Bilal Khan", LIVE);
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  const battleId = await startExam(asha, bilal, TOTAL);
  for (const person of [asha, bilal]) await openFriendZone(person, `&battle=${battleId}`);
  for (const person of [asha, bilal]) await expect(examUi.heading(person.page, 1, TOTAL)).toBeVisible({ timeout: 20_000 });
  return { asha, bilal, battleId };
}

const ending = (page: Page, headline: string) => page.getByRole("heading", { name: headline, level: 4 });

/** Back to Friend Zone: the battle's line in the Battles history. */
async function historyLine(page: Page, line: string): Promise<void> {
  await page.getByRole("button", { name: "Back to Friend Zone" }).click();
  await expect(section(page, "Battles").getByText(line)).toBeVisible();
}

test("absent: neither answers (one submits, the other runs out of time): No result for both, no Rematch, no XP", async ({ people }, testInfo) => {
  test.setTimeout(240_000);
  const { asha, bilal, battleId } = await examining(people);
  await examUi.action(asha.page, "Submit").click();
  await examUi.submitDialog(asha.page).getByRole("button", { name: "Submit", exact: true }).click();
  await expect(examUi.waiting(asha.page)).toBeVisible();
  // Bilal never submits: the deadline does it for him (his own auto-submit, or Asha's next read after the cutoff).
  for (const person of [bilal, asha]) {
    const headline = ending(person.page, "No result");
    await expect(headline).toBeVisible({ timeout: (TOTAL * ROUND_SECONDS + 20) * 1000 });
    await expect.poll(() => focused(headline)).toBe(true);
    await expect(person.page.getByText("Neither of you answered a question.", { exact: true })).toBeVisible();
    await expect(person.page.getByRole("button", { name: "Rematch" })).toHaveCount(0);
  }
  await axe(asha.page, testInfo, "void-absent");
  expect(await battleState(bilal, battleId)).toMatchObject({ result: "void", endReason: "absent" });
  expect(await xpOf(people, asha)).toEqual([]);
  expect(await xpOf(people, bilal)).toEqual([]);
  await historyLine(asha.page, `No result · Month 1 · ${TOTAL} questions`);
});

test("forfeit: the opponent's account goes mid-exam; the remaining player submits half the paper and wins, +20 XP and never +30", async ({ people }, testInfo) => {
  test.setTimeout(240_000);
  const { asha, bilal, battleId } = await examining(people);
  const keys = await correctKeys(people, battleId);
  const { page } = asha;
  for (let round = 1; round <= TOTAL / 2; round++) {
    await expect(examUi.heading(page, round, TOTAL)).toBeVisible();
    await pick(page, keys[round - 1] ?? "A");
    await examUi.action(page, "Save & Next").click();
  }
  await expect(examUi.saved(page)).toBeVisible();
  await bilal.context.close();
  await people.sql("DELETE FROM users WHERE id = $1", [bilal.userId]); // as deleting the account does; the fixture's own clean-up then finds nothing

  await examUi.action(page, "Submit").click();
  await examUi.submitDialog(page).getByRole("button", { name: "Submit", exact: true }).click();
  const won = ending(page, "You won");
  await expect(won).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => focused(won)).toBe(true);
  await expect(page.getByText("The other member left the battle.", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Your XP", level: 5 })).toBeVisible();
  await expect(page.getByText("Battle played", { exact: true })).toBeVisible();
  await expect(page.getByText("+20 XP", { exact: true })).toBeVisible(); // the total: no win XP for a forfeit
  await expect(page.getByText("Win", { exact: true })).toHaveCount(0);
  await axe(page, testInfo, "forfeit-won");
  expect(await battleState(asha, battleId)).toMatchObject({ status: "completed", result: "forfeit" });
  expect(await xpOf(people, asha)).toEqual([{ reason: "battle", amount: 20 }]);
  await historyLine(page, `Won by forfeit · Month 1 · ${TOTAL} questions`);
  await expect(section(page, "Your progress").getByText("First Battle")).toBeVisible();
});

test("void by a block mid-exam: both screens read the same 'ended early', no winner, no XP, the block stays silent, and the saved pick still counts as practice", async ({
  people,
}, testInfo) => {
  const { asha, bilal, battleId } = await examining(people);
  const [key] = await correctKeys(people, battleId);
  await pick(asha.page, key ?? "A");
  await expect(examUi.saved(asha.page)).toBeVisible();

  // Asha blocks Bilal from Friend Zone in a second tab, confirmed in the alertdialog.
  const tab = await asha.context.newPage();
  await tab.goto("/programs/networking-ai/quizzes?zone=friends&month=1");
  const friends = section(tab, /^Friends/);
  await friends.getByRole("button", { name: "Bilal Khan", exact: true }).click();
  await friends.getByRole("button", { name: "Block" }).click();
  await tab.getByRole("alertdialog").getByRole("button", { name: "Block" }).click();
  await expect(section(tab, "Blocked").getByText("Bilal Khan", { exact: true })).toBeVisible();
  await tab.close();

  // Both exam screens: the same words on the next read (every 10 s); neither can tell a block from any other end (I21).
  for (const person of [asha, bilal]) {
    await expect(ending(person.page, "No result")).toBeVisible({ timeout: 20_000 });
    await expect(person.page.getByText("This battle ended early, so it doesn't count.", { exact: true })).toBeVisible();
  }
  await axe(bilal.page, testInfo, "void-ended-blocked-side");
  for (const person of [asha, bilal]) {
    expect(await battleState(person, battleId)).toMatchObject({ status: "completed", result: "void", endReason: "ended" });
  }
  expect(await xpOf(people, asha)).toEqual([]);
  expect(await xpOf(people, bilal)).toEqual([]);
  const told = await people.sql("SELECT kind FROM user_notifications WHERE user_id = $1 AND kind = 'battle_finished'", [bilal.userId]);
  expect(told, "a void battle sends no result notice").toEqual([]);
  expect(await people.sql("SELECT 1 FROM fz_battle_responses WHERE battle_id = $1", [battleId]), "the drafts are gone").toEqual([]);
  const practised = await people.sql(
    "SELECT i.position FROM qb_set_items i JOIN fz_battle_players p ON p.set_id = i.set_id WHERE p.battle_id = $1 AND p.role = 'challenger' AND i.answered_at IS NOT NULL",
    [battleId],
  );
  expect(practised, "a void exam's saved answers still count as practice (plan section 3)").toEqual([{ position: 1 }]);
  expect(battleIdOf(bilal.page)).toBe(battleId);
});
