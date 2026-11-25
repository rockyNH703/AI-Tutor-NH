import { randomBytes } from "node:crypto";

import AxeBuilder from "@axe-core/playwright";
import { type BrowserContext, type Locator, type Page, type TestInfo, test as base, expect } from "@playwright/test";
import { Client } from "pg";

import { PLAIN_COOKIE } from "../../src/lib/server/cookies";
import { signedCookieValue } from "../learning/wire";
import { waitForHydration } from "../support";
import {
  AUTH_DATABASE_URL,
  AUTH_SECRET,
  DOMAIN,
  MONTH,
  ON_API_URL,
  ON_APP_URL,
  PROGRAM,
  QA_API_URL,
  QA_APP_URL,
  QA_EMAILS,
  SLOW_API_URL,
  SLOW_APP_URL,
  assertSafeDatabase,
} from "./env";

export { expect };

const SESSION_MS = 24 * 60 * 60 * 1000;

/** Which stack a person's browser and API calls go to (env.ts): FRIEND_ZONE_MODE=qa or =on, or qa with 12 s rounds. */
export type Stack = "qa" | "on" | "slow";
const STACKS: Record<Stack, { app: string; api: string }> = {
  qa: { app: QA_APP_URL, api: QA_API_URL },
  on: { app: ON_APP_URL, api: ON_API_URL },
  slow: { app: SLOW_APP_URL, api: SLOW_API_URL },
};

export type Person = {
  name: string;
  email: string;
  userId: string;
  /** The seeded session token: the browser holds it as the signed cookie; tests use it only to ask the API directly. */
  token: string;
  /** The first invoice this account claimed (seeded, never shown by the app). */
  invoice: string;
  /** Every invoice it claimed, one per program. */
  invoices: string[];
  qa: boolean;
  stack: Stack;
  appUrl: string;
  apiUrl: string;
  context: BrowserContext;
  page: Page;
  /** Set once the person has joined (join() or apiJoin()). */
  playerId?: string;
};

export type PersonOptions = {
  /** In the QA list (FRIEND_ZONE_QA_EMAILS): the isolated qa cohort. Default true. */
  qa?: boolean;
  stack?: Stack;
  viewport?: { width: number; height: number };
  reducedMotion?: "reduce" | "no-preference";
  colorScheme?: "light" | "dark";
  /** The programs its invoices open (default: the seeded one, networking-ai). */
  programs?: string[];
};

export type People = {
  add: (name: string, options?: PersonOptions) => Promise<Person>;
  /** The suite's database (a `*_test` one), for checks the UI cannot show. */
  sql: <T extends Record<string, unknown>>(query: string, values?: unknown[]) => Promise<T[]>;
};

/**
 * People, each in a browser context of their own: a verified account (QA list or not), a session written the way
 * Better Auth writes them (e2e/learning/support.ts), and NH invoices claimed by it (networking-ai unless told; inserted,
 * because the claim route allows 30 claims a day per address). Every account and invoice is deleted after the test.
 */
