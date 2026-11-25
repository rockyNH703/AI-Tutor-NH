import { describe, expect, test } from "bun:test";

import type { Battle } from "./battle-api";
import {
  clockText,
  completionTracker,
  countdownLeft,
  examCounts,
  firstQuestion,
  focusAfterClose,
  marksText,
  paletteState,
  pillLabel,
  readMessage,
  saveStatus,
  shortcutAllowed,
  staleMessage,
  summaryCounts,
  summaryText,
  timeTakenText,
  warnLevel,
} from "./exam-model";
import { POLL, nextPollDelay } from "./poll";

const T0 = Date.parse("2026-11-25T10:00:00.000Z");
const at = (ms: number) => new Date(T0 + ms).toISOString();

describe("the palette", () => {
  test("the five states come from the row and the unsaved change, which wins", () => {
    expect(paletteState(undefined, undefined)).toBe("not-visited");
    expect(paletteState(undefined, undefined, true)).toBe("not-answered");
    expect(paletteState({ choice: null, marked: false }, undefined)).toBe("not-answered");
    expect(paletteState({ choice: "B", marked: false }, undefined)).toBe("answered");
    expect(paletteState({ choice: null, marked: true }, undefined)).toBe("marked");
    expect(paletteState({ choice: "B", marked: true }, undefined)).toBe("answered-marked");
    expect(paletteState({ choice: "B", marked: false }, { choice: null, marked: false })).toBe("not-answered");
    expect(paletteState(undefined, { choice: "A", marked: true })).toBe("answered-marked");
  });

  test("counts add up to the paper; Not saved counts the unsaved changes; answered includes answered and marked (D6)", () => {
    const snapshot = {
      rows: new Map([
        [1, { round: 1, choice: "A", marked: false, rev: 1 }],
        [2, { round: 2, choice: null, marked: false, rev: 1 }],
        [3, { round: 3, choice: "C", marked: true, rev: 2 }],
      ]),
      intents: new Map([[4, { choice: null, marked: true }]]),
      visits: new Set([5]),
    };
    const counts = examCounts(snapshot, 10);
    expect(counts).toEqual({ answered: 1, "not-answered": 2, marked: 1, "answered-marked": 1, "not-visited": 5, notSaved: 1 });
    expect(summaryCounts(counts)).toEqual({ answered: 2, notAnswered: 2, marked: 1, notVisited: 5 });
    expect(summaryText(counts)).toBe(
      "Answered 2 (1 of them marked for review), not answered 2, marked for review without an answer 1, not visited 5.",
    );
  });

  test("a pill says its state in words, and Not saved on top of it (never colour alone)", () => {
    expect(pillLabel(3, "answered-marked", false)).toBe("Question 3, answered and marked for review");
    expect(pillLabel(7, "answered", true)).toBe("Question 7, answered, not saved");
    expect(pillLabel(1, "not-visited", false)).toBe("Question 1, not visited");
  });

  test("the first question: &q= when valid, else the lowest unanswered", () => {
    const answered = (round: number) => round <= 2;
    expect(firstQuestion("7", 10, answered)).toBe(7);
    expect(firstQuestion("11", 10, answered)).toBe(3);
    expect(firstQuestion("x", 10, answered)).toBe(3);
    expect(firstQuestion(null, 2, answered)).toBe(1);
  });
});

describe("time", () => {
  test("the countdown never goes up within one exam, even when the clock offset moves", () => {
    let shown = { endsAt: at(600_000), left: countdownLeft(null, at(600_000), 300) };
    for (const raw of [299, 301, 305, 298]) {
      const left = countdownLeft(shown, shown.endsAt, raw);
      expect(left).toBeLessThanOrEqual(shown.left);
      shown = { ...shown, left };
    }
    expect(shown.left).toBe(298);
    expect(countdownLeft(shown, at(1_200_000), 900)).toBe(900); // another exam starts fresh
  });

  test("m:ss, and the warnings at 5 minutes and 1 minute", () => {
    expect(clockText(600)).toBe("10:00");
    expect(clockText(65)).toBe("1:05");
    expect(clockText(0)).toBe("0:00");
    expect([301, 300, 61, 60, 1].map(warnLevel)).toEqual([null, 5, 5, 1, 1]);
  });

  test("time taken, or Time ran out at the deadline", () => {
    const battle = { startsAt: at(0), endsAt: at(600_000) };
    expect(timeTakenText({ timeTakenSeconds: 245 }, battle)).toBe("4:05");
    expect(timeTakenText({ timeTakenSeconds: 600 }, battle)).toBe("Time ran out");
    expect(timeTakenText({ timeTakenSeconds: null }, battle)).toBe("–");
  });

  test("polling a running exam: every 10 s, once at the cutoff + 100 ms, never a 100 ms storm", () => {
    const exam = { secondsPerQuestion: 60, endsAt: at(58_000), cutoffAt: at(60_000), questions: [], responses: [], submittedAt: null, opponentSubmitted: false };
    const base = { status: "active", format: "exam", nextChangeAt: exam.cutoffAt, question: null, rounds: [] } as unknown as Battle;
    const live = { offset: 0, localNow: T0, hidden: false, failures: 0 };
    expect(nextPollDelay({ ...base, exam }, live)).toBe(POLL.exam);
    expect(nextPollDelay({ ...base, exam }, { ...live, localNow: T0 + 55_000 })).toBe(5000 + POLL.examMargin);
    expect(nextPollDelay({ ...base, exam }, { ...live, localNow: T0 + 61_000 })).toBe(POLL.roundOpen);
    expect(nextPollDelay({ ...base, exam: { ...exam, submittedAt: at(1000) } }, live)).toBe(POLL.waiting);
    expect(nextPollDelay({ ...base, exam: null, nextChangeAt: at(2000) }, live)).toBe(2000 + POLL.margin); // the start
  });
});

