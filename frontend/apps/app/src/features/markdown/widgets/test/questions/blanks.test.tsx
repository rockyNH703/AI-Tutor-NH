import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { AssessmentAnswer, AssessmentQuestion } from "@/lib/api/types";
import { BlankText } from "../../fill-blank";
import type { KindModule } from "../question-props";
import pack from "./blanks";

const blanks = pack.fill_blank as KindModule<string[]>;

function q(over: Partial<AssessmentQuestion> = {}): AssessmentQuestion {
  return {
    kind: "fill_blank",
    text: null,
    options: null,
    segments: ["A ", " listens on port ", "."],
    blanks: 2,
    response: null,
    correct: null,
    answers: null,
    explanation: null,
    ...over,
  };
}

function answered(over: Partial<AssessmentAnswer>): AssessmentAnswer {
  return { status: "answered", value: null, earned: 0, possible: 2, at: "2026-11-18T10:00:00Z", marks: null, ...over };
}

const base = { number: 2, onChange: () => {}, disabled: false, mode: "practice" as const, tries: [], labelledBy: "legend" };
const text = (html: string) => html.replace(/<[^>]+>/g, "");

describe("fill-in question in a quiz (fill_blank)", () => {
  test("one text per blank; complete once one is typed; the request fills every blank", () => {
    expect(blanks.empty(q())).toEqual(["", ""]);
    expect(blanks.isComplete(q(), ["", " "])).toBe(false);
    expect(blanks.isComplete(q(), ["", "22"])).toBe(true);
    expect(blanks.toRequest(q(), ["SSH"])).toEqual(["SSH", ""]);
  });

  test("reads an exam draft, else the typed answer, else nothing", () => {
    expect(blanks.fromRecord(q({ draft: ["SSH", "22"] }))).toEqual(["SSH", "22"]);
    expect(blanks.fromRecord(q({ response: answered({ value: ["SSH", "23"] }) }))).toEqual(["SSH", "23"]);
    expect(blanks.fromRecord(q({ response: answered({ status: "skipped" }) }))).toBeNull();
  });

  test("an input per blank in the sentence, each named, an error bound to all", () => {
    const html = renderToStaticMarkup(<blanks.Input {...base} question={q()} value={["SSH", ""]} />);
    expect(html.match(/<input/g)).toHaveLength(2);
    expect(html).toContain('aria-label="Blank 1"');
    expect(html).toContain('value="SSH"');
    expect(html).not.toContain("aria-invalid");
    const wrong = renderToStaticMarkup(<blanks.Input {...base} question={q()} value={["", ""]} invalid="Type an answer." />);
    expect(wrong.match(/aria-invalid="true"/g)).toHaveLength(2);
    const error = wrong.match(/<p id="([^"]+)"[^>]*>Type an answer\.<\/p>/)?.[1];
    expect(wrong).toContain(`aria-describedby="${error}"`);
  });

  test("its result: each blank right or wrong with the answer, the count, Why", () => {
    const question = q({
      answers: [["SSH"], ["22"]],
      explanation: "SSH uses TCP 22.",
      response: answered({ value: ["ssh", "23"], earned: 1, marks: [true, false] }),
    });
    const html = renderToStaticMarkup(<blanks.Result question={question} number={2} open />);
    expect(html).toContain('aria-label="correct"');
    expect(html).toContain('aria-label="the answer is 22"');
    expect(html.match(/disabled=""/g)).toHaveLength(2);
    const shown = text(html);
    expect(shown).toContain("Q2.");
    expect(shown).toContain("1 of 2 right.");
    expect(shown).toContain("SSH uses TCP 22.");
  });

  test("skipped shows every answer", () => {
    const question = q({ answers: [["SSH"], ["22"]], response: answered({ status: "skipped" }) });
    const html = renderToStaticMarkup(<blanks.Result question={question} number={null} />);
    expect(html.match(/aria-label="the answer is /g)).toHaveLength(2);
    expect(text(html)).toContain("Skipped. The answers are shown in the blanks.");
  });
});

describe("the exercise's sentence (widgets/fill-blank.tsx) is unchanged", () => {
  test("open: plain inputs, no error wiring", () => {
    const html = renderToStaticMarkup(
      <BlankText segments={["A ", " listens on port ", "."]} blanks={2} values={["SSH"]} disabled={false} onChange={() => {}} />,
    );
    expect(html.startsWith('<p class="leading-9">')).toBe(true);
    expect(html.match(/border-input/g)).toHaveLength(2);
    expect(html).not.toContain("aria-describedby");
    expect(html).not.toContain("aria-invalid");
  });

  test("graded: borders and marks from the record", () => {
    const html = renderToStaticMarkup(
      <BlankText
        segments={["A ", " listens on port ", "."]}
        blanks={2}
        values={["SSH", "23"]}
        disabled
        graded={{ marks: [true, false], answers: [["SSH"], ["22"]] }}
      />,
    );
    expect(html).toContain("border-success");
    expect(html).toContain("border-destructive");
    expect(text(html)).toContain("22");
  });
});
