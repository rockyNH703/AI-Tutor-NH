import { type Locator, type Page, expect } from "@playwright/test";

import { blockOf } from "./support";
import { tabTo } from "./ui";

/**
 * How a person answers each of the 12 question types (plan §1) in its card: with the mouse or a tap, and
 * keyboard only. Each kind's texts come from its fixture (backend/tests/fixtures/learning/kind-<kind>.json),
 * whose first question is the kind and whose second is a plain single-choice question.
 */
export const KINDS = ["single", "multi", "fill", "numeric", "short", "long", "match", "order", "cli", "lines", "scenario", "diagram"] as const;
export type Kind = (typeof KINDS)[number];

export type Driver = {
  kind: Kind;
  fixture: `kind-${Kind}`;
  /** What the question is worth (the progress line counts points). */
  points: number;
  /** Earned by `wrong` (partial credit where the kind has it). */
  wrongPoints: number;
  right: (q: Locator, page: Page) => Promise<void>;
  wrong: (q: Locator, page: Page) => Promise<void>;
  /** Keyboard only, starting with focus on the question's fieldset. Ends with the answer filled in. */
  keys: (q: Locator, page: Page) => Promise<void>;
  /** How the keyboard checks it once filled in: the Check button, Enter (numeric, cli) or Ctrl/⌘+Enter (written). */
  submit: "button" | "Enter" | "ControlOrMeta+Enter";
  /** A request value the server refuses with 422 (a test rewrites the request to carry it). */
  invalid: unknown;
  /** A typed value the client refuses before sending, and what it says. */
  client?: { value: string; message: string };
  /** Practice tries before the answer is shown. */
  tries: number;
  /** The start of the question's explanation (shown once final). */
  explanation: string;
};

const literal = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const named = (text: string) => new RegExp(literal(text));
const first = (kind: Kind) => blockOf(`kind-${kind}`).questions[0];
const explained = (kind: Kind) => String(first(kind).explanation).slice(0, 40);

async function choose(q: Locator, option: string): Promise<void> {
  await q.getByRole("radio", { name: named(option) }).check();
}

/** Exactly these boxes ticked (any others cleared), so a changed answer leaves nothing behind. */
async function tick(q: Locator, options: string[]): Promise<void> {
  // The checked set shrinks as each is cleared: always clear its first (an `.all()` index would go stale).
  const checked = q.getByRole("checkbox", { checked: true });
  while ((await checked.count()) > 0) await checked.first().uncheck();
  for (const option of options) await q.getByRole("checkbox", { name: named(option) }).check();
}

/** Exactly these listing lines selected (0-based). */
async function pickLines(q: Locator, lines: number[]): Promise<void> {
  const selected = q.getByRole("option", { selected: true });
  while ((await selected.count()) > 0) await selected.first().click();
  for (const line of lines) await q.getByRole("option").nth(line).click();
}

async function typeInto(field: Locator, text: string): Promise<void> {
  await field.fill(text);
}

async function keyTicks(q: Locator, page: Page, options: string[]): Promise<void> {
  for (const option of options) {
    await tabTo(page, q.getByRole("checkbox", { name: named(option) }));
    await page.keyboard.press("Space");
  }
}

// ------------------------------------------------------------------ match (a select per left item)

async function pair(q: Locator, page: Page, left: string, right: string): Promise<void> {
  await q.getByRole("combobox", { name: left, exact: true }).click();
  await page.getByRole("option", { name: right, exact: true }).click();
}

async function keyPair(q: Locator, page: Page, left: string, right: string): Promise<void> {
  const trigger = q.getByRole("combobox", { name: left, exact: true });
  await tabTo(page, trigger);
  await page.keyboard.press("Enter");
  // Type once the open list holds focus (a person does not type within 50 ms of opening it).
  await expect(page.getByRole("listbox").locator("[role=option]:focus")).toHaveCount(1);
  // The select's typeahead, at a person's pace: Radix moves focus a tick after each key (setTimeout), so keys
  // with no gap are judged against the old focus, and Enter must wait for the option it should choose.
  await page.keyboard.type(right, { delay: 80 });
  await expect(page.getByRole("listbox").locator("[role=option]:focus")).toHaveText(right);
  await page.keyboard.press("Enter");
  await expect(trigger).toContainText(right);
  await expect(trigger, "focus returns to the trigger").toBeFocused();
}

// ------------------------------------------------------------------ order (Move up/down)

