import type { Locator, Page, TestInfo } from "@playwright/test";

import { SMALL_PROGRAM } from "./env";
import {
  type Person,
  apiBefriend,
  apiJoin,
  axe,
  battleIdOf,
  bff,
  examUi,
  expect,
  focused,
  openFriendZone,
  playExamByApi,
  section,
  shot,
  startExam,
  test,
} from "./support";

/**
 * Phase 2 (J8-J10, plan section 13; CD16, CD17: shipped with Phase 1): the daily challenge, XP and level, badges,
 * stats on the player view, Rematch. XP and badges exist for the live cohort only (a QA account never earns any,
 * battle.friendzone.ts checks that), so these run with live accounts on the FRIEND_ZONE_MODE=on stack.
 */

const LIVE = { qa: false, stack: "on" as const };
const KEYS = ["A", "B", "C", "D"];

type Daily = {
  challengeId: string | null;
  available: boolean;
  question: { position: number } | null;
  attempt: { status: string; served: number } | null;
};

async function daily(person: Person, program = "networking-ai"): Promise<Daily> {
  const response = await bff(person, "GET", `/daily?program=${program}`);
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as Daily;
}

/** The whole daily through the API, answering `choice` each time (the friend whose result Asha then sees). */
async function playDailyByApi(person: Person, choice = "A"): Promise<void> {
  const challengeId = (await daily(person)).challengeId;
  if (!challengeId) throw new Error("no daily challenge today: seed.py maps too few questions");
  for (let position = 1; position <= 10; position++) {
    const served = await bff(person, "POST", `/daily/${challengeId}/next`);
    expect(served.status(), await served.text()).toBe(200);
    const answered = await bff(person, "POST", `/daily/${challengeId}/answers`, { position, choice });
    expect(answered.status(), await answered.text()).toBe(200);
  }
  const finished = await bff(person, "POST", `/daily/${challengeId}/next`);
  expect(finished.status(), await finished.text()).toBe(200);
  expect(((await finished.json()) as Daily).attempt?.status).toBe("finished");
}

const dailySection = (page: Page) => section(page, "Daily challenge");

async function dailyAnswer(page: Page, position: number, how: "click" | "digit" | "letter", choice = "A"): Promise<void> {
  const challenge = dailySection(page);
  const heading = challenge.getByRole("heading", { name: `Question ${position} of 10`, level: 4 });
  await expect(heading).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => focused(heading), { message: `focus on question ${position}` }).toBe(true);
  const option = challenge.getByRole("button", { name: new RegExp(`^${choice}\\. `) });
  if (how === "click") await option.click();
  else await page.keyboard.press(how === "digit" ? String(KEYS.indexOf(choice) + 1) : choice.toLowerCase());
  await expect(option).toHaveAttribute("aria-pressed", "true");
  await challenge.getByRole("button", { name: position === 10 ? "Finish" : "Next question" }).click();
}

/** Whatever state the daily is in, play on to the finish: answer an open question (A), press Next or Finish. */
async function playRest(page: Page): Promise<void> {
  const challenge = dailySection(page);
  const finished = challenge.getByRole("heading", { name: "Finished", level: 4 });
  const open = challenge.getByRole("button", { name: /^A\. /, pressed: false });
  const proceed = challenge.getByRole("button", { name: /^(Next question|Finish)$/ });
  // The screen moves under us (a press shows "Loading…", a window can close), so each action is short and retried.
  for (let step = 0; step < 60; step++) {
    await expect(finished.or(proceed).or(open).first()).toBeVisible({ timeout: 15_000 });
    if (await finished.isVisible()) return;
    const target = (await proceed.isVisible()) ? proceed : open;
    await target.click({ timeout: 2_000 }).catch(() => undefined);
    await expect(challenge.getByRole("button", { name: "Loading…" })).toHaveCount(0);
  }
  throw new Error("the daily challenge did not finish");
}

