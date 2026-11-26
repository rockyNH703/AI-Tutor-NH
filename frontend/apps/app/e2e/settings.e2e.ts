/**
 * Settings, end to end: every section saves to the account, survives a reload and shows up in
 * a second browser (another device), and two tabs never overwrite each other unnoticed.
 *
 * The learning sections have exactly one test each (Engineering Profile, Skills & Certification
 * Goals, Learning Preferences, AI Assistant, Practice & Lab Time, Practice & Goals, Notifications;
 * Practice Streak is in streak.e2e.ts). One test is one section's whole workflow, not one
 * assertion: it keeps every check of the tests it replaced, and ends where the setting takes
 * effect: what the assistant is told (`tutorKnows`), or, for the settings that no longer reach it
 * (goals, time goals, Notifications), that nothing of them does.
 */

import { type Browser, type Page, expect } from "@playwright/test";

import { APP_URL } from "./env";
import {
  PASSWORD,
  SAME_ORIGIN,
  expectAccessible,
  expectNoHorizontalOverflow,
  mailCode,
  openSettingsFromSidebar,
  sendAndWait,
  test,
  tutorKnows,
  uniqueEmail,
} from "./support";

/** A 48 x 48 PNG (two colours), made with Pillow. */
const PHOTO = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAIAAADYYG7QAAAATklEQVR42u3QsRFAEQBEQaT6ksoV9+Nfk1wJck0wBPsKuNm5OL8cdtT+vmUnhccCAgICAgICAgICAgI6W6xleAgICAgICAgICAgICOheCzTtBVMQB30NAAAAAElFTkSuQmCC",
  "base64",
);

/** The same account signed in from another browser: a second device. */
async function secondDevice(browser: Browser, account: { email: string; password: string }): Promise<Page> {
  const context = await browser.newContext({ baseURL: APP_URL });
  const page = await context.newPage();
  const login = await context.request.post("/api/auth/sign-in/email", {
    data: { email: account.email, password: account.password },
    headers: { ...SAME_ORIGIN, "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0" },
  });
  expect(login.status()).toBe(200);
  return page;
}

test("the sections: one address each, one heading each, accessible, no sideways scroll", async ({ page, account: _ }) => {
  test.setTimeout(120_000); // fourteen sections, each with an accessibility scan
  await page.goto("/settings");
  await expect(page).toHaveURL("/settings/profile");
  for (const [slug, heading] of [
    ["profile", "Profile"],
    ["engineering", "Engineering Profile"],
    ["goals", "Skills & Certification Goals"],
    ["learning", "Learning Preferences"],
    ["tutor", "AI Assistant"],
    ["memory", "Memory"],
    ["subjects", "Practice & Lab Time"],
    ["progress", "Practice & Goals"],
    ["study-tracking", "Lab Tracking"],
    ["streak", "Practice Streak"],
    ["notifications", "Notifications"],
    ["appearance", "Appearance & Accessibility"],
    ["privacy", "Privacy & Security"],
    ["help", "Help & Support"],
  ] as const) {
    await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: heading, exact: true }).click();
    await expect(page).toHaveURL(`/settings/${slug}`);
    await expect(page.getByRole("heading", { level: 2, name: heading })).toBeVisible();
    await expect(page.getByRole("link", { name: heading, exact: true })).toHaveAttribute("aria-current", "page");
    await expectAccessible(page);
    await expectNoHorizontalOverflow(page);
  }
  await page.goto("/settings/nothing-here");
  await expect(page.getByRole("heading", { name: "This page could not be found" })).toBeVisible();
});

test("profile: name and time zone persist, on this device and another", async ({ page, browser, account }) => {
  await page.goto("/settings/profile");
  await page.getByRole("textbox", { name: "Display name" }).fill("Asha Settings");
  await page.getByLabel("Time zone").selectOption("Asia/Kolkata");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Profile saved.")).toBeVisible();

  await page.reload();
  await expect(page.getByRole("textbox", { name: "Display name" })).toHaveValue("Asha Settings");
  await expect(page.getByLabel("Time zone")).toHaveValue("Asia/Kolkata");

  const other = await secondDevice(browser, account);
  await other.goto("/settings/profile");
  await expect(other.getByRole("textbox", { name: "Display name" })).toHaveValue("Asha Settings");
  await other.context().close();
});

