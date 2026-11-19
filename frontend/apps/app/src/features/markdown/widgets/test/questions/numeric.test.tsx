import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { AssessmentAnswer, AssessmentQuestion } from "@/lib/api/types";
import type { KindModule } from "../question-props";
import pack, { NUMERIC_HINT, numberProblem } from "./numeric";

const numeric = pack.numeric as KindModule<string>;

function q(over: Partial<AssessmentQuestion> = {}): AssessmentQuestion {
  return {
    kind: "numeric",
    text: "What is the one-way delay?",
    options: null,
    segments: null,
    blanks: null,
    response: null,
    correct: null,
    answers: null,
    explanation: null,
    unit: "ms",
    ...over,
  };
}

function answered(over: Partial<AssessmentAnswer>): AssessmentAnswer {
  return { status: "answered", value: null, earned: 0, possible: 1, at: "2026-11-18T10:00:00Z", marks: null, ...over };
}

const base = { number: 1, onChange: () => {}, disabled: false, mode: "practice" as const, tries: [], labelledBy: "legend" };
const text = (html: string) => html.replace(/<[^>]+>/g, "");

describe("the number check (mirrors the server's grammar)", () => {
  test("accepts what the server accepts", () => {
    for (const fine of ["-67", "0", "12.5", "-0.000000001", " 42 ", "1".repeat(30), `1.${"9".repeat(9)}`, ""]) {
      expect(numberProblem(fine)).toBeNull();
    }
  });

  test("a comma gets its own message", () => {
    expect(numberProblem("0,5")).toBe("Use a full stop for decimals, not a comma.");
    expect(numberProblem("1,000")).toBe("Use a full stop for decimals, not a comma.");
  });

  test("too many digits says the limits", () => {
    expect(numberProblem("1".repeat(31))).toBe("Use at most 30 digits before the full stop and 9 after it.");
    expect(numberProblem(`1.${"0".repeat(10)}`)).toBe("Use at most 30 digits before the full stop and 9 after it.");
  });

  test("anything else repeats the format", () => {
    for (const wrong of ["12.", ".5", "1e3", "+5", "--1", "²", "١٢", "12 ms", "−5"]) {
      expect(numberProblem(wrong)).toBe(`Enter a number. ${NUMERIC_HINT}.`);
    }
  });

  test("the module: complete when typed, checked, sent trimmed, read back", () => {
    expect(numeric.isComplete(q(), "  ")).toBe(false);
    expect(numeric.isComplete(q(), "5")).toBe(true);
    expect(numeric.check?.(q(), "0,5")).toBe("Use a full stop for decimals, not a comma.");
    expect(numeric.check?.(q(), "-67")).toBeNull();
    expect(numeric.toRequest(q(), " -12.5 ")).toBe("-12.5");
    expect(numeric.fromRecord(q({ draft: "12" }))).toBe("12");
    expect(numeric.fromRecord(q({ response: answered({ answer: "13" }) }))).toBe("13");
    expect(numeric.fromRecord(q())).toBeNull();
  });
});

describe("the numeric input", () => {
  test("plain text with no inputmode (the iPhone decimal pad has no minus), no autofill or corrections", () => {
    const html = renderToStaticMarkup(<numeric.Input {...base} question={q()} value="-1" />);
    expect(html).toContain('type="text"');
    expect(html.toLowerCase()).not.toContain("inputmode");
    expect(html).toContain('autoComplete="off"');
    expect(html).toContain('autoCapitalize="none"');
    expect(html).toContain('spellCheck="false"');
    expect(html).toContain('aria-labelledby="legend"');
    expect(html).toContain("<form");
    expect(text(html)).toContain(NUMERIC_HINT);
  });

  test("the unit is a visible suffix read with the field, then the hint and earlier tries", () => {
    const html = renderToStaticMarkup(<numeric.Input {...base} question={q()} value="" tries={["11", "14"]} />);
    const unit = html.match(/<span id="([^"]+)"[^>]*>ms<\/span>/)?.[1];
    const hint = html.match(/<p id="([^"]+)"[^>]*>Digits,/)?.[1];
    const tries = html.match(/<p id="([^"]+)"[^>]*>Tried so far: 11, 14<\/p>/)?.[1];
    expect(unit && hint && tries).toBeTruthy();
    expect(html).toContain(`aria-describedby="${unit} ${hint} ${tries}"`);
  });

  test("no unit, no suffix; an error is shown first and marks the field", () => {
    const html = renderToStaticMarkup(
      <numeric.Input {...base} question={q({ unit: null })} value="0,5" invalid="Use a full stop for decimals, not a comma." />,
    );
    expect(html).toContain('aria-invalid="true"');
    const error = html.match(/<p id="([^"]+)"[^>]*>Use a full stop/)?.[1];
    expect(html).toMatch(new RegExp(`aria-describedby="${error} [^"]+"`));
    expect(html).not.toContain("-unit");
  });
});

describe("the numeric result", () => {
  test("right: the value given and the accepted value with its tolerance", () => {
    const question = q({ accepted: ["12.5"], tolerance: "0.5", response: answered({ answer: "12.4", earned: 1 }) });
    expect(text(renderToStaticMarkup(<numeric.Result question={question} number={4} />))).toContain(
      "Correct! You gave 12.4 ms; the accepted answer is 12.5 ± 0.5 ms.",
    );
  });

  test("wrong after tries, with a share as tolerance", () => {
    const question = q({
      accepted: ["200"],
      tolerance: "5%",
      response: answered({ answer: "250", tries: ["300", "180"] }),
    });
    const shown = text(renderToStaticMarkup(<numeric.Result question={question} number={4} />));
    expect(shown).toContain("Not quite: you gave 250 ms; the accepted answer is 200 ms ± 5%.");
    expect(shown).toContain("Earlier tries: 300, 180");
  });

  test("skipped, exact, no unit", () => {
    const question = q({ unit: null, accepted: ["-67"], tolerance: "0", response: answered({ status: "skipped" }) });
    expect(text(renderToStaticMarkup(<numeric.Result question={question} number={null} />))).toContain(
      "Skipped. The accepted answer is -67.",
    );
  });

  test("the unit goes through the maths renderer", () => {
    const question = q({ unit: "$\\mu s$", accepted: ["3"], tolerance: "0", response: answered({ answer: "3", earned: 1 }) });
    expect(renderToStaticMarkup(<numeric.Result question={question} number={1} />)).toContain("katex");
  });
});