test("J8 daily challenge: start, answer by click and key, a reload resumes, the finish, friends' results, one attempt, XP", async ({ people }, testInfo) => {
  test.setTimeout(300_000);
  const asha = await people.add("Asha Menon", LIVE);
  const bilal = await people.add("Bilal Khan", LIVE);
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  await playDailyByApi(bilal);

  const { page } = asha;
  await openFriendZone(asha);
  const challenge = dailySection(page);
  await expect(challenge.getByText("Today's challenge: 10 questions from", { exact: false })).toBeVisible();
  await axe(page, testInfo, "daily-start");
  await challenge.getByRole("button", { name: "Start today's challenge" }).click();
  await dailyAnswer(page, 1, "click");
  await axe(page, testInfo, "daily-question");
  await dailyAnswer(page, 2, "digit", "B");
  await dailyAnswer(page, 3, "letter", "C");

  // A reload resumes where Asha was, never from the start (question 4 open, or its time ran out during the reload).
  await expect(challenge.getByRole("heading", { name: "Question 4 of 10" })).toBeVisible({ timeout: 15_000 });
  await openFriendZone(asha);
  await expect(
    challenge.getByRole("heading", { name: /^Question (4|5) of 10$/ }).or(challenge.getByText(/^Ready for question (4|5) of 10\./)),
  ).toBeVisible({ timeout: 15_000 });
  await expect(challenge.getByRole("button", { name: "Start today's challenge" })).toHaveCount(0);
  await shot(page, testInfo, "daily-resumed");
  await playRest(page);

  const finished = challenge.getByRole("heading", { name: "Finished", level: 4 });
  await expect(finished).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => focused(finished)).toBe(true);
  await expect(challenge.getByText(/^\d+ points · \d+ of 10 correct$/)).toBeVisible();
  await expect(challenge.getByText("Friends today")).toBeVisible();
  // Bilal keeps "Show my stats" off: his result reads Completed, without his score.
  const bilalToday = challenge.getByRole("listitem").filter({ hasText: "Bilal Khan" });
  await expect(bilalToday).toContainText("Completed");
  await expect(bilalToday).not.toContainText("points");
  const review = challenge.getByRole("region", { name: "Review" });
  await expect(review.getByText("Why:")).toHaveCount(10);
  await axe(page, testInfo, "daily-finished");

  // One attempt a day: the finish stays; and the XP arrived once.
  await openFriendZone(asha);
  await expect(dailySection(page).getByRole("heading", { name: "Finished" })).toBeVisible({ timeout: 15_000 });
  await expect(dailySection(page).getByRole("button", { name: "Start today's challenge" })).toHaveCount(0);
  const progress = section(page, "Your progress");
  await expect(progress.getByText("Daily challenge completed")).toHaveCount(1);
  await expect(progress.getByText("+25 XP")).toBeVisible();
});

