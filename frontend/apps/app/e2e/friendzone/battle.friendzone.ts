import { ROUND_SECONDS } from "./env";
import {
  type People,
  type Person,
  type Stack,
  apiBefriend,
  apiDirect,
  apiJoin,
  axe,
  battleIdOf,
  battleState,
  bff,
  correctKeys,
  createBattle,
  examStarted,
  examUi,
  expect,
  focused,
  legacyRoundBattle,
  openFriendZone,
  pick,
  saveResponses,
  savedRows,
  section,
  setItem,
  shot,
  sleep,
  startExam,
  submitExam,
  test,
  timerSeconds,
  wrongOf,
} from "./support";

/**
 * The exam battle (Friend Challenge plan Rev 2: sections 2-6, 9; D1-D8): one paper, one shared deadline of
 * length × FRIEND_ZONE_ROUND_SECONDS (an e2e-only override of the production 60 s per question), free navigation with
 * a palette, answers saved as they change and editable until Submit or the deadline. Two signed-in browsers on the
 * FRIEND_ZONE_MODE=qa stack. The server is the only judge: keys come from the database only to answer right or wrong
 * on purpose.
 */

const UNSAVED_MESSAGE = "You have unsaved changes. Leave without saving them?"; // features/navigation/navigation-guard.ts

async function friends(people: People, stack: Stack = "qa") {
  const asha = await people.add("Asha Menon", { stack });
  const bilal = await people.add("Bilal Khan", { stack });
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  return { asha, bilal };
}

/** Each pill's accessible name, in order (the palette's state words, plan section 6). */
async function pillNames(person: Person, total: number): Promise<string[]> {
  const names: string[] = [];
  for (let round = 1; round <= total; round++) names.push((await examUi.pill(person.page, round).getAttribute("aria-label")) ?? "");
  return names;
}