test("two tabs: a stale save is refused, the edit is kept and saves on the second try", async ({ page, account: _ }) => {
  const tab = await page.context().newPage();
  await page.goto("/settings/profile");
  await tab.goto("/settings/profile");

  await tab.getByRole("textbox", { name: "Display name" }).fill("From the other tab");
  await tab.getByRole("button", { name: "Save", exact: true }).click();
  await expect(tab.getByText("Profile saved.")).toBeVisible();

  await page.getByRole("textbox", { name: "Display name" }).fill("From this tab");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText(/changed somewhere else/)).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Display name" })).toHaveValue("From this tab"); // the edit is not lost
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Profile saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Display name" })).toHaveValue("From this tab");
  await tab.close();
});

test("leaving with unsaved changes asks first", async ({ page, account: _ }) => {
  await page.goto("/settings/profile");
  await page.getByRole("textbox", { name: "Display name" }).fill("Not saved yet");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("link", { name: "Notifications" }).click();
  await expect(page).toHaveURL("/settings/profile");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("link", { name: "Notifications" }).click();
  await expect(page).toHaveURL("/settings/notifications");
});

test("photo: upload, shown with the account, replace-safe, removed", async ({ page, account: _ }) => {
  await page.goto("/settings/profile");
  await page.locator('input[type="file"]').setInputFiles({ name: "me.png", mimeType: "image/png", buffer: PHOTO });
  await expect(page.getByText("Photo updated.")).toBeVisible();
  const photo = page.getByRole("main").locator('img[src^="/api/v1/me/avatar?v="]');
  await expect(photo).toBeVisible();
  const src = await photo.getAttribute("src");
  const response = await page.request.get(src as string);
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("image/webp");
  expect(response.headers()["cache-control"]).toContain("immutable");
  await expect(page.locator(`aside img[src="${src}"]`)).toBeVisible(); // the Settings panel's account

  await page.getByRole("button", { name: "Remove photo" }).click();
  await expect(page.getByText("Photo removed.")).toBeVisible();
  await expect(photo).toHaveCount(0);
  expect((await page.request.get(src as string)).status()).toBe(404); // the old address never serves again

  await page.locator('input[type="file"]').setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hi") });
  await expect(page.getByRole("alert").filter({ hasText: "Use a JPEG, PNG or WebP image." })).toBeVisible();
});

test("email: changed only after the code from the new address", async ({ page, account }) => {
  const next = uniqueEmail("moved");
  await page.goto("/settings/profile");
  await page.getByRole("button", { name: "Change email" }).click();
  const dialog = page.getByRole("dialog", { name: "Change your email" });
  await dialog.getByLabel("New email").fill(next);
  await dialog.getByLabel("Current password").fill("not-the-password");
  await dialog.getByRole("button", { name: "Send code" }).click();
  await expect(dialog.getByRole("alert")).toHaveText("That password isn't right.");

  await dialog.getByLabel("Current password").fill(account.password);
  await dialog.getByRole("button", { name: "Send code" }).click();
  const codeStep = page.getByRole("dialog", { name: "Enter the code" });
  await codeStep.getByLabel("Code").fill("000000");
  await codeStep.getByRole("button", { name: "Change email" }).click();
  await expect(codeStep.getByRole("alert")).toHaveText("That code is wrong or has expired.");
  await codeStep.getByLabel("Code").fill(await mailCode(next, "change"));
  await codeStep.getByRole("button", { name: "Change email" }).click();
  await expect(page.getByText(`Your email is now ${next}.`, { exact: false })).toBeVisible();
  await expect(page.getByRole("main").getByText(next)).toBeVisible();

  await page.request.post(`${APP_URL}/api/v1/auth/logout`, { headers: SAME_ORIGIN });
  const old = await page.request.post(`${APP_URL}/api/auth/sign-in/email`, {
    data: { email: account.email, password: PASSWORD },
    headers: SAME_ORIGIN,
  });
  expect(old.status()).toBe(401);
  const moved = await page.request.post(`${APP_URL}/api/auth/sign-in/email`, {
    data: { email: next, password: PASSWORD },
    headers: SAME_ORIGIN,
  });
  expect(moved.status()).toBe(200); // signed in again, so the fixture can delete the account
});

