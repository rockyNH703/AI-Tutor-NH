import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { Assessment, AssessmentAnswer, AssessmentQuestion } from "@/lib/api/types";
import { ResultList } from "../review";
import { toggled } from "./choice";
import { lineAfterKey, lines, linesText, listingLines, stateOf } from "./listing";

const BASE: AssessmentQuestion = {
  kind: "lines",
  text: "Which lines drop the traffic?",
  options: null,
  segments: null,
  blanks: null,
  response: null,
  correct: null,
  answers: null,
  explanation: null,
  listing: { title: "ACL 101", content: "permit tcp any any eq 22\r\ndeny ip any any\n\nremark end\n" },
};
const answered = (answer: number[], earned: number, extra: Partial<AssessmentQuestion> = {}): AssessmentQuestion => {
  const response: AssessmentAnswer = {
    status: "answered",
    value: null,
    answer,
    earned,
    possible: 1,
    at: "2026-11-18T10:00:00Z",
    marks: null,
  };
  return { ...BASE, response, correctList: [1], explanation: "Line 2 denies everything else.", ...extra };
};
const input = (value: number[], extra: object = {}) =>
  renderToStaticMarkup(
    <lines.Input
      question={BASE}
      number={1}
      value={value}
      onChange={() => {}}
      disabled={false}
      mode="practice"
      tries={[]}
      labelledBy="q1-legend"
      {...extra}
    />,
  );

describe("the lines kind", () => {
  test("counts lines like the server: CRLF read as LF, one trailing newline dropped", () => {
    expect(listingLines(BASE.listing?.content ?? "")).toEqual(["permit tcp any any eq 22", "deny ip any any", "", "remark end"]);
  });

  test("an answer needs a line; it is sent sorted, 0-based", () => {
    expect(lines.isComplete(BASE, [])).toBe(false);
    expect(lines.isComplete(BASE, [2])).toBe(true);
    expect(lines.toRequest(BASE, [3, 0, 2])).toEqual([0, 2, 3]);
    expect(lines.empty(BASE)).toEqual([]);
  });

  test("the value held by the server: a draft first, then a final answer, never a malformed one", () => {
    expect(lines.fromRecord({ ...BASE, draft: [2, 0] })).toEqual([2, 0]);
    expect(lines.fromRecord(answered([1], 1))).toEqual([1]);
    expect(lines.fromRecord({ ...BASE, draft: ["a"] })).toBeNull();
    expect(lines.fromRecord({ ...BASE, draft: "1" })).toBeNull();
    expect(lines.fromRecord(BASE)).toBeNull();
  });

  test("keys move within the listing; any other key is not the listbox's", () => {
    expect(lineAfterKey("ArrowDown", 0, 4)).toBe(1);
    expect(lineAfterKey("ArrowDown", 3, 4)).toBe(3);
    expect(lineAfterKey("ArrowUp", 0, 4)).toBe(0);
    expect(lineAfterKey("Home", 2, 4)).toBe(0);
    expect(lineAfterKey("End", 0, 4)).toBe(3);
    expect(lineAfterKey("a", 1, 4)).toBeNull();
    expect(lineAfterKey(" ", 1, 4)).toBeNull();
  });

  test("line numbers read 1-based", () => {
    expect(linesText([])).toBe("no lines");
    expect(linesText([4])).toBe("line 5");
    expect(linesText([3, 0])).toBe("lines 1, 4");
  });

  test("the input is a multi-select listbox with one tab stop, named by the question and the listing", () => {
    const html = input([3, 1]);
    expect(html).toContain('role="listbox"');
    expect(html).toContain('aria-multiselectable="true"');
    expect(html).toMatch(/aria-labelledby="q1-legend [^"]+"/);
    expect(html.match(/role="option"/g)).toHaveLength(4);
    expect(html.match(/aria-selected="true"/g)).toHaveLength(2);
    expect(html.match(/tabindex="0"/g)).toHaveLength(1);
    // The roving stop starts on the first chosen line.
    expect(html).toMatch(/aria-selected="true" tabindex="0"[^>]*>(?:(?!role="option").)*Line 2/);
    expect(html).toContain("Line 4: </span>remark end");
    expect(html).not.toContain("aria-readonly");
  });

  test("an error is shown and bound to the listbox", () => {
    const html = input([], { invalid: "Choose at least one line." });
    expect(html).toContain('aria-invalid="true"');
    expect(html).toContain("Choose at least one line.");
    const errorId = html.match(/<p id="([^"]+)"[^>]*>Choose at least one line/)?.[1];
    expect(html).toContain(`aria-describedby="${errorId}"`);
  });

  test("a result says what was chosen and what was right, with the listing marked read-only", () => {
    const Result = lines.Result;
    const wrong = renderToStaticMarkup(<Result question={answered([0, 1], 0)} number={2} />);
    expect(wrong).toContain("Not quite.");
    expect(wrong).toContain("You chose lines 1, 2.");
    expect(wrong).toContain("The answer is line 2.");
    expect(wrong).toContain('aria-readonly="true"'); // practice keeps the listing too, not only a review
    expect(wrong).toMatch(/<summary class="focus-ring[^"]*"[^>]*>Why<\/summary>/);
    const right = renderToStaticMarkup(<Result question={answered([1], 1)} number={2} />);
    expect(right).toContain("Correct.");
    expect(right).not.toContain("The answer is");
    const review = renderToStaticMarkup(<Result question={answered([0, 1], 0)} number={2} compact open />);
    expect(review).toContain('aria-readonly="true"');
    expect(review).toContain("Line 1, your choice, not in the answer");
    expect(review).toContain("Line 2, your choice, right");
    expect(review).toContain("<details open");
    const skipped = answered([], 0);
    const shown = renderToStaticMarkup(
      <Result question={{ ...skipped, response: { ...(skipped.response as AssessmentAnswer), status: "skipped" } }} number={null} />,
    );
    expect(shown).toContain("Skipped.");
    expect(shown).not.toContain("You chose");
    expect(shown).not.toMatch(/Q\d+\./);
  });
});