test("exam end to end in two browsers: challenge, accept, Ready, navigate, visit, mark, change, clear, refresh, offline, an early submit, the auto-submit at the deadline, result and review", async ({
  people,
}, testInfo) => {
  test.setTimeout(420_000);
  const total = 20; // the 20-question length, chosen in the dialog: 20 × ROUND_SECONDS on one timer
  const { asha, bilal } = await friends(people);

  // Asha challenges Bilal: the dialog offers 10, 20 and 30 questions at one minute each (D4), 10 by default.
  await openFriendZone(asha);
  const list = section(asha.page, /^Friends/);
  await list.getByRole("button", { name: "Bilal Khan", exact: true }).click();
  await list.getByRole("button", { name: "Challenge" }).click();
  const dialog = asha.page.getByRole("dialog", { name: "Challenge a friend" });
  await expect(dialog.getByLabel(/^Topic/)).toBeEnabled(); // the month's topics have loaded
  await expect(dialog.getByRole("radio", { name: "10 questions · 10 min" })).toBeChecked();
  await expect(dialog.getByRole("radio", { name: "30 questions · 30 min" })).toBeVisible();
  await dialog.getByRole("radio", { name: "20 questions · 20 min" }).check();
  await axe(asha.page, testInfo, "challenge-dialog", "[role='dialog']");
  await dialog.getByRole("button", { name: "Send challenge" }).click();
  await expect(asha.page.getByText("Waiting for Bilal Khan to accept.", { exact: false })).toBeVisible();
  await expect(asha.page.getByRole("heading", { name: "How it works", level: 4 })).toBeVisible();
  const battleId = battleIdOf(asha.page);
  await axe(asha.page, testInfo, "battle-invited-out");

  // Bilal accepts from his Battles list; both read "How it works" before the start.
  await openFriendZone(bilal);
  const battles = section(bilal.page, "Battles");
  await expect(battles.getByText(`Challenged you · Month 1 · ${total} questions`)).toBeVisible();
  await battles.getByRole("button", { name: "Accept and play" }).click();
  await expect(bilal.page.getByText("You're in. Waiting for Asha Menon to start the battle.", { exact: false })).toBeVisible();
  await expect(bilal.page.getByRole("heading", { name: "How it works", level: 4 })).toBeVisible();
  await axe(bilal.page, testInfo, "battle-waiting-start");
  await expect(asha.page.getByText("Bilal Khan accepted. Press Ready to start", { exact: false })).toBeVisible({ timeout: 10_000 });
  await asha.page.getByRole("button", { name: "Ready", exact: true }).click();
  await expect(asha.page.getByText("Get ready")).toBeVisible();

  // The exam starts for both at once: focus on the question heading, one countdown that starts at most at the deadline.
  for (const person of [asha, bilal]) {
    const first = examUi.heading(person.page, 1, total);
    await expect(first).toBeVisible({ timeout: 20_000 });
    await expect.poll(() => focused(first)).toBe(true);
    expect(await timerSeconds(person.page)).toBeLessThanOrEqual(total * ROUND_SECONDS);
    await expect(examUi.pill(person.page, total)).toBeVisible(); // the palette has every question (inline at 1440 px)
  }
  const paper = await battleState(asha, battleId);
  expect(paper.question, "an exam never sends a round question").toBeNull();
  expect(paper.review ?? null).toBeNull();
  expect(paper.rounds).toEqual([]);
  await axe(bilal.page, testInfo, "exam-question", undefined, false);
  const keys = await correctKeys(people, battleId);
  const key = (round: number) => keys[round - 1] ?? "A";

  // Asha: Q1 right (click); Q2 wrong, then changed to right; Q3 marked without an answer; Q4 right by letter and
  // marked (counts, D6); Q5 picked, then cleared; Q6 only visited; Q9 reached from the palette, right by digit.
  const a = asha.page;
  await pick(a, key(1));
  await expect(examUi.saved(a)).toBeVisible();
  await examUi.action(a, "Save & Next").click();
  await expect(examUi.heading(a, 2, total)).toBeVisible();
  await pick(a, wrongOf(key(2)));
  await pick(a, key(2));
  await examUi.action(a, "Save & Next").click();
  await examUi.action(a, "Mark for Review & Next").click();
  await expect(examUi.heading(a, 4, total)).toBeVisible();
  await examUi.heading(a, 4, total).focus(); // the letter shortcut works with focus anywhere in the exam
  await pick(a, key(4), "letter");
  await examUi.action(a, "Mark for Review & Next").click();
  await pick(a, wrongOf(key(5)));
  await examUi.action(a, "Clear Response").click();
  for (const option of ["A", "B", "C", "D"]) await expect(examUi.option(a, option)).not.toBeChecked();
  await examUi.action(a, "Save & Next").click();
  await expect(examUi.heading(a, 6, total)).toBeVisible();
  await examUi.pill(a, 9).click();
  await expect(examUi.heading(a, 9, total)).toBeVisible();
  await examUi.heading(a, 9, total).focus();
  await pick(a, key(9), "digit");
  await expect(examUi.saved(a)).toBeVisible();
  expect(new URL(a.url()).searchParams.get("q"), "the current question is in the address").toBe("9");
  const ashaPills = [
    "Question 1, answered",
    "Question 2, answered",
    "Question 3, marked for review",
    "Question 4, answered and marked for review",
    "Question 5, not answered",
    "Question 6, not answered",
    "Question 7, not visited",
    "Question 8, not visited",
    "Question 9, answered",
    "Question 10, not visited",
  ];
  expect((await pillNames(asha, total)).slice(0, 10)).toEqual(ashaPills);
  await expect(a.getByText("Answered and marked for review", { exact: true }).first()).toBeVisible(); // the legend
  await shot(a, testInfo, "palette-asha");

  // Bilal, at the same time and independently: Q1 wrong by digit; Q2 right and marked; jumps to Q10, right; Previous.
  const b = bilal.page;
  await examUi.heading(b, 1, total).focus();
  await pick(b, wrongOf(key(1)), "digit");
  await examUi.action(b, "Save & Next").click();
  await pick(b, key(2));
  await examUi.action(b, "Mark for Review & Next").click();
  await expect(examUi.heading(b, 3, total)).toBeVisible();
  await examUi.pill(b, 10).click();
  await pick(b, key(10));
  await examUi.action(b, "Previous").click();
  await expect(examUi.heading(b, 9, total)).toBeVisible();
  await expect(examUi.saved(b)).toBeVisible();
  expect((await pillNames(bilal, total)).slice(0, 10)).toEqual([
    "Question 1, answered",
    "Question 2, answered and marked for review",
    "Question 3, not answered",
    "Question 4, not visited",
    "Question 5, not visited",
    "Question 6, not visited",
    "Question 7, not visited",
    "Question 8, not visited",
    "Question 9, not answered",
    "Question 10, answered",
  ]);
  // Neither palette shows the other's work; the server holds exactly what each screen shows.
  expect((await pillNames(asha, total)).slice(0, 10)).toEqual(ashaPills);
  await expect.poll(() => savedRows(people, battleId, "challenger")).toEqual([
    { round: 1, choice: key(1), marked: false },
    { round: 2, choice: key(2), marked: false },
    { round: 3, choice: null, marked: true },
    { round: 4, choice: key(4), marked: true },
    { round: 5, choice: null, marked: false },
    { round: 6, choice: null, marked: false },
    { round: 9, choice: key(9), marked: false },
  ]);
  await expect.poll(() => savedRows(people, battleId, "opponent")).toEqual([
    { round: 1, choice: wrongOf(key(1)), marked: false },
    { round: 2, choice: key(2), marked: true },
    { round: 3, choice: null, marked: false },
    { round: 9, choice: null, marked: false },
    { round: 10, choice: key(10), marked: false },
  ]);

  // A refresh mid-exam keeps the answers, the deadline (the countdown never goes back up) and the question (&q=9).
  const before = await timerSeconds(a);
  await a.reload();
  await expect(examUi.heading(a, 9, total)).toBeVisible({ timeout: 20_000 });
  await expect(examUi.option(a, key(9))).toBeChecked();
  expect((await pillNames(asha, total)).slice(0, 10)).toEqual(ashaPills);
  expect(await timerSeconds(a)).toBeLessThanOrEqual(before);

  // Offline: Bilal's change is shown at once, marked Not saved; leaving inside the page asks first; back online, it saves.
  await bilal.context.setOffline(true);
  await pick(b, wrongOf(key(9)));
  await expect(b.getByText("Offline: 1 not saved", { exact: true })).toBeVisible();
  await expect(examUi.pill(b, 9)).toHaveAttribute("aria-label", "Question 9, answered, not saved");
  await shot(b, testInfo, "offline-not-saved");
  const asked = new Promise<string>((resolve) => {
    b.once("dialog", async (prompt) => {
      resolve(prompt.message());
      await prompt.dismiss();
    });
  });
  await b.getByRole("button", { name: "Back to Friend Zone" }).click();
  expect(await asked).toBe(UNSAVED_MESSAGE);
  await expect(examUi.heading(b, 9, total)).toBeVisible(); // stayed
  await bilal.context.setOffline(false);
  await expect(examUi.saved(b)).toBeVisible({ timeout: 15_000 });
  await expect(examUi.pill(b, 9)).toHaveAttribute("aria-label", "Question 9, answered");
  expect((await savedRows(people, battleId, "opponent")).find((row) => row.round === 9)?.choice).toBe(wrongOf(key(9)));

  // Bilal submits early: the confirmation shows the time left and the counts, Keep answering has focus.
  await examUi.action(b, "Submit").click();
  const confirm = examUi.submitDialog(b);
  await expect(confirm).toBeVisible();
  await expect.poll(() => focused(confirm.getByRole("button", { name: "Keep answering" }))).toBe(true);
  await expect(confirm).toContainText(/\d+:\d{2} left\. Answered 4 \(1 of them marked for review\), not answered 1, marked for review without an answer 0, not visited 15\./);
  await axe(b, testInfo, "submit-dialog", "[role='alertdialog']");
  await confirm.getByRole("button", { name: "Keep answering" }).click();
  await expect(confirm).toHaveCount(0);
  await examUi.action(b, "Submit").click();
  await examUi.submitDialog(b).getByRole("button", { name: "Submit", exact: true }).click();
  const waiting = examUi.waiting(b);
  await expect(waiting).toBeVisible();
  await expect.poll(() => focused(waiting)).toBe(true);
  await expect(examUi.opponentStatus(b, "Asha Menon", "In progress")).toBeVisible();
  await axe(b, testInfo, "submitted-waiting");
  expect((await saveResponses(bilal, battleId, [setItem(11, "A")])).status(), "no save after my own submit").toBe(409);

  // Asha carries on: Bilal shows as Submitted (D4: nothing more), said once in the live region.
  await expect(examUi.opponentStatus(a, "Bilal Khan", "Submitted")).toBeVisible({ timeout: 15_000 });
  await expect(a.getByRole("status").filter({ hasText: /^Bilal Khan has submitted\.$/ }).first()).toBeAttached();
  await examUi.pill(a, 10).click();
  await pick(a, key(10));
  await examUi.action(a, "Save & Next").click();
  await expect(examUi.heading(a, 11, total)).toBeVisible();

  // The deadline: a pick whose save is still on its way when time runs out goes in with the auto-submit (+0.5 s).
  // The pick is made with 3-4 s left (a headed window can stall a click for over a second, so 2 s left was too tight),
  // and its save is held 5 s, so it still reaches the server only after the deadline.
  await expect.poll(() => timerSeconds(a), { timeout: total * ROUND_SECONDS * 1000, intervals: [250] }).toBeLessThanOrEqual(4);
  await a.route("**/api/v1/friend-zone/battles/*/responses", async (route) => {
    await sleep(5_000);
    await route.continue().catch(() => undefined);
  });
  // The auto-submit itself is held 1 s (it still lands inside the allowance), so the time-up screen can be seen.
  await a.route("**/api/v1/friend-zone/battles/*/submit", async (route) => {
    await sleep(1_000);
    await route.continue().catch(() => undefined);
  });
  await pick(a, key(11));
  const timeUp = examUi.timeUp(a);
  await expect(timeUp).toBeVisible({ timeout: 10_000 });
  await expect.poll(() => focused(timeUp)).toBe(true);
  await shot(a, testInfo, "time-up");

  // The result, focused, for both: marks +4/−1, time taken, the review with both picks, no XP (a QA account).
  const won = a.getByRole("heading", { name: "You won", level: 4 });
  const lost = b.getByRole("heading", { name: "You lost", level: 4 });
  await expect(won).toBeVisible({ timeout: 20_000 });
  await expect(lost).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => focused(won)).toBe(true);
  await expect(a.getByText("Final marks 24 to 6.", { exact: true })).toBeVisible();
  const scores = a.getByRole("table", { name: "Scores" });
  const row = (label: string) => scores.getByRole("row", { name: new RegExp(`^${label}`) });
  await expect(row("Marks")).toHaveText(/Marks\s*24\s*6/);
  await expect(row("Correct")).toHaveText(/Correct\s*6\s*2/);
  await expect(row("Incorrect")).toHaveText(/Incorrect\s*0\s*2/);
  await expect(row("Unanswered")).toHaveText(/Unanswered\s*14\s*16/);
  await expect(row("Accuracy")).toHaveText(/Accuracy\s*100%\s*50%/);
  await expect(row("Time taken")).toContainText("Time ran out");
  for (const page of [a, b]) {
    await expect(page.getByRole("heading", { name: "Your XP", level: 5 })).toBeVisible();
    await expect(page.getByText("No XP for this battle.", { exact: true })).toBeVisible();
  }
  const review = a.getByRole("region", { name: "Review" });
  await expect(review.getByRole("listitem").filter({ hasText: /^Question \d+/ })).toHaveCount(total);
  await expect(review.getByText("Why:")).toHaveCount(total);
  const eleventh = review.getByRole("listitem").filter({ hasText: /^Question 11/ });
  await expect(eleventh.getByText(`You: ${key(11)}, correct`, { exact: true })).toBeVisible(); // the auto-submitted pick
  await expect(eleventh.getByText("Bilal Khan: not answered", { exact: true })).toBeVisible();
  await axe(a, testInfo, "exam-result-won");
  await axe(b, testInfo, "exam-result-lost");
  const [state] = await people.sql<{ status: string; result: string; format: string }>("SELECT status, result, format FROM fz_battles WHERE id = $1", [battleId]);
  expect(state).toEqual({ status: "completed", result: "points", format: "exam" });
  expect(await people.sql("SELECT 1 FROM fz_battle_responses WHERE battle_id = $1", [battleId]), "the drafts are gone once graded").toEqual([]);
  expect(await people.sql("SELECT 1 FROM fz_xp_events WHERE user_id = ANY($1)", [[asha.userId, bilal.userId]])).toEqual([]);

  // Back to Friend Zone: focus on Battles, and the battle in the history.
  await a.unrouteAll({ behavior: "ignoreErrors" });
  await a.getByRole("button", { name: "Back to Friend Zone" }).click();
  await expect.poll(() => focused(a.getByRole("heading", { name: "Battles", level: 3 }))).toBe(true);
  await expect(section(a, "Battles").getByText(`Won · Month 1 · ${total} questions`)).toBeVisible();
  await expect(section(a, "Your progress").getByText("No XP yet.", { exact: false })).toBeVisible();
});