test("J9-J10 a won battle: XP once, level, the First Battle badge, stats only when shown, and Rematch as a new invitation", async ({ people }, testInfo) => {
  test.setTimeout(300_000);
  const asha = await people.add("Asha Menon", LIVE);
  const bilal = await people.add("Bilal Khan", LIVE);
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);

  // An exam Asha wins (all right; Bilal all wrong, so both answered every question), played through the API (the UI
  // path is battle.friendzone.ts).
  const battleId = await startExam(asha, bilal);
  await openFriendZone(asha, `&battle=${battleId}`);
  await expect(examUi.heading(asha.page, 1, 10)).toBeVisible({ timeout: 20_000 });
  await playExamByApi(people, asha, bilal, battleId);
  await expect(asha.page.getByRole("heading", { name: "You won" })).toBeVisible({ timeout: 20_000 });
  // Your XP on the result (D7): +20 for the battle, +30 for the win.
  await expect(asha.page.getByText("Battle played", { exact: true })).toBeVisible();
  await expect(asha.page.getByText("Win", { exact: true })).toBeVisible();
  await expect(asha.page.getByText("+50 XP", { exact: true })).toBeVisible();

  // J10 Rematch: a new invitation to the same friend, a new battle, the friend's bell says so.
  const rematch = asha.page.getByRole("button", { name: "Rematch" });
  await expect(rematch).toBeVisible();
  await axe(asha.page, testInfo, "result-with-rematch");
  await rematch.click();
  await expect(asha.page.getByText("Waiting for Bilal Khan to accept.", { exact: false })).toBeVisible({ timeout: 15_000 });
  const second = battleIdOf(asha.page);
  expect(second).not.toBe(battleId);
  const invites = await people.sql<{ title: string; link: string }>("SELECT title, link FROM user_notifications WHERE user_id = $1 AND link LIKE '%' || $2 || '%'", [bilal.userId, second]);
  expect(invites.map((row) => row.title)).toEqual(["Rematch invitation"]);
  await shot(asha.page, testInfo, "rematch-invited");
  expect((await bff(asha, "POST", `/battles/${second}/cancel`)).status()).toBe(200);

  // J9 XP and level, once: battle +20, win +30 = 50 XP, level 1 (level 2 starts at 100).
  await openFriendZone(asha);
  const progress = section(asha.page, "Your progress");
  await expect(progress.getByText("Battle completed")).toHaveCount(1);
  await expect(progress.getByText("Battle won")).toHaveCount(1);
  await expect(progress.getByRole("progressbar", { name: "Level 1 progress" })).toHaveAttribute("aria-valuetext", "50 / 100 XP to level 2");
  await expect(progress.getByText("First Battle")).toBeVisible();
  await expect(progress.getByText("1 battle: 1 win, 0 losses, 0 draws")).toBeVisible();
  await axe(asha.page, testInfo, "your-progress");

  // Stats on the player view: hidden by default, shown once Bilal turns on "Show my stats".
  const friends = section(asha.page, /^Friends/);
  await friends.getByRole("button", { name: "Bilal Khan", exact: true }).click();
  await expect(friends.getByText("Program hidden")).toBeVisible();
  await expect(friends.getByText(/battles?: /)).toHaveCount(0);
  const me = (await (await bff(bilal, "GET", "/me")).json()) as { profile: { version: number } };
  expect((await bff(bilal, "PATCH", "/me", { version: me.profile.version, set: { showStats: true } })).status()).toBe(200);
  await openFriendZone(asha);
  await section(asha.page, /^Friends/).getByRole("button", { name: "Bilal Khan", exact: true }).click();
  await expect(section(asha.page, /^Friends/).getByText("1 battle: 0 wins, 1 loss, 0 draws")).toBeVisible();
  await expect(section(asha.page, /^Friends/).getByRole("progressbar")).toBeVisible();
  await axe(asha.page, testInfo, "friend-stats");
});

