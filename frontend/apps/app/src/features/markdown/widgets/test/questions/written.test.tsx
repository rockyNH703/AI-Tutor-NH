import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { AssessmentAnswer, AssessmentQuestion } from "@/lib/api/types";
import type { KindModule } from "../question-props";
import pack, { checksAnswer, counterShown, limitOf } from "./written";

const written = pack.short as KindModule<string>;

function q(over: Partial<AssessmentQuestion> = {}): AssessmentQuestion {
  return {
    kind: "short",
    text: "Why does OSPF elect a DR on a broadcast segment?",
    options: null,
    segments: null,
    blanks: null,
    response: null,
    correct: null,
    answers: null,
    explanation: null,
    points: 3,
    maxLength: 1000,
    ...over,
  };
}

function answered(over: Partial<AssessmentAnswer>): AssessmentAnswer {
  return { status: "answered", value: null, earned: 0, possible: 3, at: "2026-11-18T10:00:00Z", marks: null, ...over };
}

const base = { number: 1, onChange: () => {}, disabled: false, mode: "practice" as const, tries: [], labelledBy: "legend" };
const text = (html: string) => html.replace(/<[^>]+>/g, "");
const key = { key: "Enter", shiftKey: false, ctrlKey: false, metaKey: false };

describe("written answers (short, long)", () => {
  test("the limit is the record's, else the server's for the kind", () => {
    expect(limitOf(q({ maxLength: 500 }))).toBe(500);
    expect(limitOf(q({ maxLength: null }))).toBe(1000);
    expect(limitOf(q({ kind: "long", maxLength: null }))).toBe(6000);
  });

  test("the counter shows only near the limit", () => {
    expect(counterShown(899, 1000)).toBe(false);
    expect(counterShown(900, 1000)).toBe(true);
    expect(counterShown(5399, 6000)).toBe(false);
  });

  test("Ctrl or ⌘ + Enter checks; Enter alone, Shift or an input method composing never does", () => {
    expect(checksAnswer({ ...key, ctrlKey: true })).toBe(true);
    expect(checksAnswer({ ...key, metaKey: true })).toBe(true);
    expect(checksAnswer(key)).toBe(false);
    expect(checksAnswer({ ...key, ctrlKey: true, shiftKey: true })).toBe(false);
    expect(checksAnswer({ ...key, ctrlKey: true, isComposing: true })).toBe(false);
    expect(checksAnswer({ ...key, ctrlKey: true, keyCode: 229 })).toBe(false);
  });

  test("the module: complete when written, sent as written, over the limit refused, read back", () => {
    expect(written.isComplete(q(), " \n ")).toBe(false);
    expect(written.isComplete(q(), "To cut adjacencies.")).toBe(true);
    expect(written.toRequest(q(), "  as typed\n")).toBe("  as typed\n");
    expect(written.check?.(q({ maxLength: 5 }), "123456")).toBe(`Keep your answer to ${(5).toLocaleString()} characters.`);
    expect(written.check?.(q(), "fine")).toBeNull();
    expect(written.fromRecord(q({ draft: "draft" }))).toBe("draft");
    expect(written.fromRecord(q({ response: answered({ answer: "final" }) }))).toBe("final");
  });
});

describe("the written input", () => {
  test("short: a named textarea with its limit, no Write/Preview, no counter far from the limit", () => {
    const html = renderToStaticMarkup(<written.Input {...base} question={q()} value="abc" />);
    expect(html).toContain("<textarea");
    expect(html).toContain('maxLength="1000"');
    expect(html).toContain('aria-labelledby="legend"');
    expect(html).not.toContain("Preview");
    expect(html).not.toContain("/ 1");
    expect(html).not.toContain("aria-describedby");
  });

  test("the keys are described only where Ctrl+Enter checks", () => {
    const html = renderToStaticMarkup(<written.Input {...base} question={q()} value="" onSubmit={() => {}} />);
    const keys = html.match(/<span id="([^"]+)"[^>]*>Enter adds a new line/)?.[1];
    expect(keys).toBeTruthy();
    expect(html).toContain(`aria-describedby="${keys}"`);
    expect(text(html)).toContain("Ctrl+Enter (⌘+Enter on a Mac) checks your answer.");
  });

  test("in a mock exam the keys save the answer, they never check it", () => {
    const html = renderToStaticMarkup(<written.Input {...base} mode="exam" question={q()} value="" onSubmit={() => {}} />);
    expect(text(html)).toContain("Enter adds a new line; Ctrl+Enter (⌘+Enter on a Mac) saves your answer.");
    expect(text(html)).not.toContain("checks");
  });

  test("long: Write/Preview pressed state, and a counter near the limit that is described, not live", () => {
    const value = "x".repeat(5500);
    const html = renderToStaticMarkup(
      <written.Input {...base} question={q({ kind: "long", maxLength: 6000 })} value={value} invalid="Too long." />,
    );
    expect(html).toMatch(/aria-pressed="true"[^>]*>Write<\/button>/);
    expect(html).toMatch(/aria-pressed="false"[^>]*>Preview<\/button>/);
    const counter = html.match(/<span id="([^"]+)"[^>]*>5/)?.[1];
    expect(text(html)).toContain(`${(5500).toLocaleString()} / ${(6000).toLocaleString()} characters`);
    expect(html).toMatch(new RegExp(`aria-describedby="[^"]+ ${counter}"`));
    expect(html).not.toContain("aria-live");
    expect(html).toContain('aria-invalid="true"');
    expect(html).toContain('rows="8"');
  });
});

describe("the written result", () => {
  const question = q({
    keyPoints: ["DR", "fewer adjacencies", "LSA flooding"],
    sample: "A **DR** cuts the number of adjacencies.",
    explanation: "Full mesh would be n(n-1)/2.",
  });

  test("partly right: key points found and missed, your answer, the sample, Why", () => {
    const html = renderToStaticMarkup(
      <written.Result
        question={{ ...question, response: answered({ answer: "The DR floods LSAs.", earned: 2, marks: [true, false, true] }) }}
        number={5}
        open
      />,
    );
    const shown = text(html);
    expect(shown).toContain("Partly right: 2 of 3 key points found.");
    expect(shown).toContain("DR (found)");
    expect(shown).toContain("fewer adjacencies (missed)");
    expect(shown).toContain("Your answer");
    expect(shown).toContain("The DR floods LSAs.");
    expect(html).toContain("<strong>DR</strong>");
    expect(shown).toContain("Full mesh would be n(n-1)/2.");
  });

  test("right and skipped", () => {
    const right = { ...question, response: answered({ answer: "all", earned: 3, marks: [true, true, true] }) };
    expect(text(renderToStaticMarkup(<written.Result question={right} number={1} />))).toContain(
      "Correct! 3 of 3 key points found.",
    );
    const skipped = { ...question, response: answered({ status: "skipped" }) };
    const shown = text(renderToStaticMarkup(<written.Result question={skipped} number={1} />));
    expect(shown).toContain("Skipped. The key points are listed below.");
    expect(shown).not.toContain("(found)");
    expect(shown).not.toContain("Your answer");
  });
});