test("exam timing and secrecy through the API: 10, 20 and 30 questions, no paper before the start, compare-and-set saves, a save inside the 2-second allowance is graded, and the old answers route is closed", async ({
  people,
}) => {
  test.setTimeout(300_000);
  const { asha, bilal } = await friends(people);
  for (const length of [30, 20, 10]) {
    const battleId = await startExam(asha, bilal, length);
    const early = await battleState(asha, battleId);
    expect(Date.parse(early.serverTime), "read during the 3-second countdown").toBeLessThan(Date.parse(early.startsAt ?? ""));
    expect(early.exam, "no paper during the countdown").toBeNull();

    const state = await examStarted(asha, battleId);
    const exam = state.exam;
    if (!exam) throw new Error("no exam");
    expect(state).toMatchObject({ format: "exam", length, status: "active", question: null, review: null, rounds: [] });
    expect(Object.keys(exam).sort()).toEqual(["cutoffAt", "endsAt", "opponentSubmitted", "questions", "responses", "secondsPerQuestion", "submittedAt"]);
    expect(exam.secondsPerQuestion).toBe(ROUND_SECONDS);
    expect(Date.parse(exam.endsAt) - Date.parse(state.startsAt ?? "")).toBe(length * ROUND_SECONDS * 1000);
    expect(Date.parse(exam.cutoffAt) - Date.parse(exam.endsAt), "the 2-second network allowance (D8)").toBe(2000);
    expect(exam.questions.map((question) => question.round)).toEqual(Array.from({ length }, (_, index) => index + 1));
    for (const question of exam.questions) expect(Object.keys(question).sort()).toEqual(["options", "round", "stem"]);
    expect(exam.responses, "question 1 is visited at the start").toEqual([{ round: 1, choice: null, marked: false, rev: 1 }]);
    const theirs = await battleState(bilal, battleId);
    expect(theirs.exam?.questions, "the same paper, in the same order (D4)").toEqual(exam.questions);
    if (length !== 10) {
      // Both submit with nothing answered: the exam ends at once, with no result.
      for (const person of [asha, bilal]) expect((await submitExam(person, battleId)).status()).toBe(200);
      expect(await battleState(asha, battleId)).toMatchObject({ status: "completed", result: "void", endReason: "absent" });
      continue;
    }

    // Compare-and-set on the server's rev: a stale or replayed item is not applied; a Clear can't be undone by a late pick.
    const keys = await correctKeys(people, battleId);
    const key = (round: number) => keys[round - 1] ?? "A";
    const items = async (person: Person, sent: ReturnType<typeof setItem>[]) => {
      const response = await saveResponses(person, battleId, sent);
      expect(response.status(), await response.text()).toBe(200);
      return ((await response.json()) as { items: { round: number; applied: boolean; rev: number }[] }).items;
    };
    expect(await items(asha, [setItem(1, key(1), false, 1)])).toEqual([{ round: 1, applied: true, rev: 2 }]);
    expect(await items(asha, [setItem(1, key(1), false, 1)]), "a replay").toEqual([{ round: 1, applied: false, rev: 2 }]);
    expect(await items(asha, [setItem(1, null, false, 2)]), "Clear").toEqual([{ round: 1, applied: true, rev: 3 }]);
    expect(await items(asha, [setItem(1, wrongOf(key(1)), false, 2)]), "a late pick after the Clear").toEqual([{ round: 1, applied: false, rev: 3 }]);
    expect(await items(asha, [setItem(1, key(1), false, 3)])).toEqual([{ round: 1, applied: true, rev: 4 }]);
    expect(await items(asha, [setItem(5, key(5), true, 0)]), "a set on a question never visited").toEqual([{ round: 5, applied: true, rev: 1 }]);
    expect(await items(asha, [{ round: 5, op: "visit" }]), "a visit never changes a row (its rev stays)").toEqual([{ round: 5, applied: true, rev: 1 }]);
    expect((await saveResponses(asha, battleId, [setItem(3, "H")])).status(), "a choice the question doesn't have").toBe(409);
    expect((await battleState(asha, battleId)).exam?.responses).toEqual([
      { round: 1, choice: key(1), marked: false, rev: 4 },
      { round: 5, choice: key(5), marked: true, rev: 1 },
    ]);

    // Secrecy while it runs: the other side's scores are masked, and the old one-tap route grades nothing.
    const masked = await battleState(bilal, battleId);
    expect(masked.opponent).toMatchObject({ score: 0, correct: 0, answered: 0, winner: false });
    expect((await apiDirect(asha, `/battles/${battleId}/answers`, "POST", { round: 1, choice: key(1) })).status, "the API keeps the route, closed").toBe(409);
    expect((await bff(asha, "POST", `/battles/${battleId}/answers`, { round: 1, choice: key(1) })).status(), "not proxied any more").toBe(404);

    // Bilal submits early (with nothing answered): a save after that is refused; Asha sees only "Submitted".
    expect((await submitExam(bilal, battleId)).status()).toBe(200);
    expect((await saveResponses(bilal, battleId, [setItem(2, "A")])).status()).toBe(409);
    expect((await battleState(asha, battleId)).exam?.opponentSubmitted).toBe(true);

    // Between the deadline and the cutoff: a read doesn't end the exam, and a save is accepted (and later graded).
    const read = await battleState(asha, battleId);
    await sleep(Date.parse(read.exam?.endsAt ?? "") - Date.parse(read.serverTime) + 300);
    expect((await battleState(asha, battleId)).status, "not completed inside the allowance").toBe("active");
    expect(await items(asha, [setItem(2, key(2), false, 0)]), "a save inside the allowance").toEqual([{ round: 2, applied: true, rev: 1 }]);
    await sleep(2_200);
    expect((await saveResponses(asha, battleId, [setItem(3, key(3), false, 0)])).status(), "after the cutoff").toBe(409);

    // Past the cutoff, any read completes it: Asha never submitted, so her time is the whole exam.
    const done = await battleState(asha, battleId);
    expect(done).toMatchObject({ status: "completed", result: "points" });
    expect(done.me).toMatchObject({ correct: 3, answered: 3, score: 12, winner: true, timeTakenSeconds: length * ROUND_SECONDS });
    expect(done.opponent).toMatchObject({ correct: 0, answered: 0, score: 0, winner: false });
    expect(done.review?.filter((item) => item.myChoice !== null).map((item) => item.round)).toEqual([1, 2, 5]);
    expect(done.xp, "a QA account earns no XP").toEqual([]);
    const late = await submitExam(asha, battleId, [setItem(4, key(4))], true);
    expect(late.status(), "an auto-submit after completion is answered, not refused").toBe(200);
    expect(((await late.json()) as { me: { answered: number } }).me.answered).toBe(3);
    expect(await people.sql("SELECT 1 FROM fz_battle_responses WHERE battle_id = $1", [battleId])).toEqual([]);
  }
  // Five questions is a round battle's length: a new battle is 10, 20 or 30 (O4).
  const five = await bff(asha, "POST", "/battles", { opponentId: bilal.playerId, program: "networking-ai", month: 1, length: 5, requestId: crypto.randomUUID() });
  expect(five.status()).toBe(422);
});

