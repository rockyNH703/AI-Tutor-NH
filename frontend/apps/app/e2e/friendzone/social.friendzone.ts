import {
  MISS,
  apiBefriend,
  apiJoin,
  axe,
  bff,
  expect,
  focused,
  openFriendZone,
  type Person,
  searchFor,
  section,
  shot,
  test,
} from "./support";

/**
 * Phase 1 journeys J1-J3 and J7 (plan section 4) on the FRIEND_ZONE_MODE=qa stack with QA accounts, as production QA
 * will run them: join, find by invoice, requests, friends, remove, block and report. Every state passes axe.
 */

const SWITCHES = ["Let classmates find me by invoice number", "Show my program to friends", "Show my stats to friends"];
/** The owner's approved consent text (U9, CD20), word for word: a change must come from the owner. */
const CONSENT =
  "Friend Zone shows your Friend Zone name to classmates you add as friends. If you turn it on, classmates can find you by your invoice number; the number itself is never shown. Your email, invoice number and learning records are never shared. Battle and daily-challenge results are shared only with friends, and stats only if you choose. You can change these settings at any time, and deleting your account removes your Friend Zone data.";

/** The friend's row in Friends, opened (its button expands the actions). */
async function openFriend(person: Person, name: string) {
  const friends = section(person.page, /^Friends/);
  const row = friends.getByRole("button", { name, exact: true });
  await row.click();
  await expect(row).toHaveAttribute("aria-expanded", "true");
  return friends;
}