test("devices: another device is listed and can be signed out on its own", async ({ page, browser, account }) => {
  const other = await secondDevice(browser, account);
  await page.goto("/settings/privacy");
  // This device is shown; the others open on request (the card keeps one height, so the page never shifts).
  await expect(page.getByRole("listitem").filter({ hasText: "This device" })).toBeVisible();
  await page.getByRole("button", { name: "Show 1 other device" }).click();
  const row = page.getByRole("list", { name: "Other devices" }).getByRole("listitem").filter({ hasText: "Firefox on Linux" });
  await expect(row).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "This device" })).toBeVisible();
  await row.getByRole("button", { name: /^Sign out Firefox on Linux/ }).click();
  await expect(page.getByText("Signed out Firefox on Linux.")).toBeVisible();
  await expect(row).toHaveCount(0);
  await expect(page.getByText("No other devices.")).toBeVisible();
  expect((await other.request.get("/api/v1/me")).status()).toBe(401);
  expect((await page.request.get(`${APP_URL}/api/v1/me`)).status()).toBe(200);
  await other.context().close();
});

test("appearance: applies at once, persists, and follows the account to another device", async ({
  page,
  browser,
  account,
}) => {
  const html = page.locator("html");
  await page.goto("/settings/appearance");
  await page.getByRole("radio", { name: "Dark" }).check();
  await page.getByRole("radio", { name: "Large", exact: true }).check();
  await page.getByRole("switch", { name: "High contrast" }).click();
  await page.getByRole("switch", { name: "Comfortable reading" }).click();
  await expect(html).toHaveClass(/\bdark\b/);
  await expect(html).toHaveAttribute("data-font-size", "large");
  await expect(html).toHaveAttribute("data-contrast", "high");
  await expect(html).toHaveAttribute("data-reading", "comfort");
  await expectAccessible(page); // high contrast, dark

  await expect
    .poll(async () => (await (await page.request.get(`${APP_URL}/api/v1/me/appearance`)).json()).fontSize)
    .toBe("large");
  await page.reload();
  await expect(html).toHaveAttribute("data-font-size", "large");
  await expect(html).toHaveClass(/\bdark\b/);

  const other = await secondDevice(browser, account);
  await other.goto("/");
  await expect(other.locator("html")).toHaveAttribute("data-font-size", "large");
  await expect(other.locator("html")).toHaveAttribute("data-contrast", "high");
  await expect(other.locator("html")).toHaveClass(/\bdark\b/);
  await other.context().close();
});

