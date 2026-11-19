import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { ApiError } from "@/lib/api/errors";
import type { Assessment, AssessmentQuestion } from "@/lib/api/types";
import { isExamSource } from "../../assessment";
import type { TestState } from "../../context";
import { loadKinds } from "./kinds";
import { pillName } from "./navigator";
import choicePack from "./questions/choice";
import orderPack from "./questions/order";
import { failure } from "./record";
import { feedbackMessage, progressLine, questionList, resultLine, scoreLine } from "./review";
import {
  LOAD_FAILED,
  TestCard,
  afterSave,
  cardState,
  examRequest,
  holdsAnswer,
  missingLine,
  notSaved,
  problemOf,
  sayRefused,
} from "./test-card";

/** Records as the server's public() gives them (app/assessments/state.py): every field, keys only once final. */
function question(overrides: Partial<AssessmentQuestion> = {}): AssessmentQuestion {
  return {
    kind: "mcq",
    text: "Which layer routes packets?",
    options: ["Data link", "Network", "Transport", "Session"],
    segments: null,
    blanks: null,
    response: null,
    correct: null,
    answers: null,
    explanation: null,
    ...overrides,
  };
}

function record(overrides: Partial<Assessment> = {}): Assessment {
  return {
    id: "m:0",
    position: 0,
    schemaVersion: 1,
    kind: "test",
    title: null,
    hash: "a".repeat(64),
    locator: "b".repeat(64),
    state: "not_started",
    answerable: true,
    total: 1,
    answered: 0,
    current: 0,
    earned: 0,
    possible: 1,
    questions: [question()],
    ...overrides,
  };
}

const answered = (earned: number, possible: number, extra: Partial<AssessmentQuestion["response"] & object> = {}) => ({
  status: "answered" as const,
  value: null,
  earned,
  possible,
  at: "2026-11-18T10:00:00+00:00",
  marks: null,
  ...extra,
});

const tests = (extra: Partial<TestState> = {}): TestState => ({
  streaming: false,
  assessments: [],
  error: null,
  notice: null,
  respond: async () => {},
  ...extra,
});

