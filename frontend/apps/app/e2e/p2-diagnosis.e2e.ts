/** Diagnose the headed run: what does the page actually show? */
import { expect, test } from "./support";

test("headed diagnosis", async ({ page, account }) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await page.waitForTimeout(3000);
  console.log("URL:", page.url());
  const themeBtn = await page.getByRole("button", { name: "Colour theme" }).count();
  console.log("THEME BUTTON COUNT:", themeBtn);
  // The real click
  await page.getByRole("button", { name: "Colour theme" }).click({ timeout: 20_000 });
  console.log("CLICK OK");
  await expect(page.getByRole("menu")).toBeVisible({ timeout: 10_000 });
  console.log("MENU OPEN");
  await page.getByRole("menuitemradio", { name: "Dark" }).click({ timeout: 20_000 });
  console.log("DARK CLICKED");
  await expect(page.locator("html")).toHaveClass(/dark/);
  console.log("DARK APPLIED");
});