test("a legacy round battle in the history still renders: its line, its result in points and its review", async ({ people }, testInfo) => {
  const { asha, bilal } = await friends(people);
  const battleId = await legacyRoundBattle(people, asha, bilal);
  await openFriendZone(bilal);
  await expect(section(bilal.page, "Battles").getByText("Lost · Month 1 · 5 questions")).toBeVisible();
  await openFriendZone(asha);
  const line = section(asha.page, "Battles").getByRole("listitem").filter({ hasText: "Won · Month 1 · 5 questions" });
  await expect(line).toBeVisible();
  await line.getByRole("button", { name: "View" }).click();
  await expect(asha.page.getByRole("heading", { name: "You won", level: 4 })).toBeVisible();
  expect(battleIdOf(asha.page)).toBe(battleId);
  await expect(asha.page.getByText("Final score 640 to 210.", { exact: true })).toBeVisible();
  const scores = asha.page.getByRole("table", { name: "Scores" });
  await expect(scores.getByRole("row", { name: /^Score/ })).toHaveText(/Score\s*640\s*210/);
  await expect(scores.getByRole("row", { name: /^Correct/ })).toContainText("5 of 5");
  await expect(scores.getByRole("row", { name: /^Marks/ })).toHaveCount(0); // not an exam's marks
  await expect(asha.page.getByRole("region", { name: "Review" }).getByRole("listitem").filter({ hasText: /^Question \d/ })).toHaveCount(5);
  await expect(asha.page.getByRole("button", { name: "Rematch" })).toBeVisible();
  await axe(asha.page, testInfo, "legacy-round-result");
});