/** What the card shows: tags and React's text separators dropped, entities read. */
function shown(test: Assessment, extra: Partial<TestState> = {}, single = false): { html: string; text: string } {
  const html = renderToStaticMarkup(<TestCard test={test} single={single} tests={tests(extra)} onError={() => {}} />);
  const text = html
    .replace(/<!-- -->/g, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ");
  return { html, text };
}

describe("practice, v1 records (copy unchanged)", () => {
  test("an adaptive bank test shows its first question out of the set's total", () => {
    const { text, html } = shown(record({ total: 10 }));
    expect(text).toContain("Question 1 of 10");
    expect(text).not.toContain("correct so far");
    expect(html.match(/type="radio"/g)?.length).toBe(4);
    expect(text).toContain("Check answer");
    expect(text).toContain("Skip");
    expect(text).not.toContain("Show answer");
  });

  test("the grown record shows the answered question's result and the next one", () => {
    const grown = record({
      total: 10,
      answered: 1,
      current: 1,
      earned: 1,
      state: "active",
      questions: [
        question({ response: answered(1, 1, { value: 1 }), correct: 1, explanation: "Layer 3." }),
        question({ text: "Which protocol resolves IPv4 to MAC?", options: ["ARP", "DNS", "DHCP", "ICMP"] }),
      ],
    });
    const { text } = shown(grown);
    expect(text).toContain("Question 2 of 10 · 1 correct so far");
    expect(text).toContain("Correct! You chose B.");
    expect(text).toContain("Which protocol resolves IPv4 to MAC?");
    expect(text).toContain("Question 1: correct."); // the status line's one line per result
  });

  test("a finished v1 test keeps its score line", () => {
    const done = record({ answered: 1, current: null, earned: 1, possible: 1, state: "completed" });
    done.questions = [question({ response: answered(1, 1, { value: 1 }), correct: 1 })];
    expect(shown(done).text).toContain("You scored 1 out of 1.");
  });
});

describe("practice, v2 records", () => {
  const five = (responses: boolean) =>
    record({
      schemaVersion: 2,
      mode: "practice",
      title: "Routing basics",
      total: 5,
      answered: responses ? 5 : 3,
      current: responses ? null : 3,
      earned: responses ? 7 : 6,
      possible: 12,
      state: responses ? "completed" : "active",
      questions: [
        question({ response: answered(1, 1, { value: 1 }), correct: 1 }),
        question({ kind: "numeric", text: "MTU of Ethernet?", options: null, response: answered(1, 1, { answer: "1500" }) }),
        question({
          kind: "short",
          text: "Why use OSPF areas?",
          options: null,
          keyPoints: ["smaller LSDB", "summarisation"],
          response: answered(4, 4, { answer: "Smaller LSDB and summarisation.", marks: [true, true] }),
        }),
        question({
          kind: "long",
          text: "Explain the TCP handshake.",
          options: null,
          response: responses ? answered(1, 5, { answer: "SYN then ACK.", marks: [true, false, false, false, false] }) : null,
        }),
        question({ response: responses ? answered(0, 1, { value: 0 }) : null, correct: responses ? 1 : null }),
      ],
    });

  test("a completed practice test gives points and questions apart", () => {
    const { text } = shown(five(true), { ask: async () => "sent" });
    expect(text).toContain("You scored 7 of 12 points: 3 of 5 questions fully right, 1 partly.");
    expect(text.match(/Get feedback/g)?.length).toBe(2); // the two graded written answers
  });

  test("Get feedback needs the chat's ask: without it the card offers none", () => {
    expect(shown(five(true)).text).not.toContain("Get feedback");
  });

  test("the progress line counts points so far, and every kind offers Show answer", () => {
    const active = five(false);
    active.questions[3] = question({ kind: "mcq", text: "Pick one" });
    const { text } = shown(active);
    expect(text).toContain("Question 4 of 5 · 6 of 6 points so far");
    expect(text).toContain("Show answer");
    expect(text).not.toContain("Skip");
  });

  test("a wrong numeric try says how many tries are left and offers Try again", () => {
    const tried = record({
      schemaVersion: 2,
      mode: "practice",
      state: "active",
      pending: { question: 0, tries: ["1400"] },
      questions: [question({ kind: "numeric", text: "MTU of Ethernet?", options: null, maxTries: 3 })],
    });
    const { text } = shown(tried);
    expect(text).toContain("Not quite: 2 tries left.");
    expect(text).toContain("Try again");
  });

  test("a scenario is a named section that describes each question's group; a listing is drawn", () => {
    const withScenario = record({
      schemaVersion: 2,
      mode: "practice",
      scenario: {
        title: "Branch outage",
        summary: "Users at the branch can't reach the file server.",
        symptoms: ["Pings to 10.0.0.1 fail"],
        evidence: [{ label: "R1 log", content: "%LINK-3-UPDOWN: Gi0/1 down" }],
      },
      questions: [question({ listing: { title: "show ip route", content: "C 10.0.0.0/24\nS 0.0.0.0/0" } })],
    });
    const { html, text } = shown(withScenario);
    const label = html.match(/<section aria-labelledby="([^"]+)"/)?.[1];
    expect(label).toBeTruthy();
    expect(html).toContain(`id="${label}"`);
    const described = html.match(/<fieldset[^>]*aria-describedby="([^"]+)"/)?.[1] ?? "";
    expect(described.split(" ")).toContain(label as string);
    expect(text).toContain("Branch outage");
    expect(text).toContain("Pings to 10.0.0.1 fail");
    expect(html).toContain('aria-label="R1 log"');
    expect(html).toContain('aria-label="show ip route"');
  });
});

