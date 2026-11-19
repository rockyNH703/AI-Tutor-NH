import { expectNoHorizontalOverflow } from "../support";
import { expect, openFixture, test } from "./support";
import { COPY, card, question, shot } from "./ui";

/**
 * Safari-class input on a phone (plan §7 "learning-webkit": iPhone 14 for numeric, cli and video; the video
 * check is tagged in video.learning.ts). The numeric field is a plain text field with no inputMode, because the
 * iPhone decimal pad has no minus key; neither field capitalises, autocompletes or spell-checks a command.
 */

test("numeric: a text field the minus key can reach, checked with Enter", { tag: "@webkit" }, async ({ page }, testInfo) => {
  const { reply } = await openFixture(page, "kind-numeric");
  const field = question(reply).getByRole("textbox");
  await expect(field).toHaveAttribute("type", "text");
  expect(await field.getAttribute("inputmode"), "no inputMode: the decimal pad has no minus").toBeNull();
  await expect(field).toHaveAttribute("autocomplete", "off");
  await expect(field).toHaveAttribute("autocapitalize", "none");
  await expect(field).toHaveAttribute("spellcheck", "false");
  await expect(card(reply)).toContainText(COPY.numericHint);
  await field.click();
  await page.keyboard.type("-67");
  await page.keyboard.press("Enter");
  await expect(card(reply)).toContainText(COPY.progress(2, 2, 1, 1));
  await expectNoHorizontalOverflow(page);
  await shot(page, testInfo, "numeric-webkit");
});

test("cli: no capitals, autocomplete or spell-check, the prompt read with the field", { tag: "@webkit" }, async ({ page }, testInfo) => {
  const { reply } = await openFixture(page, "kind-cli");
  const field = question(reply).getByRole("textbox");
  await expect(field).toHaveAttribute("autocapitalize", /^(?:none|off)$/); // HTML synonyms: no capitals
  await expect(field).toHaveAttribute("autocomplete", "off");
  await expect(field).toHaveAttribute("spellcheck", "false");
  await expect(field).toHaveAccessibleDescription(/R1#/);
  await field.click();
  await page.keyboard.type("sh ip int br");
  await page.keyboard.press("Enter");
  await expect(card(reply)).toContainText(COPY.progress(2, 2, 1, 1));
  await expectNoHorizontalOverflow(page);
  await shot(page, testInfo, "cli-webkit");
});