/** Put the items in `target` order with their "Move '<item>' up" buttons (clicked, or Enter from the keyboard). */
async function arrange(q: Locator, page: Page, target: string[], keyboard: boolean): Promise<void> {
  for (const [place, item] of target.entries()) {
    for (let guard = 0; guard < target.length; guard += 1) {
      const shown = await q.getByRole("listitem").allInnerTexts();
      if (shown.findIndex((text) => text.includes(item)) === place) break;
      const up = q.getByRole("button", { name: new RegExp(`^Move .${literal(item)}. up$`) });
      if (keyboard) {
        await tabTo(page, up, 40);
        await page.keyboard.press("Enter");
        // Focus table: after a move, the moved item's same button (its other one at the top).
        await expect(q.locator(":focus"), "focus stays on the moved item").toHaveAccessibleName(
          new RegExp(`^Move .${literal(item)}. (?:up|down)$`),
        );
      } else {
        await up.click();
      }
    }
  }
  expect((await q.getByRole("listitem").allInnerTexts()).map((text) => target.find((item) => text.includes(item)))).toEqual(target);
}

// ------------------------------------------------------------------ the drivers

const single = first("single");
const multi = first("multi");
const match = first("match") as { pairs: [string, string][]; extra: string[] };
const order = first("order") as { items: string[] };
const scenario = first("scenario");
const RIGHT_SHORT = "The destination is unknown unicast: it is not in the MAC address table yet, so the switch floods it and learns the port from the reply.";
const RIGHT_LONG =
  "The inside host sends a SYN. The firewall checks its rules and records the connection in its state table. Return traffic that matches the entry is automatically allowed, with no inbound rule, until the connection closes.";