describe("mock exam, v2", () => {
  const exam = (overrides: Partial<Assessment> = {}) =>
    record({
      schemaVersion: 2,
      mode: "exam",
      submitted: false,
      revision: 3,
      total: 3,
      current: null,
      state: "active",
      title: "CCNA mock",
      questions: [
        // A key or explanation that reached the record by mistake must still never be drawn before Submit.
        question({ draft: 1, correct: 2, explanation: "SECRET-WHY" }),
        question({ text: "Which port does SSH use?", options: ["22", "23", "80"] }),
        question({ text: "Which is a private range?", options: ["10.0.0.0/8", "8.8.8.0/24"] }),
      ],
      ...overrides,
    });

  test("before Submit: navigator pills, the draft chosen, no keys or explanations", () => {
    const { html, text } = shown(exam());
    expect(text).toContain("Mock exam");
    expect(html).toContain('aria-label="Questions"');
    expect(html).toContain('aria-label="Question 1, answered"');
    expect(html).toContain('aria-label="Question 2"');
    expect(html.match(/aria-current="step"/g)?.length).toBe(1);
    expect(html).toMatch(/<input[^>]*type="radio"[^>]*checked=""/);
    expect(text).toContain("Previous");
    expect(text).toContain("Submit");
    expect(text).not.toContain("SECRET-WHY");
    expect(text).not.toContain("Why");
    expect(text).not.toContain("Correct");
    expect(text).not.toContain("You scored");
  });

  test("Clear answer empties the question shown; with nothing held it is aria-disabled and keeps focus", () => {
    const clear = (html: string) => /<button[^>]*>(?:(?!<\/button>).)*Clear answer<\/button>/.exec(html)?.[0] ?? "";
    const held = clear(shown(exam()).html);
    expect(held).not.toBe("");
    expect(held).not.toContain('aria-disabled="true"');
    const empty = exam({ questions: [question(), question({ text: "Which port does SSH use?" })] });
    expect(clear(shown(empty).html)).toContain('aria-disabled="true"');
  });

  test("a partly matched question (a draft, not yet an answer) can be cleared", async () => {
    await loadKinds(["match"]);
    const pairs = question({ kind: "match", text: "Match each port.", options: null, left: ["SSH", "DNS"] });
    const partial = exam({ questions: [{ ...pairs, right: ["53", "22"], draft: [1, null] }] });
    const html = shown(partial).html;
    expect(html).toContain('aria-label="Question 1"'); // a part matched is not an answer yet
    expect(/<button[^>]*>(?:(?!<\/button>).)*Clear answer<\/button>/.exec(html)?.[0]).not.toContain(
      'aria-disabled="true"',
    );
  });

  test("a question whose save failed is named in its pill, with Save again", () => {
    const { html, text } = shown(exam(), { unsaved: () => [2] });
    expect(html).toContain('aria-label="Question 3, not saved"');
    expect(text).toContain("Save again");
  });

  test("after Submit: the score and the review", () => {
    const submitted = exam({
      submitted: true,
      answered: 3,
      earned: 2,
      possible: 3,
      state: "completed",
      questions: [
        question({ response: answered(1, 1, { value: 1 }), correct: 1, explanation: "Layer 3." }),
        question({ text: "Which port does SSH use?", options: ["22", "23"], response: answered(1, 1, { value: 0 }), correct: 0 }),
        question({ text: "Which is a private range?", options: ["10.0.0.0/8", "8.8.8.0/24"], response: { ...answered(0, 1), status: "skipped" }, correct: 0 }),
      ],
    });
    const { html, text } = shown(submitted);
    expect(text).toContain("You scored 2 of 3 points: 2 of 3 questions fully right.");
    expect(text).toContain("Why");
    expect(html).not.toContain('aria-label="Questions"');
    expect(text).not.toContain("Submit your answers");
  });
});

test("a kind this card can't draw yet waits (Preparing), never guesses", () => {
  const unknown = record({ schemaVersion: 2, questions: [question({ kind: "mystery" as AssessmentQuestion["kind"] })] });
  expect(shown(unknown).text).toContain("Preparing your test…");
});

test("a kind that can't be drawn gives up with LOAD_FAILED; a failed mock exam's block never prints its source", () => {
  const mystery = question({ kind: "mystery" as AssessmentQuestion["kind"] });
  const unknown = record({ schemaVersion: 2, mode: "exam", questions: [mystery] });
  // "failed" is what TestCard reports with onError(LOAD_FAILED) (an unknown kind's load rejects in useKinds).
  expect(cardState("failed", unknown)).toBe("failed");
  expect(cardState("ready", unknown)).toBe("failed"); // a kind with no module never draws
  expect(cardState("loading", unknown)).toBe("loading");
  expect(cardState("ready", record())).toBe("ready");
  expect(LOAD_FAILED).toBe("This test couldn't be loaded. Reload to try again.");
  // RichBlock's fallback decides from the fence's own JSON: an exam's shows that note, never the source and its keys.
  const source = JSON.stringify({ mode: "exam", questions: [{ kind: "mystery", question: "Q?", answer: "KEY" }] }, null, 2);
  expect(isExamSource(source)).toBe(true);
  expect(isExamSource(source.replace(/^/gm, "> "))).toBe(true);
});