test("notifications: honest about delivery, saved here and on another device, and never told to the tutor", async ({
  page,
  browser,
  account,
}) => {
  const saved = async (target: Page) => (await target.request.get(`${APP_URL}/api/v1/notifications/preferences`)).json();
  await page.goto("/settings/notifications");
  await expect(page.getByText("NetSec AI isn't sending notification emails yet.", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "Set your time zone" })).toBeVisible();
  const weekly = page.getByRole("switch", { name: "Weekly summary" });
  await expect(weekly).toHaveAttribute("aria-checked", "false");
  await weekly.click();
  await expect(weekly).toHaveAttribute("aria-checked", "true");
  await expect
    .poll(async () => (await (await page.request.get(`${APP_URL}/api/v1/notifications/preferences`)).json()).weeklySummary)
    .toBe(true);
  await page.reload();
  await expect(page.getByRole("switch", { name: "Weekly summary" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByRole("switch", { name: "Sign-in alerts" })).toHaveAttribute("aria-checked", "true");

  // Reminders in the app: off here, still off on another device, and back on from there.
  const inApp = (target: Page) => target.getByRole("switch", { name: "Reminders in the app" });
  await expect(inApp(page)).toBeChecked();
  await inApp(page).click();
  await expect.poll(async () => (await saved(page)).inAppReminders).toBe(false);
  await page.reload();
  await expect(inApp(page)).not.toBeChecked();
  const other = await secondDevice(browser, account);
  await other.goto("/settings/notifications");
  await expect(inApp(other)).not.toBeChecked();
  await inApp(other).click();
  await expect.poll(async () => (await saved(other)).inAppReminders).toBe(true);
  await other.context().close();
  await page.reload();
  await expect(inApp(page)).toBeChecked();

  // Delivery settings, not teaching: nothing of them reaches the tutor.
  expect(await tutorKnows(page)).not.toMatch(/reminder|summary|alert|notification/i);
});

// ------------------------------------------------------------------ learning

async function apiJson(page: Page, path: string) {
  return (await page.request.get(`${APP_URL}/api${path}`)).json();
}

test("engineering profile: catalog choices, chips within their limits, previewed, saved and told", async ({
  page,
  account: _,
}) => {
  // The old address still works.
  await page.goto("/settings/academic");
  await expect(page).toHaveURL("/settings/engineering");
  await expect(page.getByRole("heading", { level: 2, name: "Engineering Profile" })).toBeVisible();

  await page.getByLabel("Role").selectOption({ label: "Network Engineer" });
  await page.getByLabel("Experience").selectOption({ label: "Mid-level (3–5 years)" });
  await page.getByLabel("Certification goal").selectOption({ label: "CCNP Enterprise" });

  const focus = page.getByRole("group", { name: "Focus areas" });
  const first = "Routing and Switching";
  const areas = [
    first,
    "Wireless LAN",
    "WAN and SD-WAN",
    "Firewalls",
    "VPN (IPsec / SSL)",
    "Network Security",
    "Linux",
    "Network Automation",
  ];
  // Checked from the keyboard: Tab reaches a chip, Space checks it.
  await focus.getByRole("checkbox", { name: first }).focus();
  await page.keyboard.press("Space");
  await expect(focus.getByRole("checkbox", { name: first })).toBeChecked();
  for (const area of areas.slice(1)) await focus.getByRole("checkbox", { name: area }).check();
  await expect(focus.getByText("8 of 8 chosen, the most you can choose")).toBeVisible();
  await expect(focus.getByRole("checkbox", { name: "Cloud Infrastructure" })).toBeDisabled();
  await focus.getByRole("checkbox", { name: "Linux" }).uncheck();
  await expect(focus.getByRole("checkbox", { name: "Cloud Infrastructure" })).toBeEnabled();
  await expect(focus.getByText("7 of 8 chosen")).toBeVisible();

  // Focus areas and Platforms are two tabs of one card; each tab's chips are a named group.
  await page.getByRole("tab", { name: "Platforms" }).click();
  const platforms = page.getByRole("group", { name: "Platforms" });
  await platforms.getByRole("checkbox", { name: "Cisco IOS / IOS XE" }).check();
  await platforms.getByRole("checkbox", { name: "Palo Alto Networks PAN-OS" }).check();

  // The preview (opened from its card) is what saving would tell the assistant, before saving.
  await page.getByRole("button", { name: "Preview what AI sees" }).click();
  await expect(page.getByText("Role: Network Engineer", { exact: false })).toBeVisible();
  await expectAccessible(page);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Engineering Profile saved.")).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Role")).toHaveValue("network_engineer");
  await expect(page.getByRole("tab", { name: "Platforms" })).toContainText("2"); // the tab counts what is chosen
  await page.getByRole("tab", { name: "Platforms" }).click();
  await expect(platforms.getByRole("checkbox", { name: "Palo Alto Networks PAN-OS" })).toBeChecked();
  const profile = await apiJson(page, "/v1/engineering/profile");
  expect([profile.role, profile.experienceLevel, profile.certificationGoal, profile.focusAreas.length, profile.platforms]).toEqual([
    "network_engineer",
    "mid",
    "ccnp_enterprise",
    7,
    ["cisco_ios", "palo_alto"],
  ]);
  const told = await tutorKnows(page);
  for (const line of [
    "Role: Network Engineer",
    "Experience: Mid-level (3–5 years)",
    "Platforms: Cisco IOS / IOS XE; Palo Alto Networks PAN-OS",
    "Certification goal: CCNP Enterprise",
  ]) {
    expect(told).toContain(line);
  }

  await page.goto("/settings/engineering");
  await page.getByLabel("Role").selectOption({ label: "Not set" });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Engineering Profile saved.")).toBeVisible();
  expect((await apiJson(page, "/v1/engineering/profile")).role).toBeNull();
  expect(await tutorKnows(page)).not.toContain("Role: Network Engineer");
});

