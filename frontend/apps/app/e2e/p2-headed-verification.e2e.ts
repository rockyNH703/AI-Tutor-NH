/**
 * P2 verification: real pointer interactions in HEADED Chromium against the live local app.
 * Run: bunx playwright test p2-headed-verification --project=desktop --headed
 * (The config's webServer is reused; this file only exercises real clicks.)
 */
import { expect } from "@playwright/test";

import { composer, lastReply, test } from "./support";

// Headed Chromium on machines with a flaky GPU compositor can stop producing frames, which
// stalls Playwright's actionability sampling forever. Software rendering makes frame
// production deterministic; the app itself is unchanged.
test.use({ launchOptions: { args: ["--disable-gpu"] } });

test("headed real-click journey: menus, composer, send, streaming, citations", async ({ page, account: _ }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await page.waitForTimeout(1500);

  // 1. Real click opens the theme menu (Radix popover) and picks Dark.
  await page.getByRole("button", { name: "Colour theme" }).click({ timeout: 20_000 });
  await expect(page.getByRole("menu")).toBeVisible();
  await page.getByRole("menuitemradio", { name: "Dark" }).click({ timeout: 15_000 });
  await expect(page.locator("html")).toHaveClass(/dark/);

  // 2. Real click focuses the composer; typing enables Send; a real click sends.
  await composer(page).click({ timeout: 15_000 });
  await page.keyboard.type("What is OSINT in penetration testing?");
  await page.getByRole("button", { name: "Send message" }).click({ timeout: 15_000 });

  // 3. The reply streams and finishes, with CPENT-scoped citations (the account's goal).
  await expect(lastReply(page)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: /^(Stop generating|Stopping)$/ })).toHaveCount(0, { timeout: 60_000 });
  const reply = await lastReply(page).innerText();
  expect(reply, "the retrieval is scoped to the CPENT course").toContain("CPENT shared library");

  // 4. The account menu opens with a real click and Settings is reachable. The first answer's
  //    streak celebration may still be up: dismiss it first (support.ts's handler also does).
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);
  await page.getByRole("button", { name: /^Account: / }).click({ timeout: 15_000 });
  await expect(page.getByRole("menu")).toBeVisible();
  await page.getByRole("menuitem", { name: "Settings" }).click({ timeout: 15_000 });
  await expect(page).toHaveURL(/\/settings/);
});
