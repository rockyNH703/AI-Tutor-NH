import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { AssessmentAnswer, AssessmentQuestion } from "@/lib/api/types";
import type { KindModule } from "../question-props";
import pack, { ids, outcomeOf, toggled } from "./choice";

const multi = pack.multi as KindModule<number[]>;
const single = pack.mcq as KindModule<number | null>;

function q(over: Partial<AssessmentQuestion> = {}): AssessmentQuestion {
  return {
    kind: "multi",
    text: "Which protocols are connection-oriented?",
    options: ["TCP", "UDP", "SCTP", "ICMP"],
    segments: null,
    blanks: null,
    response: null,
    correct: null,
    answers: null,
    explanation: null,
    choose: 2,
    ...over,
  };
}

function answered(over: Partial<AssessmentAnswer>): AssessmentAnswer {
  return { status: "answered", value: null, earned: 0, possible: 1, at: "2026-11-18T10:00:00Z", marks: null, ...over };
}

const base = { number: 1, onChange: () => {}, disabled: false, mode: "practice" as const, tries: [], labelledBy: "legend" };
const text = (html: string) => html.replace(/<[^>]+>/g, "");

describe("multiple choice (multi)", () => {
  test("a toggle keeps the value sorted and unique", () => {
    expect(toggled([2], 0)).toEqual([0, 2]);
    expect(toggled([0, 2], 2)).toEqual([0]);
    expect(toggled([], 3)).toEqual([3]);
    expect(toggled([2], 10)).toEqual([2, 10]); // by value, not as text
    expect(toggled(["sw1", "l2"], "fw")).toEqual(["fw", "l2", "sw1"]); // the diagram's ids
  });

  test("complete once one is chosen; the request is sorted and unique", () => {
    expect(multi.isComplete(q(), [])).toBe(false);
    expect(multi.isComplete(q(), [1])).toBe(true);
    expect(multi.toRequest(q(), [2, 0, 2])).toEqual([0, 2]);
    expect(multi.empty(q())).toEqual([]);
  });

  test("reads an exam draft, else the final answer, else nothing", () => {
    expect(multi.fromRecord(q({ draft: [1, 3] }))).toEqual([1, 3]);
    expect(multi.fromRecord(q({ response: answered({ answer: [0, 2] }) }))).toEqual([0, 2]);
    expect(multi.fromRecord(q({ response: answered({ status: "skipped" }) }))).toBeNull();
    expect(multi.fromRecord(q({ draft: "not indices" }))).toBeNull();
  });

  test("checkboxes with 'Choose 2.' read with each option, the chosen ones checked", () => {
    const html = renderToStaticMarkup(<multi.Input {...base} question={q()} value={[0]} />);
    expect(html.match(/type="checkbox"/g)).toHaveLength(4);
    expect(html.match(/checked=""/g)).toHaveLength(1);
    expect(text(html)).toContain("Choose 2.");
    const hint = html.match(/<p id="([^"]+)"[^>]*>Choose 2\.<\/p>/)?.[1];
    expect(hint).toBeTruthy();
    expect(html).toContain(`aria-describedby="${hint}"`);
    expect(html).not.toContain("aria-invalid");
  });

  test("an error is shown, read first and marks every option invalid", () => {
    const html = renderToStaticMarkup(
      <multi.Input {...base} question={q()} value={[]} describedBy="scenario" invalid="Choose at least one." />,
    );
    expect(text(html)).toContain("Choose at least one.");
    expect(html.match(/aria-invalid="true"/g)).toHaveLength(4);
    const error = html.match(/<p id="([^"]+)"[^>]*>Choose at least one\.<\/p>/)?.[1];
    expect(html).toMatch(new RegExp(`aria-describedby="${error} [^"]+ scenario"`));
  });

  test("a wrong answer marks every option with an icon and a word", () => {
    const question = q({ correctList: [0, 2], response: answered({ answer: [0, 1], marks: [true, false, false, true] }) });
    const shown = text(renderToStaticMarkup(<multi.Result question={question} number={3} />));
    expect(shown).toContain("Q3.");
    expect(shown).toContain("Not quite: you chose A, B; the answer is A, C.");
    expect(shown).toContain("A.TCP (your choice, correct)");
    expect(shown).toContain("B.UDP (your choice, wrong)");
    expect(shown).toContain("C.SCTP (correct, missed)");
    expect(shown).toContain("D.ICMP");
    expect(shown).not.toContain("D.ICMP (");
  });

  test("right and skipped read as such", () => {
    const right = q({ correctList: [0, 2], response: answered({ answer: [0, 2], earned: 1 }) });
    expect(text(renderToStaticMarkup(<multi.Result question={right} number={null} />))).toContain("Correct! You chose A, C.");
    const skipped = q({ correctList: [0, 2], response: answered({ status: "skipped" }), explanation: "Both keep state." });
    const shown = text(renderToStaticMarkup(<multi.Result question={skipped} number={1} open />));
    expect(shown).toContain("Skipped. The answer is A, C.");
    expect(shown).toContain("A.TCP (correct)");
    expect(shown).toContain("Both keep state.");
  });

  test("no result before it is final", () => {
    expect(renderToStaticMarkup(<multi.Result question={q()} number={1} />)).toBe("");
  });
});

describe("single choice (mcq) keeps working", () => {
  const question = q({ kind: "mcq", choose: null, options: ["Hub", "Switch", "Router"] });

  test("radios, one chosen, no hint", () => {
    const html = renderToStaticMarkup(<single.Input {...base} question={question} value={1} />);
    expect(html.match(/type="radio"/g)).toHaveLength(3);
    expect(html.match(/checked=""/g)).toHaveLength(1);
    expect(html).not.toContain("aria-describedby");
    expect(single.isComplete(question, null)).toBe(false);
    expect(single.toRequest(question, 2)).toBe(2);
  });

  test("its result says what was chosen and the answer", () => {
    const right = { ...question, correct: 1, response: answered({ value: 1, earned: 1 }) };
    expect(text(renderToStaticMarkup(<single.Result question={right} number={2} />))).toContain("Correct! You chose B. Switch");
    const wrong = { ...question, correct: 1, response: answered({ value: 2 }) };
    expect(text(renderToStaticMarkup(<single.Result question={wrong} number={2} />))).toContain(
      "Not quite: you chose C, the answer is B. Switch",
    );
  });
});

describe("shared parts", () => {
  test("ids drops what is missing", () => {
    expect(ids(false, null, undefined, "")).toBeUndefined();
    expect(ids("a", false, "b")).toBe("a b");
  });

  test("outcomes: right, partly, wrong, skipped", () => {
    expect(outcomeOf(answered({ earned: 1, possible: 1 }))).toBe("right");
    expect(outcomeOf(answered({ earned: 2, possible: 4 }))).toBe("partly");
    expect(outcomeOf(answered({ earned: 0, possible: 4 }))).toBe("wrong");
    expect(outcomeOf(answered({ status: "skipped" }))).toBe("skipped");
  });
});