test("skills & certification goals: topics and goals, saved under their old codes", async ({ page, account: _ }) => {
  // Within the API's window for an exam date: from today to two years ahead.
  const exam = new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10);
  await page.goto("/settings/goals");
  await page.getByLabel("Main goal").selectOption({ label: "Prepare for an advanced certification (CCNP, CCIE, OSCP)" });
  await page.getByLabel("Certification or exam you're preparing for").fill("CCNP ENCOR 350-401");
  await page.getByLabel("Exam date").fill(exam);
  await page.getByLabel("Target score (%)").fill("90");
  const topic = page.getByLabel("Topics you find difficult", { exact: true });
  await topic.fill("BGP path selection");
  await topic.press("Enter");
  await topic.fill("bgp path selection");
  await topic.press("Enter");
  await expect(page.getByText("That topic is already on the list.")).toBeVisible();
  await topic.fill("OSPF areas");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByRole("button", { name: "Remove BGP path selection" }).click();
  await page.getByLabel("Short-term goal").fill("Finish the EIGRP labs");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Goals saved.")).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Certification or exam you're preparing for")).toHaveValue("CCNP ENCOR 350-401");
  await expect(page.getByLabel("Exam date")).toHaveValue(exam);
  await expect(page.getByRole("list", { name: "Your difficult topics" }).getByRole("listitem")).toHaveText(["OSPF areas"]);
  const profile = await apiJson(page, "/v1/learning/profile");
  expect([profile.mainGoal, profile.targetScorePercent, profile.examDate]).toEqual(["competitive_exam", 90, exam]);
  // Goals are kept for My Learning; the assistant is told the Engineering Profile instead.
  const told = await tutorKnows(page);
  for (const text of ["CCNP ENCOR 350-401", "OSPF areas", "Finish the EIGRP labs"]) expect(told).not.toContain(text);
});

test("learning preferences: saved as they change, in the preview, and told to the tutor", async ({ page, account: _ }) => {
  const language = page.getByRole("group", { name: "Reply language" });
  await page.goto("/settings/learning");
  await language.getByRole("radio", { name: "Hindi" }).check();
  await expect.poll(async () => (await apiJson(page, "/v1/learning/preferences")).language).toBe("hindi");

  await page.goto("/settings/tutor");
  await page.getByRole("button", { name: "Preview what AI sees" }).click();
  await expect(page.getByText("Reply in Hindi", { exact: false })).toBeVisible();
  expect(await tutorKnows(page)).toContain("Reply in Hindi");

  await page.goto("/settings/learning");
  await language.getByRole("radio", { name: "No preference" }).check();
  await expect.poll(async () => (await apiJson(page, "/v1/learning/preferences")).language).toBe("auto");
  expect(await tutorKnows(page)).not.toContain("Reply in Hindi");
});

test("AI tutor: choices save, the preview shows what the tutor is told, and the chat is told the same", async ({
  page,
  account: _,
}) => {
  const answers = page.getByRole("group", { name: "How it answers problems" });
  await page.goto("/settings/tutor");
  await expect(page.getByRole("heading", { name: "What your settings tell NetSec AI" })).toBeVisible();
  await page.getByRole("button", { name: "Preview what AI sees" }).click();
  await answers.getByRole("radio", { name: "Hint first" }).check();
  await expect(page.getByText("start with a hint", { exact: false })).toBeVisible();
  await expectAccessible(page);
  await expect.poll(async () => (await apiJson(page, "/v1/learning/preferences")).answerMode).toBe("hints_first");
  expect(await tutorKnows(page)).toContain("start with a hint");

  await page.goto("/settings/tutor");
  await page.getByRole("button", { name: "Preview what AI sees" }).click();
  await expect(page.getByText("start with a hint", { exact: false })).toBeVisible(); // what is saved, shown
  await answers.getByRole("radio", { name: "No preference" }).check();
  await expect(page.getByText("start with a hint", { exact: false })).toHaveCount(0);
});