export const DRIVERS: Record<Kind, Driver> = {
  single: {
    kind: "single",
    fixture: "kind-single",
    points: 1,
    wrongPoints: 0,
    right: (q) => choose(q, single.options[1]),
    wrong: (q) => choose(q, single.options[0]),
    keys: async (q, page) => {
      await tabTo(page, q.getByRole("radio").first());
      await page.keyboard.press("ArrowDown"); // radios: an arrow moves and selects
      await expect(q.getByRole("radio", { name: named(single.options[1]) })).toBeChecked();
    },
    submit: "button",
    invalid: 99,
    tries: 1,
    explanation: explained("single"),
  },
  multi: {
    kind: "multi",
    fixture: "kind-multi",
    points: 1,
    wrongPoints: 0,
    right: (q) => tick(q, multi.options.slice(0, 3)),
    wrong: (q) => tick(q, [multi.options[0], multi.options[3]]),
    keys: (q, page) => keyTicks(q, page, multi.options.slice(0, 3)),
    submit: "button",
    invalid: [0, 0],
    tries: 1,
    explanation: explained("multi"),
  },
  fill: {
    kind: "fill",
    fixture: "kind-fill",
    points: 3,
    wrongPoints: 2,
    right: async (q) => {
      for (const [index, text] of ["DR", "BDR", "0"].entries()) await typeInto(q.getByRole("textbox", { name: `Blank ${index + 1}` }), text);
    },
    wrong: async (q) => {
      for (const [index, text] of ["DR", "ABR", "0"].entries()) await typeInto(q.getByRole("textbox", { name: `Blank ${index + 1}` }), text);
    },
    keys: async (q, page) => {
      await tabTo(page, q.getByRole("textbox", { name: "Blank 1" }));
      await page.keyboard.type("DR");
      await page.keyboard.press("Tab");
      await page.keyboard.type("BDR");
      await page.keyboard.press("Tab");
      await page.keyboard.type("0");
    },
    submit: "button",
    invalid: ["DR"],
    tries: 1,
    explanation: explained("fill"),
  },
  numeric: {
    kind: "numeric",
    fixture: "kind-numeric",
    points: 1,
    wrongPoints: 0,
    right: (q) => typeInto(q.getByRole("textbox"), "-67"),
    wrong: (q) => typeInto(q.getByRole("textbox"), "-70"),
    keys: async (q, page) => {
      await tabTo(page, q.getByRole("textbox"));
      await page.keyboard.type("-67");
    },
    submit: "Enter",
    invalid: "1e3",
    client: { value: "-67,5", message: "Use a full stop for decimals, not a comma." },
    tries: 3,
    explanation: explained("numeric"),
  },
  short: {
    kind: "short",
    fixture: "kind-short",
    points: 2,
    wrongPoints: 0,
    right: (q) => typeInto(q.getByRole("textbox"), RIGHT_SHORT),
    wrong: (q) => typeInto(q.getByRole("textbox"), "Spanning tree blocks the port."),
    keys: async (q, page) => {
      await tabTo(page, q.getByRole("textbox"));
      await page.keyboard.type(RIGHT_SHORT);
    },
    submit: "ControlOrMeta+Enter",
    invalid: "x".repeat(1001),
    tries: 1,
    explanation: explained("short"),
  },
  long: {
    kind: "long",
    fixture: "kind-long",
    points: 4,
    wrongPoints: 0,
    right: (q) => typeInto(q.getByRole("textbox"), RIGHT_LONG),
    wrong: (q) => typeInto(q.getByRole("textbox"), "It blocks everything coming in."),
    keys: async (q, page) => {
      await tabTo(page, q.getByRole("textbox"));
      await page.keyboard.type(RIGHT_LONG);
    },
    submit: "ControlOrMeta+Enter",
    invalid: "x".repeat(6001),
    tries: 1,
    explanation: explained("long"),
  },
  match: {
    kind: "match",
    fixture: "kind-match",
    points: 4,
    wrongPoints: 3,
    right: async (q, page) => {
      for (const [left, right] of match.pairs) await pair(q, page, left, right);
    },
    wrong: async (q, page) => {
      for (const [index, [left, right]] of match.pairs.entries()) await pair(q, page, left, index === 0 ? (match.extra[0] ?? right) : right);
    },
    keys: async (q, page) => {
      for (const [left, right] of match.pairs) await keyPair(q, page, left, right);
    },
    submit: "button",
    invalid: [0],
    tries: 1,
    explanation: explained("match"),
  },
  order: {
    kind: "order",
    fixture: "kind-order",
    points: 1,
    wrongPoints: 0,
    right: (q, page) => arrange(q, page, order.items, false),
    wrong: (q, page) => arrange(q, page, [...order.items].reverse(), false), // five items: reversed is never right
    keys: (q, page) => arrange(q, page, order.items, true),
    submit: "button",
    invalid: [0, 0, 1, 2, 3],
    tries: 1,
    explanation: explained("order"),
  },
  cli: {
    kind: "cli",
    fixture: "kind-cli",
    points: 1,
    wrongPoints: 0,
    right: (q) => typeInto(q.getByRole("textbox"), "sh ip int br"),
    wrong: (q) => typeInto(q.getByRole("textbox"), "show ip route"),
    keys: async (q, page) => {
      await tabTo(page, q.getByRole("textbox"));
      await page.keyboard.type("sh ip int br");
    },
    submit: "Enter",
    invalid: "x".repeat(301),
    tries: 3,
    explanation: explained("cli"),
  },
  lines: {
    kind: "lines",
    fixture: "kind-lines",
    points: 1,
    wrongPoints: 0,
    right: (q) => pickLines(q, [2, 4]),
    wrong: (q) => pickLines(q, [1]),
    keys: async (q, page) => {
      await tabTo(page, q.getByRole("option").first());
      for (const _ of [3, 5]) {
        await page.keyboard.press("ArrowDown");
        await page.keyboard.press("ArrowDown");
        await page.keyboard.press("Space");
      }
      await expect(q.getByRole("option", { selected: true })).toHaveCount(2);
    },
    submit: "button",
    invalid: [99],
    tries: 1,
    explanation: explained("lines"),
  },
  scenario: {
    kind: "scenario",
    fixture: "kind-scenario",
    points: 1,
    wrongPoints: 0,
    right: (q) => choose(q, scenario.options[0]),
    wrong: (q) => choose(q, scenario.options[1]),
    keys: async (q, page) => {
      await tabTo(page, q.getByRole("radio").first());
      await page.keyboard.press("Space"); // the right one is the first: Tab focuses it, Space selects it
      await expect(q.getByRole("radio", { name: named(scenario.options[0]) })).toBeChecked();
    },
    submit: "button",
    invalid: 99,
    tries: 1,
    explanation: explained("scenario"),
  },
  diagram: {
    kind: "diagram",
    fixture: "kind-diagram",
    points: 1,
    wrongPoints: 0,
    right: (q) => tick(q.getByRole("group", { name: "Devices and links" }), ["SW1", "FW1", "R1"]),
    wrong: (q) => tick(q.getByRole("group", { name: "Devices and links" }), ["SW1"]),
    keys: (q, page) => keyTicks(q.getByRole("group", { name: "Devices and links" }), page, ["SW1", "FW1", "R1"]),
    submit: "button",
    invalid: ["NOPE"],
    tries: 1,
    explanation: explained("diagram"),
  },
};