describe("mock exam: what counts, what is sent, what is said", () => {
  const order = orderPack.order!;
  const single = choicePack.mcq!;
  const steps = question({ kind: "order", text: "Order the steps.", options: null, items: ["Ping", "Trace", "Fix"] });

  test("an order never touched holds no answer (it is graded skipped), though the card shows its starting order", () => {
    expect(order.isComplete(steps, order.empty(steps))).toBe(true); // why the shown value can't be counted
    expect(holdsAnswer(steps, order)).toBe(false);
    expect(holdsAnswer({ ...steps, draft: [2, 0, 1] }, order)).toBe(true);
    expect(holdsAnswer({ ...steps, draft: [0, 1, 2] }, order)).toBe(true); // saved as shown: an answer
    expect(holdsAnswer(question(), single)).toBe(false);
    expect(holdsAnswer(question({ draft: 1 }), single)).toBe(true);
  });

  test("a touched order left as shown is saved, never sent as clear; an emptied choice clears", () => {
    expect(examRequest(steps, order, [0, 1, 2])).toEqual({ action: "save", value: [0, 1, 2] });
    expect(examRequest(steps, order, [1, 0, 2])).toEqual({ action: "save", value: [1, 0, 2] });
    expect(examRequest(question(), single, null)).toEqual({ action: "clear" });
    expect(examRequest(question(), single, 2)).toEqual({ action: "save", value: 2 });
    expect(examRequest(question({ kind: "multi" }), choicePack.multi!, [])).toEqual({ action: "clear" });
  });

  test("Clear answer (a cleared null) clears every kind, a touched order too", () => {
    expect(examRequest(steps, order, null)).toEqual({ action: "clear" });
    expect(examRequest(question(), single, null)).toEqual({ action: "clear" });
  });

  test("a question's 'wasn't saved' line goes once it saves; any other line stays", () => {
    expect(notSaved(2)).toBe("Question 3 wasn't saved.");
    expect(afterSave(notSaved(2), 2)).toBeNull();
    expect(afterSave(notSaved(1), 2)).toBe("Question 2 wasn't saved.");
    const submitLine = "Your answers weren't submitted. Check your connection and try again.";
    expect(afterSave(submitLine, 2)).toBe(submitLine);
    expect(afterSave(null, 0)).toBeNull();
  });
});

test("a refused value is heard: its input takes focus with the text selected, else the card's status line says it", () => {
  const calls: string[] = [];
  const field = { focus: () => calls.push("focus"), select: () => calls.push("select") } as unknown as HTMLElement;
  const said: string[] = [];
  sayRefused(field, "Use digits.", (line) => said.push(line));
  expect(calls).toEqual(["focus", "select"]);
  expect(said).toEqual([]);
  // A kind with no single input (match, order, lines, diagram) binds no inputRef.
  sayRefused(null, "Each choice can be picked only once.", (line) => said.push(line));
  expect(said).toEqual(["Each choice can be picked only once."]);
});

