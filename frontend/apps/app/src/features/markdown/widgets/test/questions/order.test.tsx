import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { AssessmentAnswer, AssessmentQuestion } from "@/lib/api/types";
import type { InputProps } from "../question-props";
import pack, { isOrder, moved, nextFocus, positionNote } from "./order";

const order = pack.order!;

function question(extra: Partial<AssessmentQuestion> = {}): AssessmentQuestion {
  return {
    kind: "order",
    text: "Put the troubleshooting steps in order.",
    options: null,
    segments: null,
    blanks: null,
    response: null,
    correct: null,
    answers: null,
    explanation: null,
    items: ["Check the cable", "Run `show ip route`", "Ping the gateway"],
    ...extra,
  };
}

function answered(answer: number[], marks: boolean[]): AssessmentAnswer {
  const earned = marks.every(Boolean) ? 1 : 0;
  return { status: "answered", value: null, earned, possible: 1, at: "2026-11-18T10:00:00Z", marks, answer };
}

/** What a reader sees: tags as spaces, entities read, spaces collapsed. */
const visible = (html: string) =>
  html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();

const input = (extra: Partial<InputProps<number[]>> = {}) =>
  renderToStaticMarkup(
    <order.Input
      question={question()}
      number={1}
      value={[2, 0, 1]}
      onChange={() => {}}
      disabled={false}
      mode="practice"
      tries={[]}
      labelledBy="legend"
      {...extra}
    />,
  );

/** The opening tag of the button with this accessible name (apostrophes as React escapes them). */
const button = (html: string, label: string) =>
  html.match(new RegExp(`<button[^>]*aria-label="${label.replace(/'/g, "&#x27;")}"[^>]*>`))?.[0] ?? "";

describe("order logic", () => {
  test("an order is each shown index exactly once", () => {
    expect(isOrder([2, 0, 1], 3)).toBe(true);
    expect(isOrder([0, 0, 1], 3)).toBe(false);
    expect(isOrder([0, 1], 3)).toBe(false);
    expect(isOrder([0, 1, 3], 3)).toBe(false);
    expect(isOrder(["0", "1", "2"], 3)).toBe(false);
    expect(isOrder(null, 3)).toBe(false);
  });

  test("a move returns a new list and ignores moves past either end", () => {
    const start = [2, 0, 1];
    expect(moved(start, 1, 0)).toEqual([0, 2, 1]);
    expect(moved(start, 0, 2)).toEqual([0, 1, 2]);
    expect(moved(start, 2, 0)).toEqual([1, 2, 0]);
    expect(moved(start, 0, -1)).toEqual([2, 0, 1]);
    expect(moved(start, 2, 3)).toEqual([2, 0, 1]);
    expect(start).toEqual([2, 0, 1]);
  });

  test("focus stays on the same Move button, or its other one at an end", () => {
    expect(nextFocus([0, 2, 1], 2, true)).toBe("up");
    expect(nextFocus([2, 0, 1], 2, true)).toBe("down");
    expect(nextFocus([0, 2, 1], 2, false)).toBe("down");
    expect(nextFocus([0, 1, 2], 2, false)).toBe("up");
  });

  test("values: the shown order first, any order is checkable, drafts before answers", () => {
    expect(order.empty(question())).toEqual([0, 1, 2]);
    expect(order.isComplete(question(), [1, 0, 2])).toBe(true);
    expect(order.isComplete(question(), [1, 1, 2])).toBe(false);
    expect(order.toRequest(question(), [1, 0, 2])).toEqual([1, 0, 2]);
    expect(order.fromRecord(question({ draft: [2, 1, 0] }))).toEqual([2, 1, 0]);
    expect(order.fromRecord(question({ response: answered([1, 2, 0], [true, true, true]) }))).toEqual([1, 2, 0]);
    expect(order.fromRecord(question({ draft: [0, 0, 0] }))).toBeNull();
    expect(order.fromRecord(question())).toBeNull();
  });
});