test("J1 join: every switch starts OFF, the consent text is shown, and the member joins the qa cohort", { tag: "@webkit" }, async ({ people }, testInfo) => {
  const asha = await people.add("Asha Menon");
  const { page } = asha;
  await openFriendZone(asha);

  await expect(page.getByRole("button", { name: "Friend Zone", pressed: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Join Friend Zone", level: 3 })).toBeVisible();
  for (const name of SWITCHES) await expect(page.getByRole("switch", { name })).toHaveAttribute("aria-checked", "false");
  await expect(page.getByText(CONSENT, { exact: true })).toBeVisible();
  await expect(page.getByText("Your account photo is shown to friends only.").first()).toBeVisible();
  await axe(page, testInfo, "join");

  // An empty name is refused on the screen, with focus on the field.
  await page.getByRole("button", { name: "Join Friend Zone" }).click();
  await expect(page.getByText("Enter a name.")).toBeVisible();
  expect(await focused(page.getByLabel("Friend Zone name"))).toBe(true);
  await axe(page, testInfo, "join-name-error");

  await page.getByLabel("Friend Zone name").fill("Asha Menon");
  await page.getByRole("button", { name: "Join Friend Zone" }).click();
  await expect(page.getByText("You've joined Friend Zone.")).toBeVisible();
  // The next step has focus: Add a friend.
  await expect.poll(() => focused(page.getByRole("heading", { name: "Add a friend", level: 3 }))).toBe(true);
  const profile = section(page, "Your profile");
  await expect(profile.getByText("Asha Menon", { exact: true })).toBeVisible();
  await expect(profile.getByText("Test account")).toBeVisible();
  await expect(profile.locator("dt")).toHaveText(SWITCHES);
  await expect(profile.locator("dd")).toHaveText(["Off", "Off", "Off"]);
  await axe(page, testInfo, "member-hub-empty");

  const [row] = await people.sql<{ cohort: string; discoverable: boolean; show_program: boolean; show_stats: boolean }>(
    "SELECT cohort, discoverable, show_program, show_stats FROM fz_profiles WHERE user_id = $1",
    [asha.userId],
  );
  expect(row).toEqual({ cohort: "qa", discoverable: false, show_program: false, show_stats: false });
});

test("J2 find: only after 'Let classmates find me' is on; every miss is the identical message; your own number says so", async ({ people }, testInfo) => {
  const asha = await people.add("Asha Menon");
  const bilal = await people.add("Bilal Khan");
  await apiJoin(asha, true);
  await apiJoin(bilal, false);
  const { page } = asha;
  await openFriendZone(asha);
  const add = section(page, "Add a friend");

  // Not findable yet, and a number nobody has: the same line, and the same answer on the wire.
  await searchFor(page, bilal.invoice);
  await expect(add.getByText(MISS, { exact: true })).toBeVisible();
  await expect(add.getByRole("button", { name: "Send friend request" })).toHaveCount(0);
  await shot(page, testInfo, "miss-not-findable");
  await searchFor(page, "FZE2E-NOBODY0000");
  await expect(add.getByText(MISS, { exact: true })).toBeVisible();
  await axe(page, testInfo, "search-miss");
  const misses = [];
  for (const invoiceNumber of [bilal.invoice, "FZE2E-NOBODY0001"]) {
    const started = Date.now();
    const response = await bff(asha, "POST", "/search", { invoiceNumber });
    const body = (await response.json()) as { error: Record<string, unknown> };
    misses.push({ status: response.status(), code: body.error.code, message: body.error.message, fields: body.error.fields });
    expect(Date.now() - started, "every answer takes at least the 0.4 s floor").toBeGreaterThanOrEqual(400);
  }
  expect(misses[0]).toEqual(misses[1]);
  expect(misses[0]?.status).toBe(404);

  // Bilal turns on "Let classmates find me" (Edit profile), then Asha finds him: his name, never his number.
  await openFriendZone(bilal);
  const profile = section(bilal.page, "Your profile");
  await profile.getByRole("button", { name: "Edit profile" }).click();
  await axe(bilal.page, testInfo, "edit-profile");
  await bilal.page.getByRole("switch", { name: SWITCHES[0] }).click();
  await bilal.page.getByRole("button", { name: "Save changes" }).click();
  await expect(profile.getByRole("button", { name: "Edit profile" })).toBeVisible();
  await expect(profile.locator("dd").first()).toHaveText("On");

  await searchFor(page, bilal.invoice);
  await expect(add.getByText("Bilal Khan", { exact: true })).toBeVisible();
  await expect(add.getByRole("button", { name: "Send friend request" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(bilal.invoice); // the number stays in the field only
  await axe(page, testInfo, "search-hit");

  await searchFor(page, asha.invoice);
  await expect(add.getByText("That's your own invoice number.")).toBeVisible();
});

test("J3 request, accept, and the friends list on both sides; the bell names no one", { tag: "@webkit" }, async ({ people }, testInfo) => {
  const asha = await people.add("Asha Menon");
  const bilal = await people.add("Bilal Khan");
  await apiJoin(asha, true);
  await apiJoin(bilal, true);

  await openFriendZone(asha);
  await searchFor(asha.page, bilal.invoice);
  await section(asha.page, "Add a friend").getByRole("button", { name: "Send friend request" }).click();
  await expect(section(asha.page, "Add a friend").getByText("Request sent to Bilal Khan.")).toBeVisible();
  await expect(section(asha.page, "Add a friend").getByLabel("Classmate's invoice number")).toHaveValue("");
  const sent = section(asha.page, "Requests");
  await expect(sent.getByText("Sent (1)")).toBeVisible();
  await expect(sent.getByText("Waiting for an answer")).toBeVisible();

  await openFriendZone(bilal);
  const received = section(bilal.page, "Requests");
  await expect(received.getByText("Received (1)")).toBeVisible();
  await expect(received.getByText("Asha Menon", { exact: true })).toBeVisible();
  await axe(bilal.page, testInfo, "request-received");
  await received.getByRole("button", { name: "Accept" }).click();
  await expect(received.getByText("You're now friends with Asha Menon.")).toBeVisible();
  await expect(section(bilal.page, /^Friends/).getByRole("heading", { name: "Friends (1)" })).toBeVisible();
  await expect(section(bilal.page, /^Friends/).getByRole("button", { name: "Asha Menon", exact: true })).toBeVisible();
  await axe(bilal.page, testInfo, "friends-list");

  await openFriendZone(asha);
  await expect(section(asha.page, /^Friends/).getByRole("button", { name: "Bilal Khan", exact: true })).toBeVisible();
  await expect(section(asha.page, "Requests").getByText("No requests right now.")).toBeVisible();
  const friends = await openFriend(asha, "Bilal Khan");
  await expect(friends.getByText("Program hidden")).toBeVisible(); // Show my program is off by default
  await axe(asha.page, testInfo, "friend-expanded");

  const bells = await people.sql<{ user_id: string; kind: string; title: string; body: string; link: string | null }>(
    "SELECT user_id, kind, title, body, link FROM user_notifications WHERE user_id = ANY($1) AND kind LIKE 'friend_%'",
    [[asha.userId, bilal.userId]],
  );
  expect(bells.map((bell) => `${bell.user_id === bilal.userId ? "bilal" : "asha"}:${bell.kind}`).sort()).toEqual(["asha:friend_accepted", "bilal:friend_request"]);
  for (const bell of bells) {
    expect(`${bell.title} ${bell.body}`).not.toMatch(/Asha|Bilal|FZE2E|@/);
    expect(bell.link).toContain("zone=friends");
  }
});

test("J3 a decline is silent to the sender, who can still cancel", async ({ people }, testInfo) => {
  const asha = await people.add("Asha Menon");
  const bilal = await people.add("Bilal Khan");
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  expect((await bff(asha, "POST", "/requests", { playerId: bilal.playerId })).status()).toBe(201);

  await openFriendZone(bilal);
  const received = section(bilal.page, "Requests");
  // Block on a received request asks first too, in an alertdialog opening on Cancel.
  await received.getByRole("button", { name: "Block" }).click();
  const confirm = bilal.page.getByRole("alertdialog");
  await expect(confirm.getByRole("heading", { name: "Block this member?" })).toBeVisible();
  await expect.poll(() => focused(confirm.getByRole("button", { name: "Cancel" }))).toBe(true);
  await axe(bilal.page, testInfo, "block-request-dialog", "[role='alertdialog']");
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toHaveCount(0);
  await received.getByRole("button", { name: "Decline" }).click();
  await expect(received.getByText("Request declined. They aren't told.")).toBeVisible();
  await expect(received.getByText("No requests right now.")).toBeVisible();
  await axe(bilal.page, testInfo, "declined");

  // Asha sees exactly what she saw before the decline.
  await openFriendZone(asha);
  const sent = section(asha.page, "Requests");
  await expect(sent.getByText("Sent (1)")).toBeVisible();
  await expect(sent.getByText("Waiting for an answer")).toBeVisible();
  await shot(asha.page, testInfo, "sender-after-decline");
  const bells = await people.sql("SELECT kind FROM user_notifications WHERE user_id = $1 AND kind LIKE 'friend_%'", [asha.userId]);
  expect(bells, "a decline notifies no one").toEqual([]);

  await sent.getByRole("button", { name: "Cancel request" }).click();
  await expect(sent.getByText("Request cancelled.")).toBeVisible();
  await expect(sent.getByText("No requests right now.")).toBeVisible();
});

test("J7 remove friend and block, each confirmed first, silent to the other member; unblock", async ({ people }, testInfo) => {
  const asha = await people.add("Asha Menon");
  const bilal = await people.add("Bilal Khan");
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  const { page } = asha;
  await openFriendZone(asha);

  // Remove: the dialog first; Cancel changes nothing.
  let friends = await openFriend(asha, "Bilal Khan");
  await friends.getByRole("button", { name: "Remove friend" }).click();
  // A destructive confirm is an alertdialog, described by its consequence, opening on Cancel (Enter removes no one).
  const confirm = page.getByRole("alertdialog");
  await expect(confirm.getByRole("heading", { name: "Remove this friend?" })).toBeVisible();
  await expect(confirm).toContainText("Bilal Khan isn't told.");
  await expect(confirm).toHaveAccessibleDescription(/Bilal Khan isn't told\./);
  await expect.poll(() => focused(confirm.getByRole("button", { name: "Cancel" }))).toBe(true);
  await axe(page, testInfo, "remove-dialog", "[role='alertdialog']");
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toHaveCount(0);
  await expect(friends.getByRole("button", { name: "Bilal Khan", exact: true })).toBeVisible();

  await friends.getByRole("button", { name: "Remove friend" }).click();
  await confirm.getByRole("button", { name: "Remove friend" }).click();
  await expect(friends.getByText("Removed from your friends. They aren't told.")).toBeVisible();
  await expect(friends.getByText("No friends yet.", { exact: false })).toBeVisible();
  await expect.poll(() => focused(page.getByRole("heading", { name: /^Friends/, level: 3 }))).toBe(true);
  await openFriendZone(bilal);
  await expect(section(bilal.page, /^Friends/).getByText("No friends yet.", { exact: false })).toBeVisible();

  // Block: confirmed, then the blocked list; Bilal can no longer find Asha, with the same miss as anyone.
  await apiBefriend(asha, bilal);
  await openFriendZone(asha);
  friends = await openFriend(asha, "Bilal Khan");
  await friends.getByRole("button", { name: "Block" }).click();
  await expect(confirm.getByRole("heading", { name: "Block this member?" })).toBeVisible();
  await expect(confirm).toHaveAccessibleDescription(/Bilal Khan isn't told\./);
  await expect.poll(() => focused(confirm.getByRole("button", { name: "Cancel" }))).toBe(true);
  await axe(page, testInfo, "block-dialog", "[role='alertdialog']");
  await confirm.getByRole("button", { name: "Block" }).click();
  await expect(friends.getByText("Blocked. They aren't told, and you won't see or find each other in Friend Zone.")).toBeVisible();
  const blocked = section(page, "Blocked");
  await expect(blocked.getByText("Bilal Khan", { exact: true })).toBeVisible();
  await axe(page, testInfo, "blocked-list");

  await openFriendZone(bilal);
  await expect(section(bilal.page, /^Friends/).getByText("No friends yet.", { exact: false })).toBeVisible();
  await searchFor(bilal.page, asha.invoice);
  await expect(section(bilal.page, "Add a friend").getByText(MISS, { exact: true })).toBeVisible();
  const kinds = await people.sql<{ kind: string }>("SELECT kind FROM user_notifications WHERE user_id = $1 AND kind LIKE 'friend_%' ORDER BY created_at", [bilal.userId]);
  expect(kinds.map((row) => row.kind), "remove and block notify no one").toEqual(["friend_request", "friend_request"]);

  await blocked.getByRole("button", { name: "Unblock" }).click();
  await expect(blocked.getByText("Unblocked. A friendship that ended with the block doesn't come back.")).toBeVisible();
  await expect(blocked.getByText("You haven't blocked anyone.")).toBeVisible();
  await expect(section(page, /^Friends/).getByText("No friends yet.", { exact: false })).toBeVisible();
});

test("J7 report: a reason is required; the report is recorded privately and the member is not told", async ({ people }, testInfo) => {
  const asha = await people.add("Asha Menon");
  const bilal = await people.add("Bilal Khan");
  await apiJoin(asha, true);
  await apiJoin(bilal, true);
  await apiBefriend(asha, bilal);
  const { page } = asha;
  await openFriendZone(asha);

  const friends = await openFriend(asha, "Bilal Khan");
  await friends.getByRole("button", { name: "Report" }).click();
  const dialog = page.getByRole("dialog", { name: "Report a member" });
  await expect(dialog).toBeVisible();
  await axe(page, testInfo, "report-dialog", "[role='dialog']");
  await dialog.getByRole("button", { name: "Send report" }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Choose a reason.");
  await axe(page, testInfo, "report-dialog-error", "[role='dialog']");
  await dialog.getByRole("radio", { name: "Spam" }).check();
  await dialog.getByLabel(/Details/).fill("Sends the same link over and over.");
  await dialog.getByRole("button", { name: "Send report" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(friends.getByText("Report sent. Thank you for telling us.")).toBeVisible();

  const reports = await people.sql<{ reason: string; details: string; cohort: string; status: string }>(
    "SELECT reason, details, cohort, status FROM fz_reports WHERE reporter_id = $1 AND reported_id = $2",
    [asha.userId, bilal.userId],
  );
  expect(reports).toEqual([{ reason: "spam", details: "Sends the same link over and over.", cohort: "qa", status: "open" }]);
  const told = await people.sql("SELECT kind FROM user_notifications WHERE user_id = $1 AND kind LIKE 'friend_%' AND kind NOT IN ('friend_request', 'friend_accepted')", [bilal.userId]);
  expect(told, "nothing tells the reported member").toEqual([]);
});