test("practice & lab time: time goals saved, and the way to the Engineering Profile", async ({ page, account: _ }) => {
  await page.goto("/settings/subjects");
  await expect(page.getByRole("heading", { level: 2, name: "Practice & Lab Time" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Your subjects" })).toHaveCount(0); // superseded by focus areas
  await page.getByLabel("Daily goal (minutes)").fill("45");
  await page.getByLabel("Weekly goal (minutes)").fill("300");
  await page.getByLabel("Preferred practice time").fill("18:30");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Practice goals saved.")).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Daily goal (minutes)")).toHaveValue("45");
  const profile = await apiJson(page, "/v1/learning/profile");
  expect([profile.dailyActiveMinutesGoal, profile.weeklyActiveMinutesGoal, profile.preferredStudyTime]).toEqual([45, 300, "18:30"]);
  await expectAccessible(page);

  await page.getByRole("main").getByRole("link", { name: "Engineering Profile" }).click();
  await expect(page).toHaveURL("/settings/engineering");
  expect(await tutorKnows(page)).not.toContain("45 min");
});

// ------------------------------------------------------------------ progress

const headerStreak = (page: Page) => page.getByRole("banner").getByRole("link", { name: /day practice streak/ });
/** A number on the Progress page, by its label (the first, when Today and This week share one). */
const stat = (page: Page, label: string) =>
  page.locator("dt", { hasText: new RegExp(`^${label}$`) }).first().locator("xpath=following-sibling::dd");

test("progress: a question and an active minute count today, and the tutor is told how the student is doing", async ({
  page,
  account: _,
}) => {
  await page.goto("/");
  await sendAndWait(page, "What is photosynthesis?");
  const report = await page.request.post(`${APP_URL}/api/v1/progress/activity`, { headers: SAME_ORIGIN });
  expect(report.status()).toBe(200);
  expect((await report.json()).credited).toBe(true);
  await page.reload();
  await expect(headerStreak(page)).toHaveAccessibleName(/^1 day practice streak$/);
  const chat = new URL(page.url()).pathname;

  await openSettingsFromSidebar(page);
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Practice & Goals" }).click();
  await expect(page).toHaveURL("/settings/progress");
  await expect(page.getByText("about 1 min").first()).toBeVisible();
  await expect(stat(page, "question")).toHaveText("1");
  await expect(page.getByRole("link", { name: "Practice Streak" }).last()).toBeVisible();

  // The chart: one tab stop, arrow keys move between days, and every value is in the table too.
  const bars = page.getByRole("group", { name: /Active minutes, last 14 days/ }).getByRole("img");
  await expect(bars).toHaveCount(14);
  await bars.last().focus();
  await expect(bars.last()).toHaveAttribute("aria-label", /: 1 min active, 1 question$/);
  await page.keyboard.press("ArrowLeft");
  await expect(bars.nth(12)).toBeFocused();
  await expect(bars.nth(12)).toHaveAttribute("aria-label", /: 0 min active, 0 questions$/);
  await page.keyboard.press("End");
  await expect(bars.last()).toBeFocused();
  await page.getByRole("button", { name: "Show as table" }).click();
  const rows = page.getByRole("table").getByRole("row");
  await expect(rows).toHaveCount(15);
  await expect(rows.nth(1)).toContainText("1 min");
  await expectAccessible(page);
  await expectNoHorizontalOverflow(page);
  await page.emulateMedia({ colorScheme: "dark" });
  await expectAccessible(page);
  await page.emulateMedia({ colorScheme: "light" });

  await page.getByRole("link", { name: "Close settings" }).click();
  await expect(page).toHaveURL(chat);
  await expect(page.getByRole("navigation", { name: "Chats", exact: true })).toBeVisible();
  // Chatting alone tells the tutor nothing of this activity: only someone who practises with the question bank gets
  // a "Practice so far" section (app/learning/tutor.py, _practice_lines; its wording is tested in test_tutor_instructions).
  expect(await tutorKnows(page)).not.toMatch(/practice streak|min active/);
});

test("the heartbeat reports a minute while NetSec AI is used, and stops when it isn't", async ({ page, account: _ }) => {
  const reports: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/v1/progress/activity") reports.push(request.url());
  });
  await page.clock.install();
  await page.goto("/settings/progress");
  await expect(stat(page, "questions?")).toBeVisible();
  await page.getByRole("heading", { level: 2, name: "Practice & Goals" }).click();

  await page.clock.runFor(61_000);
  await expect.poll(() => reports.length).toBe(1);
  // A credited minute refreshes what is on screen.
  await expect(page.getByText("about 1 min").first()).toBeVisible();

  // Nobody touches it: reports stop within the two-minute window, and stay stopped.
  await page.clock.runFor(4 * 60_000);
  const afterIdle = reports.length;
  expect(afterIdle).toBeLessThanOrEqual(2);
  await page.clock.runFor(5 * 60_000);
  expect(reports.length).toBe(afterIdle);
});

// ------------------------------------------------------------------ help