test("J5 an invitation can be declined, and a challenge cancelled, with the ending shown to both", { tag: "@webkit" }, async ({ people }, testInfo) => {
  const { asha, bilal } = await friends(people);

  const declined = await createBattle(asha, bilal);
  await openFriendZone(bilal, `&battle=${declined}`);
  await expect(bilal.page.getByText("Asha Menon challenged you to an exam battle of 10 questions (10 minutes).", { exact: false })).toBeVisible();
  await expect(bilal.page.getByRole("heading", { name: "How it works", level: 4 })).toBeVisible();
  await axe(bilal.page, testInfo, "battle-invited-in");
  await bilal.page.getByRole("button", { name: "Decline" }).click();
  await expect(bilal.page.getByRole("heading", { name: "Declined", level: 4 })).toBeVisible();
  await openFriendZone(asha, `&battle=${declined}`);
  await expect(asha.page.getByRole("heading", { name: "Not accepted", level: 4 })).toBeVisible();
  await axe(asha.page, testInfo, "battle-not-accepted");

  const cancelled = await createBattle(asha, bilal);
  await openFriendZone(asha, `&battle=${cancelled}`);
  await asha.page.getByRole("button", { name: "Cancel challenge" }).click();
  await expect(asha.page.getByRole("heading", { name: "Cancelled", level: 4 })).toBeVisible();
  await openFriendZone(bilal, `&battle=${cancelled}`);
  await expect(bilal.page.getByText("Asha Menon cancelled this challenge.", { exact: true })).toBeVisible();
  await axe(bilal.page, testInfo, "battle-cancelled");
});