describe("the answer shortcuts' guard", () => {
  const element = (inside: string[] = []) => ({ closest: (selector: string) => (inside.some((part) => selector.includes(part)) ? {} : null) });
  const press = (patch: Partial<Parameters<typeof shortcutAllowed>[0]> = {}) =>
    shortcutAllowed({
      key: "b",
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
      repeat: false,
      target: element(),
      inRegion: true,
      layerOpen: false,
      locked: false,
      ...patch,
    });

  test("allowed in the exam region, also with a radio focused", () => {
    expect(press()).toBe(true);
    expect(press({ target: element(["input[type=radio]"]) })).toBe(true); // the text-entry selector excludes radios
  });

  test("never from the palette, a sheet or dialog, a text field, with a modifier, a held key or once locked", () => {
    expect(press({ target: element(["data-exam-palette"]) })).toBe(false);
    expect(press({ layerOpen: true })).toBe(false);
    expect(press({ target: element(["textarea"]) })).toBe(false);
    expect(press({ inRegion: false })).toBe(false);
    for (const modifier of ["altKey", "ctrlKey", "metaKey", "shiftKey"] as const) expect(press({ [modifier]: true })).toBe(false);
    expect(press({ repeat: true })).toBe(false);
    expect(press({ locked: true })).toBe(false);
  });
});

describe("words", () => {
  test("the save status, in words beside its icon", () => {
    const snap = (patch = {}) => ({ intents: new Map(), failed: new Set<number>(), inFlight: false, offline: false, ...patch });
    expect(saveStatus(snap()).text).toBe("Saved");
    expect(saveStatus(snap({ inFlight: true })).text).toBe("Saving…");
    expect(saveStatus(snap({ intents: new Map([[1, {}], [2, {}]]), failed: new Set([1]) })).text).toBe("2 not saved");
    expect(saveStatus(snap({ offline: true, intents: new Map([[1, {}]]) })).text).toBe("Offline: 1 not saved");
  });

  test("after a 409, the message comes from the new state and never names a reason", () => {
    const state = (patch: Partial<Battle>) =>
      ({ status: "completed", result: "points", endReason: "finished", exam: null, startsAt: at(0), endsAt: at(600_000), me: { timeTakenSeconds: 600 }, ...patch }) as Battle;
    expect(staleMessage(state({}), 3)).toBe("3 answers weren't saved before time ran out.");
    expect(staleMessage(state({}), 1)).toBe("1 answer wasn't saved before time ran out.");
    expect(staleMessage(state({}), 0)).toBeNull();
    expect(staleMessage(state({ result: "void", endReason: "absent" }), 2)).toContain("before time ran out");
    // Finished by a submit from another tab, not by the deadline: no claim that time ran out.
    const early = { timeTakenSeconds: 120 } as Battle["me"];
    expect(staleMessage(state({ me: early }), 2)).toBe("2 answers weren't saved before the battle finished.");
    expect(staleMessage(state({ result: "void", endReason: "absent", me: { timeTakenSeconds: null } as Battle["me"] }), 1)).toBe(
      "1 answer wasn't saved before the battle finished.",
    );
    for (const endReason of ["blocked", "unfriended", "suspended", "upgraded"])
      expect(staleMessage(state({ result: "void", endReason }), 2)).toBe("This battle has ended.");
    expect(staleMessage(state({ status: "active" }), 2)).toBeNull(); // submitted elsewhere: the waiting screen says it
  });

  test("a submit the server didn't apply (time ran out first) says how many answers weren't saved", () => {
    const state = (patch: Partial<Battle>) =>
      ({ status: "completed", result: "points", endReason: "finished", exam: null, startsAt: at(0), endsAt: at(600_000), me: { timeTakenSeconds: 600 }, ...patch }) as Battle;
    expect(readMessage(state({ submitApplied: false }), 2, false)).toBe("2 answers weren't saved before time ran out.");
    expect(readMessage(state({ submitApplied: false, result: "void", endReason: "ended" }), 2, false)).toBe("This battle has ended.");
    expect(readMessage(state({ submitApplied: false, status: "active" }), 2, false)).toBeNull(); // submitted in another tab
    // An applied submit, or an ordinary read, says nothing; a re-read after a 409 still does.
    expect(readMessage(state({ submitApplied: true }), 2, false)).toBeNull();
    expect(readMessage(state({ submitApplied: null }), 2, false)).toBeNull();
    expect(readMessage(state({}), 2, false)).toBeNull();
    expect(readMessage(state({}), 2, true)).toBe("2 answers weren't saved before time ran out.");
  });

  test("marks use a real minus sign", () => {
    expect(marksText(-3)).toBe("−3");
    expect(marksText(12)).toBe("12");
  });

  test("the completion event fires exactly once, and never for a battle opened already finished", () => {
    const fresh = completionTracker();
    expect(["active", "active", "completed", "completed"].map((status) => fresh(status as Battle["status"]))).toEqual([false, false, true, false]);
    const history = completionTracker();
    expect(history("completed")).toBe(false);
  });
});

describe("focus when the Questions sheet or the submit dialog closes", () => {
  test("a dismissal returns to the button that opened it; a pill to the question; the deadline and a submit leave it to their heading", () => {
    // The openers are plain buttons, so Radix alone would drop focus on the page body after Escape.
    expect(focusAfterClose("dismiss")).toBe("opener");
    expect(focusAfterClose("pill")).toBe("heading");
    expect(focusAfterClose("deadline")).toBeNull();
    expect(focusAfterClose("submit")).toBeNull();
  });
});
