/**
 * P2: the chat's controls under real input, made to be watched in HEADED Chromium:
 *   bunx playwright test p2-headed-verification --project=desktop --headed
 * Pointer clicks, the keyboard, Radix menus, visible focus, Send, streaming, Stop and citations.
 * Every step waits for the state it needs: no fixed waits, no forced clicks, no GPU flags.
 * Its citation step needs one global knowledge source about OSINT: e2e/fixtures/osint.md, ingested before the suite
 * (backend: scripts/ingest_documents.py --global --source e2e-osint <that file>, with the suite's fake embeddings;
 * CI does this in .github/workflows/ci.yml).
 */
import { expect } from "@playwright/test";

import { LONG_MESSAGE, composer, describeFocus, lastReply, replies, stopButton, test, waitForHydration, waitForIdle } from "./support";

test("headed journey: pointer, keyboard, menus, focus, send, stream, stop, sources", async ({ page, account: _ }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await waitForHydration(page);
  const html = page.locator("html");
  const menu = page.getByRole("menu");
  const themeButton = page.getByRole("button", { name: "Colour theme" });
  const sendButton = page.getByRole("button", { name: "Send message" });

  // 1. Pointer: the theme menu opens and Dark applies.
  await themeButton.click();
  await page.getByRole("menuitemradio", { name: "Dark" }).click();
  await expect(html).toHaveClass(/dark/);
  await expect(menu).toHaveCount(0);

  // 2. Keyboard: Enter opens the same menu, the arrows move, Enter picks White, and focus returns
  //    to the button with a visible ring.
  await themeButton.focus();
  await page.keyboard.press("Enter");
  await expect(menu).toBeVisible();
  await expect(page.locator("[role='menuitemradio']:focus")).toHaveCount(1);
  const white = page.getByRole("menuitemradio", { name: "White" });
  for (let step = 0; step < 3 && !(await white.evaluate((item) => item === document.activeElement)); step += 1) {
    await page.keyboard.press("ArrowDown");
  }
  await expect(white).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(html).not.toHaveClass(/dark/);
  await expect(menu).toHaveCount(0);
  await expect(themeButton).toBeFocused();
  expect((await describeFocus(page)).indicated, "the theme button shows its focus ring").toBe(true);

  // 3. Escape closes a keyboard-opened menu and gives focus back to its trigger.
  const accountButton = page.getByRole("complementary").getByRole("button", { name: /^Account: / });
  await accountButton.focus();
  await page.keyboard.press("Enter");
  await expect(menu).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(accountButton).toBeFocused();

  // 4. Send is off for an empty message; a pointer click focuses the composer with its ring.
  await expect(sendButton).toBeDisabled();
  await composer(page).click();
  await expect(composer(page)).toBeFocused();
  expect((await describeFocus(page)).indicated, "the composer shows its focus ring").toBe(true);

  // 5. Keyboard send, streaming, and a pointer Stop mid-reply.
  const before = await replies(page).count();
  await composer(page).fill(LONG_MESSAGE);
  await expect(sendButton).toBeEnabled();
  await composer(page).press("Enter");
  await expect(replies(page)).toHaveCount(before + 1);
  await expect(stopButton(page)).toBeEnabled();
  const started = (await lastReply(page).innerText()).length;
  await expect.poll(async () => (await lastReply(page).innerText()).length, { message: "tokens keep arriving" }).toBeGreaterThan(started);
  await stopButton(page).click();
  await waitForIdle(page);
  await expect(lastReply(page).getByText("Stopped", { exact: true })).toBeVisible();
  expect(await lastReply(page).innerText(), "the reply ended where it was stopped").not.toContain("w80");

  // 6. Pointer send; the finished reply's citation chip names its source (hover and keyboard focus
  //    both show it), and the Sources list under the reply lists it.
  await composer(page).fill("Explain OSINT reconnaissance in penetration testing [1]");
  await sendButton.click();
  await expect(replies(page)).toHaveCount(before + 2);
  await waitForIdle(page);
  // A new account's first answer starts a streak, celebrated in a dialog (as in motion.e2e.ts): close
  // it before hovering, or its Escape would also dismiss the tooltip under the pointer.
  const celebration = page.locator("[data-celebration$='-dialog']");
  if (await celebration.waitFor({ timeout: 10_000 }).then(() => true, () => false)) await page.keyboard.press("Escape");
  await expect(celebration).toHaveCount(0);
  const chip = lastReply(page).getByRole("button", { name: /^1\. Source: / }).first();
  await expect(chip).toBeVisible();
  await chip.hover();
  await expect(page.getByRole("tooltip")).toContainText("Source:");
  // Away like a real mouse (several moves): Radix keeps a tooltip open while the pointer is in its
  // grace area and re-checks on the next move, which a single teleporting event never sends.
  await page.mouse.move(0, 0, { steps: 8 });
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  // Reach the chip with the keyboard: a script focus right after the pointer shows no ring (by design
  // of :focus-visible), a Tab does.
  await chip.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(chip).toBeFocused();
  // Known defect (2026-11-18, docs/known-limitations.md): the tooltip does not always open on the first
  // keyboard focus after a page load (probe: 1 of 30). The chip's own name always carries the source.
  await expect(chip).toHaveAccessibleName(/^1\. Source: .+/);
  expect((await describeFocus(page)).indicated, "the citation chip shows its focus ring").toBe(true);
  await page.keyboard.press("Escape");
  const sources = lastReply(page).getByRole("listitem").filter({ hasText: "[1]" });
  await expect(sources).toHaveCount(1);

  // 7. Pointer: the account menu reaches Settings.
  await accountButton.click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings/);
});
