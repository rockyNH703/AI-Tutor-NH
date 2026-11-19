import { readFileSync } from "node:fs";

import type { Page } from "@playwright/test";

import { expectNoHorizontalOverflow, waitForHydration } from "../support";
import { blockOf, expect, openFixture, test } from "./support";
import { COPY, at, card, expectAccessible22, shot } from "./ui";

/**
 * :::cheatsheet (plan §3 "Cheatsheet" and "Print", §7 "Renderers"): a long sheet with tables at 320 px,
 * highlighting, maths and mermaid; sections and Collapse/Expand all; Copy code and Copy as Markdown through a
 * recording clipboard stub, a rejecting one (a toast), and one real clipboard read-back (headless); Download .md
 * and a download that throws; an exam inside a sheet exports no key; Print recorded at call time, afterprint
 * restoring the page, a PDF's page count (headless), and print media on pages without a printed sheet.
 */

const SECTIONS = ["Interfaces", "Routing", "Subnetting", "Path to the internet", "Logs", "Next steps"];

/** navigator.clipboard replaced by a recorder (window.__clipboard), or one that refuses (window.__clipboardRefuses). */
async function recordClipboard(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const target = window as unknown as { __clipboard: string[]; __clipboardRefuses?: boolean };
    target.__clipboard = [];
    const clipboard = {
      writeText: async (text: string) => {
        if (target.__clipboardRefuses) throw new DOMException("Refused by the test", "NotAllowedError");
        target.__clipboard.push(text);
      },
      write: async () => {
        if (target.__clipboardRefuses) throw new DOMException("Refused by the test", "NotAllowedError");
      },
    };
    Object.defineProperty(Navigator.prototype, "clipboard", { get: () => clipboard, configurable: true });
  });
}

const copied = (page: Page) => page.evaluate(() => (window as unknown as { __clipboard: string[] }).__clipboard.at(-1) ?? "");
const errorToast = (page: Page) => page.locator("[data-sonner-toast][data-type='error']");

test.describe("320px", () => {
  test.use(at(320));
  test("a long sheet with tables, highlighting, maths and mermaid, all sections open", async ({ page }, testInfo) => {
    const { reply } = await openFixture(page, "cheatsheet");
    const sheet = card(reply);
    for (const name of SECTIONS) await expect(sheet.getByRole("button", { name, exact: true })).toHaveAttribute("aria-expanded", "true");
    const wide = sheet.locator("table").nth(1);
    const scroller = wide.locator("xpath=ancestor::*[@tabindex='0'][1]");
    expect(await scroller.evaluate((element) => element.scrollWidth > element.clientWidth), "the wide table scrolls in its own region").toBe(true);
    await expect(sheet.locator("code [class*='hljs-']").first()).toBeVisible();
    await expect(sheet.locator(".katex").first()).toBeVisible();
    await expect(sheet.getByRole("img").first()).toBeVisible(); // the mermaid diagram, drawn as an image
    await expectNoHorizontalOverflow(page);
    await expectAccessible22(page);
    await shot(page, testInfo, "sheet-320");
  });
});

test("a section folds; Collapse all / Expand all keep their button in view and Jump to latest appears", { tag: "@demo" }, async ({ page }, testInfo) => {
  const { reply } = await openFixture(page, "cheatsheet");
  const sheet = card(reply);
  const interfaces = sheet.getByRole("button", { name: "Interfaces", exact: true });
  await interfaces.click();
  await expect(interfaces).toHaveAttribute("aria-expanded", "false");
  await expect(sheet.getByText("show ip interface brief").first()).toBeHidden();
  await interfaces.click();
  await expect(interfaces).toHaveAttribute("aria-expanded", "true");

  const collapse = sheet.getByRole("button", { name: COPY.collapseAll });
  await collapse.click();
  for (const name of SECTIONS) await expect(sheet.getByRole("button", { name, exact: true })).toHaveAttribute("aria-expanded", "false");
  const expand = sheet.getByRole("button", { name: COPY.expandAll });
  await expect(expand).toBeInViewport();
  await expand.click();
  for (const name of SECTIONS) await expect(sheet.getByRole("button", { name, exact: true })).toHaveAttribute("aria-expanded", "true");
  await expect(sheet.getByRole("button", { name: COPY.collapseAll })).toBeInViewport();
  await expect(page.getByRole("button", { name: COPY.jumpToLatest })).toBeVisible();
  await shot(page, testInfo, "expanded");
});

test("Copy code and Copy as Markdown: recorded, announced; refused: a toast", async ({ page, pageGuard }) => {
  pageGuard.allow(/Refused by the test/);
  await recordClipboard(page);
  const { reply } = await openFixture(page, "cheatsheet");
  const sheet = card(reply);

  await sheet.getByRole("button", { name: "Copy code" }).first().click();
  await expect.poll(() => copied(page)).toContain("ip route get 10.20.0.1");

  await sheet.getByRole("button", { name: COPY.copyMarkdown }).click();
  await expect.poll(() => copied(page)).toContain("## Interfaces");
  const markdown = await copied(page);
  expect(markdown).toContain("| Cisco IOS |");
  expect(markdown).toContain("$2^{h} - 2$");
  expect(markdown).toContain("```mermaid");
  // Announced by a polite live region (sr-only, cheatsheet.tsx), cleared again after 1.5 s.
  await expect(page.locator("[aria-live='polite']").filter({ hasText: COPY.copied }).first()).toBeAttached();

  await page.evaluate(() => {
    (window as unknown as { __clipboardRefuses: boolean }).__clipboardRefuses = true;
  });
  await sheet.getByRole("button", { name: COPY.copyMarkdown }).click();
  await expect(errorToast(page)).toBeVisible();
  await sheet.getByRole("button", { name: "Copy code" }).first().click();
  await expect(errorToast(page).first()).toBeVisible();
});