test("J8 no daily challenge today in a program with fewer than ten checked questions", async ({ people }, testInfo) => {
  const asha = await people.add("Asha Menon", { programs: [SMALL_PROGRAM] });
  await apiJoin(asha, true);
  const today = await daily(asha, SMALL_PROGRAM);
  expect(today).toMatchObject({ available: false, challengeId: null });
  await openFriendZone(asha, "", SMALL_PROGRAM);
  const challenge = dailySection(asha.page);
  await expect(
    challenge.getByText(/^No daily challenge today\. There aren't enough checked questions in .+ for one yet; Battles and Practice are still here\.$/),
  ).toBeVisible();
  await expect(challenge.getByRole("button", { name: "Start today's challenge" })).toHaveCount(0);
  await axe(asha.page, testInfo, "daily-unavailable");
});

test("J8 a daily answered fewer than half: it finishes, but gives no XP and no completion notice", async ({ people }, testInfo) => {
  test.setTimeout(300_000);
  const asha = await people.add("Asha Menon", LIVE);
  await apiJoin(asha, true);
  const { page } = asha;
  await openFriendZone(asha);
  const challenge = dailySection(page);
  await challenge.getByRole("button", { name: "Start today's challenge" }).click();
  for (let position = 1; position <= 4; position++) await dailyAnswer(page, position, "click");
  // Questions 5 to 9: served, left unanswered until their time runs out, then Next.
  for (let position = 5; position <= 9; position++) {
    await expect(challenge.getByRole("heading", { name: `Question ${position} of 10`, level: 4 })).toBeVisible({ timeout: 15_000 });
    await expect(challenge.getByText(`Ready for question ${position + 1} of 10.`, { exact: false })).toBeVisible({ timeout: 20_000 });
    await challenge.getByRole("button", { name: "Next question" }).click();
  }
  // The last one runs out too: the attempt is finished then (or by Finish, if the screen asks for it first).
  await expect(challenge.getByRole("heading", { name: "Question 10 of 10", level: 4 })).toBeVisible({ timeout: 15_000 });
  const finished = challenge.getByRole("heading", { name: "Finished", level: 4 });
  const finish = challenge.getByRole("button", { name: "Finish" });
  await expect(finished.or(finish)).toBeVisible({ timeout: 20_000 });
  if (await finish.isVisible()) await finish.click();
  await expect(finished).toBeVisible({ timeout: 15_000 });
  await expect(challenge.getByText(/^\d+ points · [0-4] of 10 correct$/)).toBeVisible();
  const review = challenge.getByRole("region", { name: "Review" });
  await expect(review.getByText("You didn't answer this one.")).toHaveCount(6);
  await axe(page, testInfo, "daily-under-half");

  expect(await people.sql("SELECT reason FROM fz_xp_events WHERE user_id = $1", [asha.userId])).toEqual([]);
  expect(await people.sql("SELECT kind FROM user_notifications WHERE user_id = $1 AND kind = 'daily_completed'", [asha.userId])).toEqual([]);
  await openFriendZone(asha);
  await expect(section(page, "Your progress").getByText("No XP yet.", { exact: false })).toBeVisible();
  await expect(section(page, "Your progress").getByText("0 completed")).toBeVisible(); // Daily challenges
});

/** Whether `item` lies wholly inside `box`'s visible area (1 px of rounding allowed). */
async function insideBox(box: Locator, item: Locator): Promise<boolean> {
  const [outer, inner] = await Promise.all([box.boundingBox(), item.boundingBox()]);
  return Boolean(outer && inner && inner.y >= outer.y - 1 && inner.y + inner.height <= outer.y + outer.height + 1);
}

/**
 * The bell's list overflows its scroll box, and the keyboard still reaches every notice: End focuses the last one and
 * scrolls it into view; Home goes back to the first item, and ArrowDown on to the first notice, scrolled back too.
 */
async function expectNoticesReachedByKeys(page: Page, menu: Locator, testInfo: TestInfo, label: string): Promise<void> {
  const scroller = menu.locator(".overflow-y-auto");
  const notices = scroller.getByRole("menuitem");
  await expect(notices.first()).toBeVisible();
  const scroll = () => scroller.evaluate((element) => ({ height: element.scrollHeight, visible: element.clientHeight, top: element.scrollTop }));
  const before = await scroll();
  expect(before.height, `the list overflows its box (${JSON.stringify(before)})`).toBeGreaterThan(before.visible);
  expect(before.top).toBe(0);
  await expect.poll(() => insideBox(scroller, notices.last()), { message: "the last notice starts out of view" }).toBe(false);

  await page.keyboard.press("End");
  await expect(notices.last()).toBeFocused();
  await expect.poll(() => insideBox(scroller, notices.last()), { message: "End scrolls the last notice into view" }).toBe(true);
  expect((await scroll()).top, "the box scrolled").toBeGreaterThan(0);
  await shot(page, testInfo, `bell-${label}-end`);

  await page.keyboard.press("Home");
  const first = menu.getByRole("menuitem").first();
  await expect(first).toBeFocused();
  await expect(first).toBeInViewport();
  for (let presses = 0; presses < 5 && !(await notices.first().evaluate((element) => element === document.activeElement)); presses++) {
    await page.keyboard.press("ArrowDown");
  }
  await expect(notices.first()).toBeFocused();
  await expect.poll(() => insideBox(scroller, notices.first()), { message: "the first notice is back in view" }).toBe(true);
  await shot(page, testInfo, `bell-${label}-home`);
}

/** Opens the bell from the chat home and chooses the notification titled `title`: it must land on Friend Zone. */
async function openFromBell(page: Page, title: RegExp, testInfo: TestInfo, label: string): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: /^Notifications, \d+ unread$/ }).click();
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  await expectNoticesReachedByKeys(page, menu, testInfo, label);
  // The bell is shared app UI (features/notifications), not Friend Zone: axe's finding on its scroll box is recorded.
  await axe(page, testInfo, `bell-${label}`, "[role='menu']", true, {
    "scrollable-region-focusable":
      "roving-focus menu: items are reached with arrow keys and scroll into view (asserted above); Tab is reserved by the menu pattern",
  });
  await menu.getByRole("menuitem", { name: title }).click();
  await page.waitForURL(/\/programs\/networking-ai\/quizzes\?zone=friends(&|$)/);
  await expect(page.getByRole("button", { name: "Friend Zone", pressed: true })).toBeVisible();
}