test("every exam phase drawn from the server: the question, the palette, Submit, and the result (the opponent plays through the API)", { tag: "@webkit" }, async ({
  people,
}, testInfo) => {
  // In WebKit the test's own steps take several seconds each, so the opponent plays through the API; the screen of
  // the one player shown is what is checked.
  test.setTimeout(240_000);
  const { asha, bilal } = await friends(people);
  await bilal.page.close(); // one window on screen
  const battleId = await startExam(asha, bilal);
  await openFriendZone(asha, `&battle=${battleId}`);
  const { page } = asha;
  const first = examUi.heading(page, 1, 10);
  await expect(first).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => focused(first)).toBe(true);
  const keys = await correctKeys(people, battleId);
  await pick(page, keys[0] ?? "A");
  await expect(examUi.saved(page)).toBeVisible();
  await examUi.action(page, "Save & Next").click();
  await expect(examUi.heading(page, 2, 10)).toBeVisible();
  await shot(page, testInfo, "exam-webkit");

  expect((await submitExam(bilal, battleId, keys.map((key, index) => setItem(index + 1, wrongOf(key))))).status()).toBe(200);
  await expect(examUi.opponentStatus(page, "Bilal Khan", "Submitted")).toBeVisible({ timeout: 15_000 });
  await examUi.action(page, "Submit").click();
  await examUi.submitDialog(page).getByRole("button", { name: "Submit", exact: true }).click();
  const won = page.getByRole("heading", { name: "You won", level: 4 });
  await expect(won).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => focused(won)).toBe(true);
  await expect(page.getByText("Final marks 4 to −10.", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Review" }).getByText("Why:")).toHaveCount(10);
  await axe(page, testInfo, "result-webkit");
});
