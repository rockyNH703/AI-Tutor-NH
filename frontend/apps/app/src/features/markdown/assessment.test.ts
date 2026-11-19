import { describe, expect, test } from "bun:test";

import type { Assessment } from "@/lib/api/types";
import { MASKED_TEST, isExamSource, maskExamFences, parseTestCommand, replaceFences } from "./assessment";

const EXAM = '{"mode": "exam", "questions": [{"question": "Q?", "options": ["a", "b"], "answer": 1}]}';
const PRACTICE = '{"questions": [{"question": "Q?", "options": ["a", "b"], "answer": 1}]}';

const record = (overrides: Partial<Assessment> = {}): Assessment => ({
  id: "t",
  position: 0,
  schemaVersion: 2,
  kind: "test",
  title: null,
  hash: "h",
  locator: "l",
  state: "active",
  answerable: true,
  total: 1,
  answered: 0,
  current: null,
  earned: 0,
  possible: 1,
  questions: [],
  mode: "exam",
  submitted: false,
  revision: 1,
  ...overrides,
});

describe("maskExamFences (mirrors state.py _blocks)", () => {
  test("hides an unsubmitted mock exam's fence, keeping the prose around it", () => {
    const text = `Try this:\n\n\`\`\`quiz\n${EXAM}\n\`\`\`\n\nGood luck.`;
    expect(maskExamFences(text, [record()])).toBe(`Try this:\n\n${MASKED_TEST}\n\nGood luck.`);
  });

  test("shows a mock exam once its record says submitted, and a practice quiz always", () => {
    const exam = `\`\`\`quiz\n${EXAM}\n\`\`\``;
    expect(maskExamFences(exam, [record({ submitted: true })])).toBe(exam);
    const practice = `\`\`\`quiz\n${PRACTICE}\n\`\`\``;
    expect(maskExamFences(practice, [])).toBe(practice);
  });

  test("while streaming, or before the records are known, hides every quiz and mcq fence", () => {
    const practice = `\`\`\`quiz\n${PRACTICE}\n\`\`\``;
    const mcq = '```mcq\n{"question": "Q?", "options": ["a", "b"], "answer": 0}\n```';
    expect(maskExamFences(practice, [], true)).toBe(MASKED_TEST);
    expect(maskExamFences(practice, undefined)).toBe(MASKED_TEST);
    expect(maskExamFences(mcq, undefined)).toBe(MASKED_TEST);
    // An unclosed fence (the reply is still writing it) runs to the end.
    expect(maskExamFences(`Start\n\`\`\`quiz\n${EXAM.slice(0, 30)}`, undefined, true)).toBe(`Start\n${MASKED_TEST}`);
  });

  test("tilde fences, longer fences, list items and quotes", () => {
    expect(maskExamFences(`~~~quiz\n${EXAM}\n~~~`, [])).toBe(MASKED_TEST);
    // A shorter run of the same character does not close a longer fence.
    expect(maskExamFences(`\`\`\`\`quiz\n${EXAM}\n\`\`\`\nstill inside\n\`\`\`\`\nafter`, [])).toBe(`${MASKED_TEST}\nafter`);
    // ``` does not close a ~~~ fence.
    expect(maskExamFences(`~~~quiz\n${EXAM}\n\`\`\`\n~~~`, [])).toBe(MASKED_TEST);
    expect(maskExamFences(`1. Step\n   \`\`\`quiz\n   ${EXAM}\n   \`\`\``, [])).toBe(`1. Step\n   ${MASKED_TEST}`);
    expect(maskExamFences(`- \`\`\`quiz\n  ${EXAM}\n  \`\`\``, [])).toBe(`- ${MASKED_TEST}`);
    expect(maskExamFences(`> \`\`\`quiz\n> ${EXAM}\n> \`\`\``, [])).toBe(`> ${MASKED_TEST}`);
  });

  test("leaves other fences and inline backticks alone, and returns the same string when nothing is hidden", () => {
    const text = "Run ```this``` now.\n```js\nconst mode = '\"mode\": \"exam\"';\n```\n```bash `x`\nnot a fence\n";
    expect(maskExamFences(text, [])).toBe(text);
    const crlf = "a\r\n```js\r\nx\r\n```\r\n";
    expect(maskExamFences(crlf, [])).toBe(crlf);
  });

  test("an escaped mode is still an exam", () => {
    expect(isExamSource('{"mode": "\\u0065xam", "questions": []}')).toBe(true);
    expect(isExamSource('> {"mode":\n> "\\u0065xam"}')).toBe(true);
    expect(isExamSource(PRACTICE)).toBe(false);
    expect(isExamSource("not json")).toBe(false);
  });
});

describe("replaceFences", () => {
  test("hands each fence's language and body to the caller", () => {
    const seen: string[] = [];
    replaceFences("```video extra\n{}\n```\n~~~\nplain\n~~~", ({ language, body }) => {
      seen.push(`${language}:${body}`);
      return null;
    });
    expect(seen).toEqual(["video:{}", ":plain"]);
  });

  test("a line of list markers and spaces is scanned in linear time (no catastrophic backtracking)", () => {
    // The first line took ~1.5 s with the old `[ \t]+[ \t>]*`; the long ones would never finish.
    const lines = ["- \t ".repeat(16), "-  ".repeat(5000), "1.   ".repeat(5000), "> -  ".repeat(5000)];
    for (const line of lines.map((markers) => `${markers}x`)) {
      const started = performance.now();
      expect(replaceFences(line, () => "hidden")).toBe(line);
      expect(performance.now() - started).toBeLessThan(200);
    }
    // Several spaces or tabs after a marker still open a fence.
    expect(replaceFences("-  \t ```quiz\n{}\n```", () => "hidden")).toBe("hidden");
  });
});

describe("parseTestCommand", () => {
  test("letters, skip and next", () => {
    expect(parseTestCommand("B")).toEqual({ kind: "answer", value: 1 });
    expect(parseTestCommand("option c")).toEqual({ kind: "answer", value: 2 });
    expect(parseTestCommand("skip")).toEqual({ kind: "skip" });
    expect(parseTestCommand("next")).toEqual({ kind: "show" });
    expect(parseTestCommand("explain B")).toBeNull();
  });
});