describe("shared helpers", () => {
  test("stateOf: chosen while answering; right, wrong or missed once final", () => {
    expect(stateOf(1, [1])).toBe("chosen");
    expect(stateOf(2, [1])).toBeNull();
    expect(stateOf("a", ["a"], ["a"])).toBe("right");
    expect(stateOf("a", ["a"], ["b"])).toBe("wrong");
    expect(stateOf("b", ["a"], ["b"])).toBe("missed");
    expect(stateOf("c", ["a"], ["b"])).toBeNull();
  });

  test("toggled adds or removes without changing the list it was given", () => {
    const list = [1, 2];
    expect(toggled(list, 3)).toEqual([1, 2, 3]);
    expect(toggled(list, 1)).toEqual([2]);
    expect(list).toEqual([1, 2]);
  });
});

describe("a final question's listing in the result list", () => {
  const listingOf = (title: string) => ({ title, content: "hostname R1\ninterface Gi0/0" });
  const final = (earned: number): AssessmentAnswer => ({
    status: "answered",
    value: 0,
    answer: "show run",
    earned,
    possible: 1,
    at: "2026-11-18T10:00:00Z",
    marks: null,
  });
  const record: Assessment = {
    id: "m:0",
    position: 0,
    schemaVersion: 2,
    kind: "test",
    title: null,
    hash: "a".repeat(64),
    locator: "b".repeat(64),
    state: "completed",
    answerable: false,
    total: 3,
    answered: 3,
    current: null,
    earned: 1,
    possible: 3,
    questions: [
      { ...BASE, kind: "mcq", options: ["A", "B"], correct: 0, response: final(1), listing: listingOf("mcq listing") },
      { ...BASE, kind: "cli", prompt: "R1#", accepted: ["show run"], response: final(0), listing: listingOf("cli listing") },
      answered([1], 1, { listing: listingOf("lines listing") }),
    ],
  };
  const regions = (html: string) => [...html.matchAll(/role="region" aria-label="([^"]+)"/g)].map((found) => found[1]);

  test("is drawn once for every kind, in practice and in the review; lines draws its own, marked", () => {
    for (const compact of [false, true]) {
      const html = renderToStaticMarkup(<ResultList test={record} single={false} open={null} compact={compact} />);
      expect(regions(html)).toEqual(["mcq listing", "cli listing"]);
      expect(html.match(/role="listbox"/g)).toHaveLength(1);
      expect(html).toContain("lines listing</figcaption>");
    }
  });
});
