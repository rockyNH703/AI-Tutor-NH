import type { Page } from "@playwright/test";

import { APP_URL } from "./env";
import { chatAction, expect, expectAccessible, seedTurn, test, waitForHydration } from "./support";

/**
 * Pin, archive and share chats (features/conversations), and the sidebar's resizable edge (components/sidebar-resizer).
 * Every change is checked after a reload, so it is the server's state, not the screen's.
 */

const chats = (page: Page) => page.locator("nav[aria-labelledby='chats-heading']");
const rowOf = (page: Page, id: string) => chats(page).locator("li", { has: page.locator(`a[href="/c/${id}"]`) });

test("pin, unpin, archive, restore and delete from Archived chats, all kept across a reload", async ({ page, account: _ }) => {
  const first = await seedTurn(page, "First chat about OSPF");
  const second = await seedTurn(page, "Second chat about BGP");
  await page.goto("/");
  await waitForHydration(page);

  // Pin: the chat moves to its own Pinned group at the top, once.
  await chatAction(rowOf(page, first), "Pin");
  const pinned = page.getByRole("list", { name: "Pinned chats" });
  await expect(pinned.locator(`a[href="/c/${first}"]`)).toBeVisible();
  await expect(chats(page).locator(`a[href="/c/${first}"]`)).toHaveCount(1);
  await page.reload();
  await expect(pinned.locator(`a[href="/c/${first}"]`)).toBeVisible();
  await expectAccessible(page);

  // Unpin: back with the others; the Pinned group disappears when empty.
  await chatAction(rowOf(page, first), "Unpin");
  await expect(pinned).toHaveCount(0);
  await page.reload();
  await expect(pinned).toHaveCount(0);
  await expect(chats(page).locator(`a[href="/c/${first}"]`)).toHaveCount(1);

  // Archive: out of the sidebar, in Archived chats.
  await chatAction(rowOf(page, second), "Archive");
  await expect(chats(page).locator(`a[href="/c/${second}"]`)).toHaveCount(0);
  await page.reload();
  await expect(chats(page).locator(`a[href="/c/${second}"]`)).toHaveCount(0);
  await page.getByRole("complementary").getByRole("button", { name: /^Account: / }).click();
  await page.getByRole("menuitem", { name: "Archived chats" }).click();
  await expect(page).toHaveURL("/archived");
  const archived = page.getByRole("list", { name: "Archived chats" });
  await expect(archived.getByText("Second chat about BGP")).toBeVisible();
  await expectAccessible(page);

  // Restore: back in the sidebar.
  await archived.getByRole("button", { name: /^Restore/ }).click();
  await expect(page.getByRole("main").getByText("No archived chats")).toBeVisible();
  await expect(chats(page).locator(`a[href="/c/${second}"]`)).toHaveCount(1);

  // Archive again, then delete for good after a confirmation: gone from everywhere, and the messages with it.
  await chatAction(rowOf(page, second), "Archive");
  await page.goto("/archived");
  await page.getByRole("list", { name: "Archived chats" }).getByRole("button", { name: /^Delete/ }).click();
  const confirm = page.getByRole("dialog", { name: "Delete this chat for good?" });
  await confirm.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByRole("main").getByText("No archived chats")).toBeVisible();
  expect((await page.request.get(`${APP_URL}/api/v1/conversations/${second}`)).status()).toBe(404);
});