test("Download .md saves the sheet; a download that throws shows a toast", async ({ page, pageGuard }) => {
  pageGuard.allow(/blocked by the test/);
  const { reply } = await openFixture(page, "cheatsheet");
  const sheet = card(reply);
  const [download] = await Promise.all([page.waitForEvent("download"), sheet.getByRole("button", { name: COPY.downloadMarkdown }).click()]);
  expect(download.suggestedFilename()).toMatch(/\.md$/);
  const saved = readFileSync(await download.path(), "utf8");
  expect(saved).toContain("## Interfaces");
  expect(saved).toContain("| /26 | 255.255.255.192 | 62 |");

  await page.evaluate(() => {
    URL.createObjectURL = () => {
      throw new Error("blocked by the test");
    };
  });
  await sheet.getByRole("button", { name: COPY.downloadMarkdown }).click();
  await expect(errorToast(page)).toBeVisible();
});

test("an exam inside a sheet exports no key (Copy and Download)", async ({ page }) => {
  await recordClipboard(page);
  const { reply } = await openFixture(page, "cheatsheet-exam");
  const sheet = card(reply);
  const exam = blockOf("cheatsheet-exam");
  const keys = [`"answer"`, exam.questions[0].explanation as string];

  await sheet.getByRole("button", { name: COPY.copyMarkdown }).click();
  await expect.poll(() => copied(page)).toContain("## Layers");
  for (const key of keys) expect(await copied(page)).not.toContain(key);

  const [download] = await Promise.all([page.waitForEvent("download"), sheet.getByRole("button", { name: COPY.downloadMarkdown }).click()]);
  const saved = readFileSync(await download.path(), "utf8");
  expect(saved).toContain("## Layers");
  for (const key of keys) expect(saved).not.toContain(key);
});

type Printed = { root: boolean; inContent: boolean; image: boolean; text: string; dark: boolean; scheme: string; mode: string | null };

test("Print: what the page is at the moment of printing, and afterprint restores it", async ({ page }, testInfo) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => {
    const target = window as unknown as { __printed: Printed[] };
    target.__printed = [];
    window.print = () => {
      const html = document.documentElement;
      const content = document.querySelector("#print-root > .message-content");
      target.__printed.push({
        root: document.querySelector("#print-root") !== null,
        inContent: content !== null,
        image: content?.querySelector("svg, img, canvas") != null,
        text: content?.textContent ?? "",
        dark: html.classList.contains("dark"),
        scheme: html.style.colorScheme,
        mode: html.getAttribute("data-print"),
      });
    };
  });
  const { reply } = await openFixture(page, "cheatsheet");
  const sheet = card(reply);
  await expect(sheet.getByRole("img").first()).toBeVisible();
  const before = await page.evaluate(() => ({ dark: document.documentElement.classList.contains("dark"), scheme: document.documentElement.style.colorScheme }));
  expect(before.dark, "drawn in dark mode").toBe(true);

  await sheet.getByRole("button", { name: COPY.print }).click();
  const printed = await page.evaluate(() => (window as unknown as { __printed: Printed[] }).__printed);
  expect(printed).toHaveLength(1);
  const [call] = printed;
  expect(call).toMatchObject({ root: true, inContent: true, image: true, dark: false, scheme: "light", mode: "sheet" });
  expect(call?.text).toContain("Interfaces");
  expect(call?.text).not.toMatch(/Preparing|Loading/);
  await shot(page, testInfo, "printing");

  await page.evaluate(() => window.dispatchEvent(new Event("afterprint")));
  await expect(page.locator("#print-root")).toHaveCount(0);
  const after = await page.evaluate(() => ({
    dark: document.documentElement.classList.contains("dark"),
    scheme: document.documentElement.style.colorScheme,
    mode: document.documentElement.getAttribute("data-print"),
  }));
  expect(after).toEqual({ ...before, mode: null });
});

test("Print to PDF: the sheet alone, a few pages", { tag: "@pdf" }, async ({ page }) => {
  await page.addInitScript(() => {
    window.print = () => {}; // page.pdf() prints instead; afterprint never fires, so the print state stays
  });
  const { reply } = await openFixture(page, "cheatsheet");
  await card(reply).getByRole("button", { name: COPY.print }).click();
  await expect(page.locator("html")).toHaveAttribute("data-print", "sheet");
  const pdf = (await page.pdf({ format: "A4" })).toString("latin1");
  const pages = pdf.match(/\/Type\s*\/Page(?!s)/g)?.length ?? 0;
  expect(pages, "the sheet prints on a few pages").toBeGreaterThanOrEqual(1);
  expect(pages).toBeLessThanOrEqual(6);
});

test("the real clipboard holds the copied Markdown", { tag: "@pdf" }, async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const { reply } = await openFixture(page, "cheatsheet");
  await card(reply).getByRole("button", { name: COPY.copyMarkdown }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("## Interfaces");
});

test("print media without a printed sheet keeps the page (settings, and a chat with a sheet)", async ({ page }) => {
  await page.emulateMedia({ media: "print" });
  await page.goto("/settings");
  await waitForHydration(page);
  await expect(page.locator("#main")).toBeVisible();
  const { reply } = await openFixture(page, "cheatsheet");
  await expect(page.locator("#main")).toBeVisible();
  await expect(card(reply)).toBeVisible();
});
