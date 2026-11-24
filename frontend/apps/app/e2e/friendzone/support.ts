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

/** The API itself, with the person's token: only for routes the BFF does not proxy (the player view). */
export async function apiDirect(person: Person, path: string) {
  return fetch(`${person.apiUrl}/v1/friend-zone${path}`, { headers: { Authorization: `Bearer ${person.token}` } });
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