export const test = base.extend<{ people: People }>({
  people: async ({ browser }, use, testInfo) => {
    assertSafeDatabase(AUTH_DATABASE_URL);
    const db = new Client({ connectionString: AUTH_DATABASE_URL });
    await db.connect();
    const created: Person[] = [];
    let nextQa = 0;
    const sql: People["sql"] = async (query, values = []) => (await db.query(query, values)).rows;
    const add: People["add"] = async (name, options = {}) => {
      const qa = options.qa ?? true;
      const stack = options.stack ?? "qa";
      const email = qa ? QA_EMAILS[nextQa++] : `live-${Date.now()}-${randomBytes(4).toString("hex")}@${DOMAIN}`;
      if (!email) throw new Error("the QA e-mail pool is used up: add more to QA_EMAILS");
      await db.query("DELETE FROM users WHERE email = $1", [email]); // a run that was stopped half way
      const token = randomBytes(24).toString("base64url");
      const now = Date.now();
      const [user] = (await db.query<{ id: string }>("INSERT INTO users (email, email_verified, display_name) VALUES ($1, true, $2) RETURNING id", [email, name])).rows;
      if (!user) throw new Error("the seeded account was not created");
      await db.query("INSERT INTO auth_sessions (user_id, token, created_at, last_seen_at, expires_at) VALUES ($1, $2, $3, $3, $4)", [
        user.id,
        token,
        new Date(now),
        new Date(now + SESSION_MS),
      ]);
      const invoices: string[] = [];
      for (const program of options.programs ?? [PROGRAM]) {
        const invoice = `FZE2E-${randomBytes(5).toString("hex").toUpperCase()}`;
        const [row] = (
          await db.query<{ id: string }>("INSERT INTO nh_invoices (invoice_no, program_id) SELECT $1, id FROM programs WHERE slug = $2 RETURNING id", [invoice, program])
        ).rows;
        if (!row) throw new Error(`program ${program} is missing`);
        await db.query("INSERT INTO invoice_claims (user_id, invoice_id) VALUES ($1, $2)", [user.id, row.id]);
        invoices.push(invoice);
      }

      const appUrl = STACKS[stack].app;
      const context = await browser.newContext({
        baseURL: appUrl,
        viewport: options.viewport ?? { width: 1440, height: 900 },
        reducedMotion: options.reducedMotion ?? "no-preference",
        colorScheme: options.colorScheme ?? "light",
        recordVideo: { dir: testInfo.outputPath("videos"), size: { width: 1280, height: 800 } },
      });
      await context.addCookies([{ name: PLAIN_COOKIE, value: signedCookieValue(token, AUTH_SECRET), url: appUrl, httpOnly: true, sameSite: "Lax" }]);
      const person: Person = {
        name,
        email,
        userId: user.id,
        token,
        invoice: invoices[0] ?? "",
        invoices,
        qa,
        stack,
        appUrl,
        apiUrl: STACKS[stack].api,
        context,
        page: await context.newPage(),
      };
      created.push(person);
      return person;
    };
    try {
      await use({ add, sql });
    } finally {
      for (const person of created) await person.context.close().catch(() => undefined);
      for (const person of created) {
        await db.query("DELETE FROM users WHERE id = $1", [person.userId]);
        await db.query("DELETE FROM nh_invoices WHERE invoice_no = ANY($1)", [person.invoices]);
      }
      await db.end();
    }
  },
});

/** Quizzes & Games of a program (the seeded one by default), on the Friend Zone side, `extra` added to the address. */
export async function openFriendZone(person: Person, extra = "", program = PROGRAM): Promise<void> {
  await person.page.goto(`/programs/${program}/quizzes?zone=friends&month=${MONTH}${extra}`);
  await waitForHydration(person.page);
}

/** A BFF call as the person's browser makes it (same origin, so the Origin check passes). */
export async function bff(person: Person, method: "GET" | "POST" | "PATCH" | "DELETE", path: string, data?: unknown) {
  return person.context.request.fetch(`/api/v1/friend-zone${path}`, {
    method,
    data,
    headers: { Origin: person.appUrl },
    failOnStatusCode: false,
  });
}