test("J9 a 10-question battle all right gives Perfect Score; a second win reaches Level 2; the bell's level and badge notices open Friend Zone", async ({
  people,
}, testInfo) => {
  test.setTimeout(480_000);
  const asha = await people.add("Asha Menon", LIVE);
  const bilal = await people.add("Bilal Khan", LIVE);
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  const { page } = asha;

  // A 10-question exam, the challenge dialog's default.
  await openFriendZone(asha);
  const friends = section(page, /^Friends/);
  await friends.getByRole("button", { name: "Bilal Khan", exact: true }).click();
  await friends.getByRole("button", { name: "Challenge" }).click();
  const dialog = page.getByRole("dialog", { name: "Challenge a friend" });
  await expect(dialog.getByRole("radio", { name: "10 questions · 10 min" })).toBeChecked();
  await dialog.getByRole("button", { name: "Send challenge" }).click();
  await expect(page.getByText("Mixed · 10 questions", { exact: false })).toBeVisible();
  const first = battleIdOf(page);
  expect((await bff(bilal, "POST", `/battles/${first}/accept`)).status()).toBe(200);
  await page.getByRole("button", { name: "Ready", exact: true }).click({ timeout: 15_000 });
  await expect(examUi.heading(page, 1, 10)).toBeVisible({ timeout: 20_000 });
  await playExamByApi(people, asha, bilal, first);
  await expect(page.getByRole("heading", { name: "You won", level: 4 })).toBeVisible({ timeout: 20_000 });
  const scores = page.getByRole("table", { name: "Scores" });
  await expect(scores.getByRole("row", { name: /^Marks/ })).toHaveText(/Marks\s*40\s*−10/);
  await expect(scores.getByRole("row", { name: /^Correct/ })).toHaveText(/Correct\s*10\s*0/);
  await expect(page.getByRole("region", { name: "Review" }).getByText("Why:")).toHaveCount(10);
  await axe(page, testInfo, "result-10-questions");

  // A second win, a rematch-free new exam: 2 x (20 + 30) = 100 XP, Level 2 (2 battles a day with one friend: both count).
  await playExamByApi(people, asha, bilal, await startExam(asha, bilal));
  const total = await people.sql<{ sum: string }>("SELECT sum(amount)::text AS sum FROM fz_xp_events WHERE user_id = $1", [asha.userId]);
  expect(total[0]?.sum).toBe("100");

  const bells = await people.sql<{ title: string; body: string }>(
    "SELECT title, body FROM user_notifications WHERE user_id = $1 AND kind IN ('level_up', 'badge_unlocked') ORDER BY title",
    [asha.userId],
  );
  expect(bells.map((bell) => bell.title)).toEqual(["Badge earned: First Battle", "Badge earned: Perfect Score", "Level 2"]);
  for (const bell of bells) expect(`${bell.title} ${bell.body}`).not.toMatch(/Bilal|Asha|FZE2E|@/);

  // Through the real bell: each notice opens Quizzes & Games on the Friend Zone side, where the change shows.
  await openFromBell(page, /^Level 2/, testInfo, "level");
  const progress = section(page, "Your progress");
  await expect(progress.getByRole("progressbar", { name: "Level 2 progress" })).toHaveAttribute("aria-valuetext", "0 / 200 XP to level 3");
  await openFromBell(page, /^Badge earned: Perfect Score/, testInfo, "badge");
  await expect(progress.getByText("Perfect Score", { exact: true })).toBeVisible();
  await expect(progress.getByText("First Battle", { exact: true })).toBeVisible();
  await axe(page, testInfo, "progress-level-2");
});