test("share: a read-only link that works signed out, updates to a new link, and stops when sharing stops", async ({
  page,
  browser,
  account: _,
}) => {
  const id = await seedTurn(page, "Explain VLAN trunking");
  await page.goto(`/c/${id}`);
  await waitForHydration(page);

  // From the chat's own header; nothing is shared until "Create link".
  await page.getByRole("banner").getByRole("button", { name: "Share chat" }).click();
  const dialog = page.getByRole("dialog", { name: "Share chat" });
  await expect(dialog.getByText("Not shared.")).toBeVisible();
  await expectAccessible(page);
  await dialog.getByRole("button", { name: "Create link" }).click();
  const field = dialog.getByLabel("Link to this chat");
  await expect(field).toHaveValue(/\/share\/[A-Za-z0-9_-]{32}$/);
  const link = await field.inputValue();
  expect(link).not.toContain(id); // never the chat's id
  // Copy link puts exactly that link on the clipboard, and says so.
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await dialog.getByRole("button", { name: "Copy link" }).click();
  await expect(dialog.getByRole("button", { name: "Copied" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(link);

  // Opened signed out (another browser context): the messages, read-only, not indexed.
  const stranger = await browser.newContext();
  const reader = await stranger.newPage();
  const response = await reader.goto(link);
  expect(response?.status()).toBe(200);
  expect(response?.headers()["x-robots-tag"]).toContain("noindex");
  await expect(reader.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(reader.getByText("Explain VLAN trunking", { exact: true })).toBeVisible();
  await expect(reader.getByText("You said: Explain VLAN trunking")).toBeVisible();
  await expect(reader.getByRole("textbox")).toHaveCount(0); // nothing to type into
  await expectAccessible(reader);

  // Update link (from the sidebar's menu this time): a new link; the old one stops at once.
  await page.keyboard.press("Escape");
  await chatAction(rowOf(page, id), "Share");
  await expect(dialog.getByText(/^Shared /)).toBeVisible();
  await dialog.getByRole("button", { name: "Update link" }).click();
  await expect(field).toHaveValue(/\/share\/[A-Za-z0-9_-]{32}$/);
  const newer = await field.inputValue();
  expect(newer).not.toBe(link);
  expect((await reader.goto(link))?.status()).toBe(404);
  expect((await reader.goto(newer))?.status()).toBe(200);

  // Stop sharing: the new link stops too.
  await dialog.getByRole("button", { name: "Stop sharing" }).click();
  await expect(dialog.getByText("Not shared.")).toBeVisible();
  expect((await reader.goto(newer))?.status()).toBe(404);
  await stranger.close();
});

test("the sidebar's edge resizes it between 200 and 420px, by pointer and keys, and the width survives a reload", async ({
  page,
  account: _,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await waitForHydration(page);
  const rail = page.locator("#app-sidebar");
  const handle = page.getByRole("separator", { name: "Resize sidebar" });
  const width = async () => Math.round((await rail.boundingBox())?.width ?? 0);
  await expect(handle).toHaveAttribute("aria-valuenow", "260");
  expect(await width()).toBe(260);

  // Drag far right, then far left: held at the limits; a quick drag lands where it was released.
  const box = await handle.boundingBox();
  if (!box) throw new Error("no handle");
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width / 2, y);
  await page.mouse.down();
  for (const x of [400, 700, 900]) await page.mouse.move(x, y);
  await page.mouse.up();
  await expect(handle).toHaveAttribute("aria-valuenow", "420");
  await expect.poll(width).toBe(420);
  const right = await handle.boundingBox();
  await page.mouse.move((right?.x ?? 0) + 6, y);
  await page.mouse.down();
  await page.mouse.move(50, y, { steps: 3 });
  await page.mouse.up();
  await expect(handle).toHaveAttribute("aria-valuenow", "200");
  await expect.poll(width).toBe(200);
  expect(await page.evaluate(() => document.body.style.cursor)).toBe(""); // the drag's cursor is gone

  // Keys: arrows step it, End and Home go to the limits, Shift steps further.
  await handle.focus();
  await page.keyboard.press("ArrowRight");
  await expect(handle).toHaveAttribute("aria-valuenow", "216");
  await page.keyboard.press("End");
  await expect(handle).toHaveAttribute("aria-valuenow", "420");
  await page.keyboard.press("Home");
  await expect(handle).toHaveAttribute("aria-valuenow", "200");
  await page.keyboard.press("Shift+ArrowRight");
  await expect(handle).toHaveAttribute("aria-valuenow", "248");

  // Kept across a reload (a cookie the server reads, so the first paint already has it).
  await page.reload();
  await expect(handle).toHaveAttribute("aria-valuenow", "248");
  expect(await width()).toBe(248);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  // Folded to icons: no edge to drag. On a phone: the drawer, with no edge at all.
  await page.getByRole("banner").getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(handle).toHaveCount(0);
  await page.getByRole("banner").getByRole("button", { name: "Expand sidebar" }).click();
  await expect(handle).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(handle).toBeHidden();
});
