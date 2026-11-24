import type { TestInfo } from "@playwright/test";

import {
  MISS,
  type Person,
  apiBefriend,
  apiDirect,
  apiJoin,
  axe,
  bff,
  expect,
  focused,
  openFriendZone,
  searchFor,
  section,
  shot,
  test,
} from "./support";

/**
 * QA isolation (plan section 6, I19): a QA account and a real (live) member never see each other. On the
 * FRIEND_ZONE_MODE=on stack both cohorts are open at once, the hard case: search, the player view, the photo, a
 * request and a battle's address all answer exactly as for something that does not exist, both ways. On the
 * FRIEND_ZONE_MODE=qa stack a live account gets no Friend Zone at all.
 */

const RANDOM_ID = "6f1d2c3b-4a59-4e8f-9a7b-0c1d2e3f4a5b";

type Miss = { status: number; code: unknown; message: unknown };

async function missOf(response: { status(): number; json(): Promise<unknown> }): Promise<Miss> {
  const body = (await response.json().catch(() => ({}))) as { error?: { code?: unknown; message?: unknown } };
  return { status: response.status(), code: body.error?.code, message: body.error?.message };
}

async function directMiss(person: Person, path: string): Promise<Miss> {
  const response = await apiDirect(person, path);
  const body = (await response.json().catch(() => ({}))) as { error?: { code?: unknown; message?: unknown } };
  return { status: response.status, code: body.error?.code, message: body.error?.message };
}

async function battleBetween(a: Person, b: Person): Promise<string> {
  const response = await bff(a, "POST", "/battles", { opponentId: b.playerId, program: "networking-ai", month: 1, length: 5, requestId: crypto.randomUUID() });
  expect(response.status(), await response.text()).toBe(201);
  return ((await response.json()) as { battleId: string }).battleId;
}

/** Everything `viewer` may try against `other` answers as a missing thing would: search, view, photo, request, battle. */
async function expectInvisible(viewer: Person, other: Person, otherBattle: string, label: string, testInfo: TestInfo) {
  const { page } = viewer;
  await openFriendZone(viewer);
  await searchFor(page, other.invoice);
  await expect(section(page, "Add a friend").getByText(MISS, { exact: true })).toBeVisible();
  await expect(page.getByText(other.name)).toHaveCount(0);
  await shot(page, testInfo, `${label}-search`);

  const search = await missOf(await bff(viewer, "POST", "/search", { invoiceNumber: other.invoice }));
  const nobody = await missOf(await bff(viewer, "POST", "/search", { invoiceNumber: "FZE2E-NOBODY0000" }));
  expect(search, "a search across cohorts is a plain miss").toEqual(nobody);

  // The player view, through the BFF and of the API itself with the viewer's session.
  expect(await missOf(await bff(viewer, "GET", `/players/${other.playerId}`)), "player view").toEqual(
    await missOf(await bff(viewer, "GET", `/players/${RANDOM_ID}`)),
  );
  expect(await directMiss(viewer, `/players/${other.playerId}`), "player view (API)").toEqual(await directMiss(viewer, `/players/${RANDOM_ID}`));
  expect(await missOf(await bff(viewer, "GET", `/players/${other.playerId}/avatar?v=1`)), "photo").toEqual(
    await missOf(await bff(viewer, "GET", `/players/${RANDOM_ID}/avatar?v=1`)),
  );
  expect(await missOf(await bff(viewer, "POST", "/requests", { playerId: other.playerId })), "request").toEqual(
    await missOf(await bff(viewer, "POST", "/requests", { playerId: RANDOM_ID })),
  );
  expect(await missOf(await bff(viewer, "GET", `/battles/${otherBattle}`)), "battle state").toEqual(
    await missOf(await bff(viewer, "GET", `/battles/${RANDOM_ID}`)),
  );
  expect(await missOf(await bff(viewer, "POST", `/battles/${otherBattle}/accept`)), "battle move").toEqual(
    await missOf(await bff(viewer, "POST", `/battles/${RANDOM_ID}/accept`)),
  );

  // The battle's address opened in the browser: the same "isn't available" as any unknown battle.
  await openFriendZone(viewer, `&battle=${otherBattle}`);
  const gone = page.getByRole("heading", { name: "This battle isn't available" });
  await expect(gone).toBeVisible();
  await expect.poll(() => focused(gone)).toBe(true);
  await expect(page.getByText(other.name)).toHaveCount(0);
  await axe(page, testInfo, `${label}-battle-url`);
}