/** The API itself, with the person's token: only for routes the BFF does not proxy (the player view, the old answers route). */
export async function apiDirect(person: Person, path: string, method: "GET" | "POST" = "GET", data?: unknown) {
  return fetch(`${person.apiUrl}/v1/friend-zone${path}`, {
    method,
    headers: { Authorization: `Bearer ${person.token}`, ...(data === undefined ? {} : { "Content-Type": "application/json" }) },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
}

/** Join through the API (setup for tests about something else); the join screen has its own spec. */
export async function apiJoin(person: Person, discoverable = true): Promise<string> {
  const response = await bff(person, "POST", "/me", { displayName: person.name, discoverable });
  expect(response.status(), `join ${person.name}: ${await response.text()}`).toBe(201);
  person.playerId = ((await response.json()) as { playerId: string }).playerId;
  return person.playerId;
}

/** Friends through the API: a asks, b accepts. */
export async function apiBefriend(a: Person, b: Person): Promise<void> {
  const asked = await bff(a, "POST", "/requests", { playerId: b.playerId });
  expect(asked.status(), `request: ${await asked.text()}`).toBe(201);
  const incoming = ((await (await bff(b, "GET", "/requests")).json()) as { incoming: { requestId: string; player: { playerId: string } }[] }).incoming;
  const request = incoming.find((item) => item.player.playerId === a.playerId);
  if (!request) throw new Error("the request did not arrive");
  const accepted = await bff(b, "POST", `/requests/${request.requestId}/accept`);
  expect(accepted.status(), `accept: ${await accepted.text()}`).toBe(200);
}

/** The Friend Zone side's sections, by their headings. */
export const section = (page: Page, heading: string | RegExp) =>
  page.locator("section").filter({ has: page.getByRole("heading", { name: heading, level: 3 }) });

/** Every miss of a search says exactly this (plan J2), whatever the reason. */
export const MISS = "No match. Your friend needs to join Friend Zone and turn on 'Let classmates find me'.";

/**
 * axe (WCAG 2.0-2.2 A and AA) on what is on screen now, every impact level, after finite animations finish. `scope`
 * limits it to a dialog or region. Each check also leaves a screenshot in the evidence folder.
 */
export async function axe(
  page: Page,
  testInfo: TestInfo,
  label: string,
  scope?: string,
  settle = true,
  /** Rules a shared part of the app (not Friend Zone) fails today, already reported: written into the report, not failed. */
  knownOutside: Record<string, string> = {},
): Promise<void> {
  // settle=false inside a battle round: its timer bar never stops moving, and the round is only ROUND_SECONDS long.
  if (settle)
    await page
    .waitForFunction(
      () =>
        document
          .getAnimations()
          .every((animation) => animation.playState !== "running" || animation.effect?.getTiming().iterations === Number.POSITIVE_INFINITY),
      undefined,
      { timeout: 3_000 },
    )
    .catch(() => undefined); // a running battle timer keeps transitioning: measure it as it is
  let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]);
  if (scope) builder = builder.include(scope);
  const results = await builder.analyze();
  await shot(page, testInfo, `axe-${label}`);
  const describe = (violation: (typeof results.violations)[number]) =>
    `[${violation.impact}] ${violation.id}: ${violation.help} — ${violation.nodes.map((node) => node.target.join(" ")).join(" | ")}`;
  for (const violation of results.violations.filter((each) => knownOutside[each.id])) {
    testInfo.annotations.push({ type: "known issue outside Friend Zone", description: `${label}: ${describe(violation)} (${knownOutside[violation.id]})` });
  }
  const found = results.violations.filter((each) => !knownOutside[each.id]).map(describe);
  expect(found, `axe on "${label}" (${page.url()})`).toEqual([]);
}


/** A screenshot in this test's evidence folder (the run's results dir, outside the repository). */
export async function shot(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  await page.screenshot({ path: testInfo.outputPath(`${name.replace(/[^a-z0-9-]+/gi, "-")}.png`), fullPage: true });
}

/**
 * No sideways scrolling: neither the page nor the program page's one scroller (the region that holds the Friend
 * Zone) is wider than its box.
 */
export async function expectNoSidewaysScroll(page: Page): Promise<void> {
  const widths = await page.evaluate(() => {
    const root = document.documentElement;
    const zone = document.querySelector("[aria-label='Quizzes & Games view']");
    let scroller: Element | null = zone;
    while (scroller && scroller !== document.body) {
      const style = getComputedStyle(scroller);
      if (style.overflowY === "auto" || style.overflowY === "scroll") break;
      scroller = scroller.parentElement;
    }
    return {
      page: root.scrollWidth - root.clientWidth,
      scroller: scroller && scroller !== document.body ? scroller.scrollWidth - scroller.clientWidth : 0,
    };
  });
  expect(widths, `sideways scroll on ${page.url()}`).toEqual({ page: 0, scroller: 0 });
}