describe("order input", () => {
  test("named Move buttons in the given order, the ends disabled, at least 44px", () => {
    const html = input();
    expect(visible(html)).toContain("1. Ping the gateway");
    expect(visible(html)).toContain("2. Check the cable");
    expect(visible(html)).toContain("3. Run show ip route");
    expect(button(html, "Move 'Ping the gateway' up")).toContain('disabled=""');
    expect(button(html, "Move 'Ping the gateway' down")).not.toContain('disabled=""');
    expect(button(html, "Move 'Run show ip route' down")).toContain('disabled=""');
    expect(button(html, "Move 'Run show ip route' up")).not.toContain('disabled=""');
    const moves = html.match(/<button[^>]*aria-label="Move [^"]*"[^>]*>/g) ?? [];
    expect(moves).toHaveLength(6);
    for (const move of moves) {
      expect(move).toContain("min-h-11");
      expect(move).toContain("min-w-11");
      expect(move).toMatch(/ id="[^"]+-\d-(up|down)"/);
    }
  });

  test("drag handles are hidden from screen readers and never reached by Tab", () => {
    const html = input();
    const handles = html.match(/<span[^>]*touch-none[^>]*>/g) ?? [];
    expect(handles).toHaveLength(3);
    for (const handle of handles) {
      expect(handle).toContain("data-drag-handle");
      expect(handle).toContain('aria-hidden="true"');
      expect(handle).toContain('tabindex="-1"');
    }
    expect(html).not.toContain('tabindex="0"');
    expect(html).not.toContain("draggable");
    expect(html.match(/touch-none/g)).toHaveLength(3);
  });

  test("a Move is said in a polite status line; the row numbers are read with the list", () => {
    expect(positionNote("Run `show ip route`", 1, 3)).toBe("'Run show ip route' is now 2 of 3.");
    const html = input();
    expect(html).toContain('<p role="status" class="sr-only"></p>');
    expect(html).toContain('<span class="shrink-0 tabular-nums text-text-secondary">1.</span>');
  });

  test("disabled disables every Move button; an error is shown and bound to the list", () => {
    const off = input({ disabled: true });
    expect((off.match(/<button[^>]*>/g) ?? []).every((tag) => tag.includes('disabled=""'))).toBe(true);
    const html = input({ invalid: "Put every item in place." });
    const error = html.match(/<p id="([^"]+)"[^>]*>Put every item in place\.<\/p>/)?.[1];
    expect(error).toBeTruthy();
    expect(html).toContain(`<ol aria-describedby="${error}"`);
  });
});

describe("order result", () => {
  const result = (q: AssessmentQuestion) => visible(renderToStaticMarkup(<order.Result question={q} number={null} />));

  test("one row per position with its word, and what belonged there when wrong", () => {
    const text = result(
      question({ correctList: [0, 2, 1], response: answered([0, 1, 2], [true, false, false]), explanation: "Layer 1 first." }),
    );
    expect(text).not.toContain("Q1.");
    expect(text).toContain("Not quite: 1 of 3 in place.");
    expect(text).toContain("1. Check the cable · correct");
    expect(text).toContain("2. Run show ip route · wrong, should be Ping the gateway");
    expect(text).toContain("3. Ping the gateway · wrong, should be Run show ip route");
    expect(text).toContain("Why");
    const html = renderToStaticMarkup(
      <order.Result question={question({ correctList: [0, 2, 1], response: answered([0, 2, 1], [true, true, true]), explanation: "x" })} number={null} />,
    );
    expect(html).toMatch(/<summary class="focus-ring[^"]*"[^>]*>Why<\/summary>/);
  });

  test("all in place; skipped shows the right order; nothing before it is final", () => {
    expect(result(question({ correctList: [0, 2, 1], response: answered([0, 2, 1], [true, true, true]) }))).toContain(
      "Correct! All in order.",
    );
    const skipped: AssessmentAnswer = {
      status: "skipped",
      value: null,
      earned: 0,
      possible: 1,
      at: "2026-11-18T10:00:00Z",
      marks: null,
    };
    const text = result(question({ correctList: [0, 2, 1], response: skipped }));
    expect(text).toContain("Skipped. The right order:");
    expect(text).toContain("1. Check the cable 2. Ping the gateway 3. Run show ip route");
    expect(text).not.toContain("wrong");
    expect(result(question())).toBe("");
  });
});