test("isolation (FRIEND_ZONE_MODE=on): a live member sees none of the QA members' Friend Zone, and the reverse", async ({ people }, testInfo) => {
  test.setTimeout(240_000);
  const quinn = await people.add("Quinn Rao", { qa: true, stack: "on" });
  const ravi = await people.add("Ravi Iyer", { qa: true, stack: "on" });
  const lina = await people.add("Lina Das", { qa: false, stack: "on" });
  const leo = await people.add("Leo Pinto", { qa: false, stack: "on" });
  for (const person of [quinn, ravi, lina, leo]) await apiJoin(person, true);
  await apiBefriend(quinn, ravi);
  await apiBefriend(lina, leo);
  const qaBattle = await battleBetween(quinn, ravi);
  const liveBattle = await battleBetween(lina, leo);

  const cohorts = await people.sql<{ name: string; cohort: string }>(
    "SELECT display_name AS name, cohort FROM fz_profiles WHERE user_id = ANY($1) ORDER BY display_name",
    [[quinn.userId, ravi.userId, lina.userId, leo.userId]],
  );
  expect(cohorts).toEqual([
    { name: "Leo Pinto", cohort: "live" },
    { name: "Lina Das", cohort: "live" },
    { name: "Quinn Rao", cohort: "qa" },
    { name: "Ravi Iyer", cohort: "qa" },
  ]);

  await expectInvisible(lina, quinn, qaBattle, "live-looks-at-qa", testInfo);
  await expectInvisible(quinn, lina, liveBattle, "qa-looks-at-live", testInfo);

  // Each side's lists hold its own cohort only, and no live bell points at a QA resource.
  await openFriendZone(lina);
  await expect(section(lina.page, /^Friends/).getByRole("button", { name: "Leo Pinto", exact: true })).toBeVisible();
  await expect(section(lina.page, "Battles").getByText("Leo Pinto")).toBeVisible();
  for (const name of ["Quinn Rao", "Ravi Iyer"]) await expect(lina.page.getByText(name)).toHaveCount(0);
  await shot(lina.page, testInfo, "live-hub");
  const bells = await people.sql<{ link: string | null }>("SELECT link FROM user_notifications WHERE user_id = ANY($1)", [[lina.userId, leo.userId]]);
  for (const bell of bells) expect(bell.link ?? "").not.toContain(qaBattle);
});

test("isolation (FRIEND_ZONE_MODE=qa): an account not on the QA list gets no Friend Zone at all", async ({ people }, testInfo) => {
  const lina = await people.add("Lina Das", { qa: false, stack: "qa" });
  const quinn = await people.add("Quinn Rao", { qa: true, stack: "qa" });
  await apiJoin(quinn, true);
  const { page } = lina;
  await openFriendZone(lina);
  // Practice is shown, with no switch: the address asked for Friend Zone, which is off for this account.
  await expect(page.getByRole("group", { name: "Quizzes & Games view" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Join Friend Zone" })).toHaveCount(0);
  await shot(page, testInfo, "live-on-qa-stack");
  const me = (await (await bff(lina, "GET", "/me")).json()) as { enabled: boolean; member: boolean };
  expect(me).toMatchObject({ enabled: false, member: false });
  for (const [method, path, data] of [
    ["POST", "/me", { displayName: "Lina Das" }],
    ["POST", "/search", { invoiceNumber: quinn.invoice }],
    ["GET", "/friends", undefined],
    ["GET", "/battles?view=open", undefined],
  ] as const) {
    expect((await bff(lina, method, path, data)).status(), `${method} ${path}`).toBe(404);
  }
  expect((await apiDirect(lina, `/players/${quinn.playerId}`)).status).toBe(404);
});
