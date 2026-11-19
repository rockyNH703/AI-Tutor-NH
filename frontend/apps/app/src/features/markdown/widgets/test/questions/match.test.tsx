import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { AssessmentAnswer, AssessmentQuestion } from "@/lib/api/types";
import type { InputProps } from "../question-props";
import pack, { asChoices } from "./match";

const match = pack.match!;

function question(extra: Partial<AssessmentQuestion> = {}): AssessmentQuestion {
  return {
    kind: "match",
    text: "Match each protocol to its type.",
    options: null,
    segments: null,
    blanks: null,
    response: null,
    correct: null,
    answers: null,
    explanation: null,
    left: ["OSPF", "RIP", "BGP"],
    right: ["Path-vector", "Link-state IGP", "Distance-vector", "Static"],
    ...extra,
  };
}

function answered(answer: (number | null)[], marks: boolean[]): AssessmentAnswer {
  const earned = marks.filter(Boolean).length;
  return { status: "answered", value: null, earned, possible: 3, at: "2026-11-18T10:00:00Z", marks, answer };
}

/** What a reader sees: tags as spaces, entities read, spaces collapsed. */
const visible = (html: string) =>
  html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();

const input = (extra: Partial<InputProps<(number | null)[]>> = {}) =>
  renderToStaticMarkup(
    <match.Input
      question={question()}
      number={2}
      value={[null, null, null]}
      onChange={() => {}}
      disabled={false}
      mode="practice"
      tries={[]}
      labelledBy="legend"
      {...extra}
    />,
  );

describe("match values", () => {
  test("starts with no choice per left item and is complete only when every item has one", () => {
    expect(match.empty(question())).toEqual([null, null, null]);
    expect(match.isComplete(question(), [1, null, 0])).toBe(false);
    expect(match.isComplete(question(), [1, 2])).toBe(false);
    expect(match.isComplete(question(), [1, 2, 0])).toBe(true);
    expect(match.toRequest(question(), [1, null, 0])).toEqual([1, null, 0]);
  });

  test("a stored value is used only when it fits the question", () => {
    expect(asChoices([1, null, 3], question())).toEqual([1, null, 3]);
    expect(asChoices([1, 2], question())).toBeNull();
    expect(asChoices([1, 2, 4], question())).toBeNull();
    expect(asChoices([1, 2, -1], question())).toBeNull();
    expect(asChoices([1, 2, 0.5], question())).toBeNull();
    expect(asChoices(["1", 2, 0], question())).toBeNull();
    expect(asChoices("1,2,0", question())).toBeNull();
  });

  test("an exam draft comes first, then the final answer; anything else is ignored", () => {
    expect(match.fromRecord(question({ draft: [3, null, 0] }))).toEqual([3, null, 0]);
    expect(match.fromRecord(question({ response: answered([1, 2, 0], [true, true, true]) }))).toEqual([1, 2, 0]);
    expect(match.fromRecord(question({ draft: "garbage" }))).toBeNull();
    expect(match.fromRecord(question())).toBeNull();
  });
});

describe("match input", () => {
  test("one select per left item, named by that item, wrapping, at least 44px on touch", () => {
    const html = input();
    const triggers = html.match(/<button[^>]*role="combobox"[^>]*>/g) ?? [];
    expect(triggers).toHaveLength(3);
    for (const [index, term] of ["OSPF", "RIP", "BGP"].entries()) {
      const labelledBy = triggers[index]?.match(/aria-labelledby="([^"]+)"/)?.[1];
      expect(labelledBy).toBeTruthy();
      expect(html.match(new RegExp(`id="${labelledBy}"[^>]*>([^<]*)<`))?.[1]).toBe(term);
      expect(triggers[index]).toContain("min-h-11");
      expect(triggers[index]).toContain("whitespace-normal");
      expect(triggers[index]).not.toMatch(/ h-10 /);
    }
    expect(visible(html)).toContain("Choose a match");
    expect(html).not.toContain("draggable");
  });

  test("an error is shown and bound to every select; disabled disables them", () => {
    const html = input({ invalid: "Choose a match for every item.", describedBy: "hint" });
    const error = html.match(/<p id="([^"]+)"[^>]*>Choose a match for every item\.<\/p>/)?.[1];
    expect(error).toBeTruthy();
    const triggers = html.match(/<button[^>]*role="combobox"[^>]*>/g) ?? [];
    for (const trigger of triggers) {
      expect(trigger).toContain('aria-invalid="true"');
      expect(trigger).toContain(`aria-describedby="hint ${error}"`);
    }
    const off = input({ disabled: true }).match(/<button[^>]*role="combobox"[^>]*>/g) ?? [];
    expect(off.every((trigger) => trigger.includes('disabled=""'))).toBe(true);
  });
});

describe("match result", () => {
  const result = (q: AssessmentQuestion) => visible(renderToStaticMarkup(<match.Result question={q} number={3} />));

  test("wrapping rows with a word per pair, and the right pair when one is wrong or empty", () => {
    const text = result(
      question({
        correctList: [1, 2, 0],
        explanation: "OSPF floods link states.",
        response: answered([1, 3, null], [true, false, false]),
      }),
    );
    expect(text).toContain("Q3. Match each protocol to its type.");
    expect(text).toContain("1 of 3 pairs right.");
    expect(text).toContain("OSPF → Link-state IGP · correct");
    expect(text).toContain("RIP → Static · wrong, should be Distance-vector");
    expect(text).toContain("BGP → no choice · wrong, should be Path-vector");
    expect(text).toContain("Why");
  });

  test("Why is the shared disclosure: the same focus ring as every kind's", () => {
    const html = renderToStaticMarkup(
      <match.Result question={question({ correctList: [1, 2, 0], explanation: "x", response: answered([1, 2, 0], [true, true, true]) })} number={1} />,
    );
    expect(html).toMatch(/<summary class="focus-ring[^"]*"[^>]*>Why<\/summary>/);
  });

  test("a skipped question lists the right pairs; nothing is drawn before it is final", () => {
    const skipped: AssessmentAnswer = {
      status: "skipped",
      value: null,
      earned: 0,
      possible: 3,
      at: "2026-11-18T10:00:00Z",
      marks: null,
    };
    const text = result(question({ correctList: [1, 2, 0], response: skipped }));
    expect(text).toContain("Skipped. The pairs:");
    expect(text).toContain("OSPF → Link-state IGP");
    expect(text).not.toContain("correct");
    expect(text).not.toContain("wrong");
    expect(result(question())).toBe("");
  });
});