describe("the card's lines", () => {
  test("progress, score and result lines", () => {
    const v2 = record({ schemaVersion: 2, total: 5, answered: 3, earned: 7 });
    v2.questions = [3, 4, 2].map((possible) => question({ response: answered(1, possible) }));
    expect(progressLine(v2, 3)).toBe("Question 4 of 5 · 7 of 9 points so far");
    expect(progressLine(record({ total: 2, answered: 1, earned: 1 }), 1)).toBe("Question 2 of 2 · 1 correct so far");
    expect(scoreLine(record({ earned: 1, possible: 2 }))).toBe("You scored 1 out of 2.");
    const partial = record({ schemaVersion: 2, questions: [question({ response: answered(2, 4) })] });
    expect(resultLine(partial, 0, false)).toBe("Question 1: partly right, 2 of 4 points.");
    const shownAnswer = record({ schemaVersion: 2, questions: [question({ response: { ...answered(0, 1), status: "skipped" } })] });
    expect(resultLine(shownAnswer, 0, false)).toBe("Question 1: answer shown.");
    expect(resultLine(record({ questions: [question({ response: answered(0, 1) })] }), 0, true)).toBe("not quite.");
  });

  test("names of questions, pills and the Submit dialog", () => {
    expect(questionList([2])).toBe("Question 3");
    expect(questionList([4, 1])).toBe("Questions 2 and 5");
    expect(questionList([0, 1, 4])).toBe("Questions 1, 2 and 5");
    expect(pillName(3, true, true)).toBe("Question 3, not saved");
    expect(pillName(3, true, false)).toBe("Question 3, answered");
    expect(pillName(3, false, false)).toBe("Question 3");
    expect(missingLine(0, 5)).toBe("All 5 questions have an answer. You can't change them after you submit.");
    expect(missingLine(1, 5)).toStartWith("1 of 5 questions isn't fully answered;");
  });

  test("Get feedback's message: what was found and missed, then the full answer, within the chat's limit", () => {
    const written = question({
      kind: "short",
      text: "Why use OSPF areas?",
      options: null,
      keyPoints: ["smaller LSDB", "summarisation", "fewer SPF runs", "stability"],
      response: answered(2, 4, { answer: "They keep the LSDB small and allow summarisation.", marks: [true, true, false, false] }),
    });
    const test3 = record({ schemaVersion: 2, title: "OSPF" });
    expect(feedbackMessage(test3, written, 2)).toBe(
      "Review my answer to Q3 'Why use OSPF areas?' in 'OSPF'. Key points found 2 of 4 " +
        "(missing: fewer SPF runs; stability). My answer: They keep the LSDB small and allow summarisation.",
    );
    const long = { ...written, response: answered(4, 4, { answer: "x".repeat(20_000), marks: [true, true, true, true] }) };
    const message = feedbackMessage(record({ schemaVersion: 2 }), long, 0);
    expect(message.length).toBe(16_000);
    expect(message).toStartWith("Review my answer to Q1 'Why use OSPF areas?'. Key points found 4 of 4. My answer: x");
  });

  test("failures in the app's words: a 422's reason, a numeric's format, a 429", () => {
    const invalid = (reason?: string) => new ApiError(422, "validation_error", reason ? { fields: [{ field: "value", reason }] } : {});
    expect(failure(invalid("duplicate"), "test", "multi")).toBe("Each choice can be picked only once.");
    expect(failure(invalid(), "test", "numeric")).toStartWith("Use digits, with an optional leading minus");
    expect(failure(invalid("value"), "test", "cli")).toBe("This answer doesn't fit the question. Check it and try again.");
    expect(failure(new ApiError(429, "rate_limited"))).toBe("Too many answers at once. Wait a moment and try again.");
    expect(failure(new ApiError(409, "assessment_changed"), "exercise")).toStartWith("This exercise moved on elsewhere");
    expect(failure(new Error("offline"))).toBe("Your answer wasn't saved. Check your connection and try again.");
  });

  test("a 409 gets no line of the card's own: the store's notice (posted after its reload) says it", () => {
    expect(problemOf(new ApiError(409, "assessment_changed"), "numeric")).toBeNull();
    expect(problemOf(new ApiError(422, "validation_error"), "numeric")).toStartWith("Use digits, with an optional leading minus");
    expect(problemOf(new Error("offline"))).toBe("Your answer wasn't saved. Check your connection and try again.");
  });

  test("the store's notice for this test is the card's status line, in practice and in an exam", () => {
    const notice = { position: 0, text: "Changed in another tab. The test now shows where it is.", seq: 1 };
    const exam = record({ schemaVersion: 2, mode: "exam", submitted: false, current: null, state: "active" });
    for (const test of [record(), exam]) {
      expect(shown(test, { notice }).html).toMatch(/role="status"[^>]*>Changed in another tab\./);
      expect(shown(test, { notice: { ...notice, position: 1 } }).text).not.toContain("Changed in another tab");
    }
  });
});