/** Tab (forwards) until `target` has focus: proves it is reachable by keyboard alone. */
export async function tabTo(page: Page, target: Locator, max = 80): Promise<void> {
  for (let presses = 0; presses < max; presses++) {
    if (await target.evaluate((element) => element === document.activeElement).catch(() => false)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`${target} was not reached with ${max} Tab presses`);
}

/** Whether `locator` has focus now. */
export const focused = (locator: Locator) => locator.evaluate((element) => element === document.activeElement);

/** The open round's correct key, read from the database (the API never sends it before the round closes). */
export async function correctKey(people: People, battleId: string, round: number): Promise<string> {
  const rows = await people.sql<{ key: string }>(
    "SELECT COALESCE(q.correct_keys[1], q.correct_option) AS key FROM fz_battle_rounds r JOIN qb_questions q USING (question_id) WHERE r.battle_id = $1 AND r.round = $2",
    [battleId, round],
  );
  const key = rows[0]?.key;
  if (!key) throw new Error(`no round ${round} in battle ${battleId}`);
  return key;
}

/** The battle open on screen, from the address (`?battle=`). */
export function battleIdOf(page: Page): string {
  const id = new URL(page.url()).searchParams.get("battle");
  if (!id) throw new Error(`no battle in ${page.url()}`);
  return id;
}

/** Types an invoice number into Add a friend and waits for the answer (every answer takes at least 0.4 s). */
export async function searchFor(page: Page, invoice: string): Promise<void> {
  const add = section(page, "Add a friend");
  await add.getByLabel("Classmate's invoice number").fill(invoice);
  await add.getByRole("button", { name: "Find" }).click();
  await expect(add.getByRole("button", { name: /^Find/ })).toHaveText("Find", { timeout: 15_000 }); // the 0.4 s floor
}

// ------------------------------------------------------------------ the exam battle (Friend Challenge plan Rev 2)

/** One of my saved exam responses, as the API sends it. */
export type ExamRow = { round: number; choice: string | null; marked: boolean; rev: number };
/** One item of a save or a submit (FriendZoneResponseItem): a whole question state, or a visit. */
export type ExamItem = { round: number; op: "visit" } | { round: number; op: "set"; choice: string | null; marked: boolean; baseRev: number };
type Player = { displayName: string | null; score: number; correct: number; answered: number; winner: boolean; timeTakenSeconds?: number | null };
/** The parts of FriendZoneBattleOut these tests read. */
export type BattleState = {
  battleId: string;
  status: string;
  result: string | null;
  endReason: string | null;
  format: "rounds" | "exam";
  length: number;
  startsAt: string | null;
  endsAt: string | null;
  serverTime: string;
  question: unknown;
  rounds: { round: number; voided: boolean }[];
  review: { round: number; correctKey: string | null; myChoice: string | null; theirChoice: string | null; voided: boolean }[] | null;
  me: Player;
  opponent: Player;
  exam: {
    secondsPerQuestion: number;
    endsAt: string;
    cutoffAt: string;
    questions: { round: number; stem: string; options: { key: string; text: string }[] }[];
    responses: ExamRow[];
    submittedAt: string | null;
    opponentSubmitted: boolean;
  } | null;
  xp: { reason: string; amount: number }[] | null;
};

/** A whole question state (a pick, a change, a Clear or a mark), on the row's `rev` (0: no row yet). */
export const setItem = (round: number, choice: string | null, marked = false, baseRev = 0): ExamItem => ({ round, op: "set", choice, marked, baseRev });

const KEYS = ["A", "B", "C", "D"];
/** Some option that is not `key` (the synthetic questions have four). */
export const wrongOf = (key: string): string => KEYS.find((other) => other !== key) ?? "A";

export const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

export async function battleState(person: Person, battleId: string): Promise<BattleState> {
  const response = await bff(person, "GET", `/battles/${battleId}`);
  expect(response.status(), `GET battle: ${await response.text()}`).toBe(200);
  return (await response.json()) as BattleState;
}

/** A challenge through the API (the challenge dialog has its own steps): `length` questions, month 1. */
export async function createBattle(challenger: Person, opponent: Person, length = 10): Promise<string> {
  const response = await bff(challenger, "POST", "/battles", { opponentId: opponent.playerId, program: PROGRAM, month: MONTH, length, requestId: crypto.randomUUID() });
  expect(response.status(), `create: ${await response.text()}`).toBe(201);
  return ((await response.json()) as { battleId: string }).battleId;
}

/** Challenge, accept and Ready through the API: the exam starts 3 seconds later. */
export async function startExam(challenger: Person, opponent: Person, length = 10): Promise<string> {
  const battleId = await createBattle(challenger, opponent, length);
  const accepted = await bff(opponent, "POST", `/battles/${battleId}/accept`);
  expect(accepted.status(), `accept: ${await accepted.text()}`).toBe(200);
  const ready = await bff(challenger, "POST", `/battles/${battleId}/ready`);
  expect(ready.status(), `ready: ${await ready.text()}`).toBe(200);
  return battleId;
}

/** Waits for the paper (sent only once the 3-second countdown is over) and returns the battle then. */
export async function examStarted(person: Person, battleId: string): Promise<BattleState> {
  let state = await battleState(person, battleId);
  for (let tries = 0; !state.exam && tries < 40; tries++) {
    await sleep(250);
    state = await battleState(person, battleId);
  }
  if (!state.exam) throw new Error(`the exam of ${battleId} did not start (${state.status})`);
  return state;
}

/** POST /battles/{id}/responses (FriendZoneBattleResponsesRequest), as the exam's save queue sends it. */
export const saveResponses = (person: Person, battleId: string, responses: ExamItem[]) => bff(person, "POST", `/battles/${battleId}/responses`, { responses });

/** POST /battles/{id}/submit (FriendZoneSubmitRequest): the items go in as final values. */
export const submitExam = (person: Person, battleId: string, responses: ExamItem[] = [], auto = false) =>
  bff(person, "POST", `/battles/${battleId}/submit`, { responses, auto });

/** Every question's correct key, in order, from the database (the API never sends one before completion). */
export async function correctKeys(people: People, battleId: string): Promise<string[]> {
  const rows = await people.sql<{ key: string }>(
    "SELECT COALESCE(q.correct_keys[1], q.correct_option) AS key FROM fz_battle_rounds r JOIN qb_questions q USING (question_id) WHERE r.battle_id = $1 ORDER BY r.round",
    [battleId],
  );
  if (rows.length === 0) throw new Error(`no questions in battle ${battleId}`);
  return rows.map((row) => row.key);
}

/** A whole exam played through the API: `a` answers every question right, `b` every one wrong; both submit. */
export async function playExamByApi(people: People, a: Person, b: Person, battleId: string): Promise<BattleState> {
  await examStarted(a, battleId);
  const keys = await correctKeys(people, battleId);
  const right = await submitExam(a, battleId, keys.map((key, index) => setItem(index + 1, key)));
  expect(right.status(), `submit a: ${await right.text()}`).toBe(200);
  const wrong = await submitExam(b, battleId, keys.map((key, index) => setItem(index + 1, wrongOf(key))));
  expect(wrong.status(), `submit b: ${await wrong.text()}`).toBe(200);
  const state = await battleState(a, battleId);
  expect(state.status, "both submitted: the exam completes at once").toBe("completed");
  return state;
}

/** A player's saved rows of an exam, straight from the database (`role`: challenger or opponent). */
export async function savedRows(people: People, battleId: string, role: "challenger" | "opponent"): Promise<{ round: number; choice: string | null; marked: boolean }[]> {
  return people.sql("SELECT round, choice, marked FROM fz_battle_responses WHERE battle_id = $1 AND role = $2 ORDER BY round", [battleId, role]);
}

/**
 * A completed legacy round battle (format 'rounds', 5 rounds, from before the exam), written straight into the
 * database: no route creates one any more. `winner` was the challenger; the scores are round points.
 */
export async function legacyRoundBattle(people: People, winner: Person, loser: Person): Promise<string> {
  const [where] = await people.sql<{ program_id: string; module_id: string }>(
    "SELECT p.id AS program_id, m.id AS module_id FROM programs p JOIN program_modules m ON m.program_id = p.id WHERE p.slug = $1 AND m.month = $2",
    [PROGRAM, MONTH],
  );
  if (!where) throw new Error(`no month ${MONTH} in ${PROGRAM}`);
  const questions = (
    await people.sql<{ question_id: string }>(
      "SELECT q.question_id FROM qb_questions q JOIN program_module_books b ON b.book_id = q.book_id WHERE b.module_id = $1 AND q.question_format = 'mcq' AND q.reviewed_hash = q.content_hash ORDER BY q.question_id LIMIT 5",
      [where.module_id],
    )
  ).map((row) => row.question_id);
  const [profile] = await people.sql<{ cohort: string }>("SELECT cohort FROM fz_profiles WHERE user_id = $1", [winner.userId]);
  if (!profile || questions.length !== 5) throw new Error("the legacy battle needs a joined winner and five questions");
  const [battle] = await people.sql<{ id: string }>(
    `INSERT INTO fz_battles (cohort, program_id, module_id, difficulty, length, question_ids, status, result, end_reason, expires_at,
       starts_at, ends_at, round, ended_at, format, created_at, updated_at)
     VALUES ($1, $2, $3, 'mixed', 5, $4, 'completed', 'points', 'finished', now() - interval '2 days', now() - interval '2 days',
       now() - interval '2 days' + interval '3 minutes', 5, now() - interval '2 days' + interval '3 minutes', 'rounds',
       now() - interval '2 days', now() - interval '2 days')
     RETURNING id`,
    [profile.cohort, where.program_id, where.module_id, questions],
  );
  if (!battle) throw new Error("the legacy battle was not written");
  for (const [role, person, score, correct, won] of [
    ["challenger", winner, 640, 5, true],
    ["opponent", loser, 210, 2, false],
  ] as const) {
    await people.sql(
      "INSERT INTO fz_battle_players (battle_id, role, cohort, user_id, ready_at, score, correct, answered, winner) VALUES ($1, $2, $3, $4, now() - interval '2 days', $5, $6, 5, $7)",
      [battle.id, role, profile.cohort, person.userId, score, correct, won],
    );
  }
  for (const [index, questionId] of questions.entries()) {
    await people.sql(
      `INSERT INTO fz_battle_rounds (battle_id, round, question_id, seconds, opened_at, closes_at, closed_at)
       VALUES ($1, $2, $3, 20, now() - interval '2 days' + make_interval(secs => $4), now() - interval '2 days' + make_interval(secs => $4 + 20),
         now() - interval '2 days' + make_interval(secs => $4 + 20))`,
      [battle.id, index + 1, questionId, index * 30],
    );
  }
  return battle.id;
}

/** The exam screen's parts, by the names lane D's exam-view.tsx and exam-palette.tsx give them. */
export const examUi = {
  heading: (page: Page, round: number, total: number) => page.getByRole("heading", { name: `Question ${round} of ${total}`, level: 4 }),
  /** An option: a native radio named "A. <text>". */
  option: (page: Page, key: string) => page.getByRole("radio", { name: new RegExp(`^${key}\\.`) }),
  /** The palette: inline from 1280 px (xl), in the Questions sheet below. */
  palette: (page: Page) => page.getByRole("navigation", { name: "Questions" }),
  /** A pill, named "Question N, <state>[, not saved]". */
  pill: (page: Page, round: number) => page.getByRole("navigation", { name: "Questions" }).getByRole("button", { name: new RegExp(`^Question ${round}, `) }),
  action: (page: Page, name: "Previous" | "Clear Response" | "Mark for Review & Next" | "Save & Next" | "Submit" | "Questions" | "Save again") =>
    page.getByRole("button", { name, exact: true }),
  timer: (page: Page) => page.getByRole("timer"),
  sheet: (page: Page) => page.getByRole("dialog", { name: "Questions" }),
  submitDialog: (page: Page) => page.getByRole("alertdialog", { name: "Submit your answers?" }),
  timeUp: (page: Page) => page.getByRole("heading", { name: "Time's up: submitting…", level: 4 }),
  waiting: (page: Page) => page.getByRole("heading", { name: "Submitted", level: 4 }),
  /** "Bilal Khan: In progress" or "…: Submitted" in the sticky header (and on the waiting screen). */
  opponentStatus: (page: Page, name: string, state: "In progress" | "Submitted") => page.getByText(`${name}: ${state}`, { exact: true }),
  saved: (page: Page) => page.getByText("Saved", { exact: true }),
};

/** The countdown's m:ss in whole seconds. */
export async function timerSeconds(page: Page): Promise<number> {
  const text = (await examUi.timer(page).innerText()).trim();
  const match = text.match(/(\d+):(\d{2})$/);
  if (!match) throw new Error(`the timer reads "${text}"`);
  return Number(match[1]) * 60 + Number(match[2]);
}

/** Picks `key` on the open question by click, digit (1-4) or letter shortcut, and waits until its radio is checked. */
export async function pick(page: Page, key: string, how: "click" | "digit" | "letter" = "click"): Promise<void> {
  const option = examUi.option(page, key);
  if (how === "click") await option.click();
  else await page.keyboard.press(how === "digit" ? String(KEYS.indexOf(key) + 1) : key.toLowerCase());
  await expect(option).toBeChecked();
}