test("help: a support request is sent once, even when the answer is lost, with a reference", async ({ page, account: _ }) => {
  await page.goto("/settings/help");
  await expect(page.getByRole("link", { name: "Help & FAQ" })).toHaveAttribute("href", "/help");
  await expect(page.getByRole("link", { name: "About NetSec AI" })).toHaveAttribute("href", "/about");
  await expect(page.getByRole("link", { name: /Terms of Service|Privacy Policy/ })).toHaveCount(0); // not published yet

  await page.getByRole("radio", { name: "Something isn't working" }).check();
  await page.getByLabel("Subject").fill("The chart looks odd");
  await page.getByLabel("Message").fill("Too short");
  await expect(page.getByText("Write at least 10 characters.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
  await page.getByLabel("Message").fill("The progress chart shows no bar for today although I asked a question.");
  await expectAccessible(page);

  // The first answer is lost on the way back (the request reached the API); sending again
  // must not make a second request, and must show the first one's reference.
  const bodies: { requestId: string }[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/v1/support/requests")) bodies.push(request.postDataJSON());
  });
  let firstId = "";
  await page.route(
    "**/api/v1/support/requests",
    async (route) => {
      firstId = (await (await route.fetch()).json()).id;
      await route.abort("connectionreset");
    },
    { times: 1 },
  );
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "couldn't reach the server" })).toBeVisible();
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("We've received your request.")).toBeVisible();
  expect(bodies).toHaveLength(2);
  expect(bodies[1]?.requestId).toBe(bodies[0]?.requestId);
  expect(firstId).not.toBe("");
  await expect(page.getByText(firstId.replaceAll("-", "").slice(0, 8).toUpperCase(), { exact: true })).toBeVisible();
  await expectAccessible(page);

  await page.getByRole("button", { name: "Send another" }).click();
  await expect(page.getByLabel("Subject")).toHaveValue("");
});

for (const { path, h1, h2 } of [
  { path: "/help", h1: "Help", h2: "Help & FAQ" },
  { path: "/about", h1: "About", h2: "About NetSec AI" },
]) {
  test(`${h2}: readable, accessible, fits a phone`, async ({ page, account: _ }) => {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: h1, exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: h2 })).toBeVisible();
    if (path === "/help") {
      // One topic at a time (tabs): the answer is under Practice and progress.
      await page.getByRole("tab", { name: /^Practice and progress/ }).click();
      await page.getByText("How is active time measured?").click();
      await expect(page.getByText(/A minute counts when NetSec AI is open and on screen/)).toBeVisible();
    }
    await expectAccessible(page);
    await page.setViewportSize({ width: 320, height: 700 });
    await expectNoHorizontalOverflow(page);
    await page.emulateMedia({ colorScheme: "dark" });
    await expectAccessible(page);
  });
}

// ------------------------------------------------------------------ unsubscribe

function fakeToken(kind: string): string {
  const payload = Buffer.from(`00000000-0000-4000-8000-000000000000:${kind}`).toString("base64url");
  return `${payload}.${"0".repeat(32)}`;
}

test("unsubscribe: works signed out, asks first, refuses a link we didn't sign", async ({ browser }) => {
  const context = await browser.newContext({ baseURL: APP_URL });
  const page = await context.newPage();
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/api/v1/notifications/unsubscribe")) posts.push(request.url());
  });

  await page.goto(`/unsubscribe?token=${fakeToken("daily_reminder")}`);
  await expect(page).toHaveURL(/\/unsubscribe\?token=/); // not sent to sign in
  await expect(page.getByRole("heading", { level: 1, name: "Stop daily practice reminders?" })).toBeVisible();
  expect(posts).toHaveLength(0); // opening the link changes nothing
  await expectAccessible(page);
  await page.getByRole("button", { name: "Unsubscribe" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "This link doesn't work" })).toBeFocused();
  expect(posts).toHaveLength(1);

  // A link the API accepts. Signing one needs the server's key, so the answer is stubbed here;
  // the API's own tests cover real tokens.
  await page.route("**/api/v1/notifications/unsubscribe", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ kind: "weekly_summary" }) }),
  );
  await page.goto(`/unsubscribe?token=${fakeToken("weekly_summary")}`);
  await page.getByRole("button", { name: "Unsubscribe" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "You're unsubscribed" })).toBeFocused();
  await expect(page.getByText("You won't get weekly summaries from NetSec AI any more.", { exact: false })).toBeVisible();
  await expectAccessible(page);

  await page.goto("/unsubscribe");
  await expect(page.getByRole("heading", { level: 1, name: "This link is incomplete" })).toBeVisible();
  await page.setViewportSize({ width: 320, height: 700 });
  await expectNoHorizontalOverflow(page);
  await context.close();
});
