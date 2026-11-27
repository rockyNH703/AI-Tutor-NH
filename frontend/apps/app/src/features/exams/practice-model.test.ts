import { describe, expect, test } from "bun:test";

import type { ExamSnapshot } from "@/features/friendzone/exam-store";
import type { ReviewItem } from "@/lib/api/practice-exams";
import {
  OUTCOME_LABEL,
  NOT_COUNTED,
  choiceOf,
  endTestSummary,
  keysOf,
  mirrorKey,
  notCounted,
  questionMeta,
  readMirror,
  rowsFrom,
  saveLine,
  toWire,
  toggleKey,
  writeMirror,
} from "./practice-model";

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
  };
}

const snap = (patch: Partial<ExamSnapshot> = {}): ExamSnapshot => ({
  rows: new Map(),
  intents: new Map(),
  visits: new Set(),
  failed: new Set(),
  inFlight: false,
  offline: false,
  stale: 0,
  gone: false,
  ...patch,
});

describe("keys as the store's one choice", () => {
  test("several keys travel as one sorted choice and come back as the same list", () => {
    expect(choiceOf(["C", "A"])).toBe("A,C");
    expect(choiceOf([])).toBeNull();
    expect(keysOf("A,C")).toEqual(["A", "C"]);
    expect(keysOf(null)).toEqual([]);
  });

  test("a checkbox toggles its key; the list stays sorted", () => {
    expect(toggleKey(["C"], "A")).toEqual(["A", "C"]);
    expect(toggleKey(["A", "C"], "A")).toEqual(["C"]);
  });

  test("server responses become store rows by question number; untouched ones stay not visited", () => {
    const rows = rowsFrom(
      [
        { index: 0, keys: ["B"], marked: false, visited: true },
        { index: 1, keys: [], marked: false, visited: false },
        { index: 2, keys: [], marked: true, visited: true },
      ],
      7,
    );
    expect(rows).toEqual([
      { round: 1, choice: "B", marked: false, rev: 7 },
      { round: 3, choice: null, marked: true, rev: 7 },
    ]);
  });

  test("the queue goes on the wire by index, a visit as an empty visited answer", () => {
    expect(
      toWire([
        { round: 2, op: "set", choice: "A,D", marked: true, baseRev: 1 },
        { round: 5, op: "visit" },
      ]),
    ).toEqual([
      { index: 1, keys: ["A", "D"], marked: true, visited: true },
      { index: 4, keys: [], marked: false, visited: true },
    ]);
  });
});

describe("the save status says only what is true", () => {
  test("saved, saving, not saved, offline with answers only on this device", () => {
    expect(saveLine(snap(), true).text).toBe("All answers saved");
    expect(saveLine(snap({ inFlight: true }), true).text).toBe("Saving…");
    const one = new Map([[3, { choice: "A", marked: false }]]);
    expect(saveLine(snap({ intents: one, failed: new Set([3]) }), true)).toEqual({ kind: "unsaved", text: "1 answer not saved." });
    const two = new Map([
      [3, { choice: "A", marked: false }],
      [4, { choice: null, marked: true }],
    ]);
    expect(saveLine(snap({ intents: two }), false).text).toBe(
      "You're offline: 2 answers are only on this device and will be sent when you reconnect. Answers not sent before time runs out can't be counted.",
    );
    expect(saveLine(snap({ intents: one, offline: true }), true).text).toContain("1 answer is only on this device");
    expect(saveLine(snap(), false).kind).toBe("offline");
  });
});

describe("not counted", () => {
  const review = (index: number, keys: string[], outcome: ReviewItem["outcome"] = "unanswered"): ReviewItem => ({
    index,
    keys,
    correctKeys: ["A"],
    explanation: "",
    outcome,
  });

  test("answers only on this device at time-up, and late items, are not counted; ones that arrived are", () => {
    const result = notCounted(
      [review(0, []), review(1, ["B"], "incorrect"), review(2, ["C"], "incorrect"), review(3, [])],
      [
        { index: 0, keys: ["A"], marked: false }, // never arrived
        { index: 1, keys: ["B"], marked: true }, // arrived (the server has B)
        { index: 2, keys: ["D"], marked: false }, // the server kept the older C
        { index: 3, keys: [], marked: true }, // a clear or a mark: nothing to count
      ],
      [3],
    );
    expect(result).toEqual([0, 2, 3]);
    expect(notCounted([review(0, [])], [], [])).toEqual([]);
    expect(NOT_COUNTED).toBe("Not counted (not saved before time ran out)");
    expect(OUTCOME_LABEL.unanswered).toBe("Unanswered");
  });

  test("the mirror is kept per exam and read back defensively", () => {
    const storage = memoryStorage();
    const id = "11111111-1111-4111-8111-111111111111";
    writeMirror(storage, id, 4, [{ index: 0, keys: ["A"], marked: false }]);
    expect(storage.data.has(mirrorKey(id))).toBe(true);
    expect(readMirror(storage, id)).toEqual({ rev: 4, items: [{ index: 0, keys: ["A"], marked: false }] });
    writeMirror(storage, id, 5, []);
    expect(storage.data.has(mirrorKey(id))).toBe(false);
    storage.setItem(mirrorKey(id), "{not json");
    expect(readMirror(storage, id)).toEqual({ rev: 0, items: [] });
    storage.setItem(mirrorKey(id), JSON.stringify({ rev: 1, items: [{ index: "x" }, { index: 2, keys: ["B", 3], marked: 1 }] }));
    expect(readMirror(storage, id)).toEqual({ rev: 1, items: [] });
    expect(readMirror(null, id)).toEqual({ rev: 0, items: [] });
  });
});

describe("labels", () => {
  test("the question line names its number and the scoring rule", () => {
    expect(questionMeta(3, 10, { marksPerQuestion: 1, negativeMarking: 0 })).toBe("Question 3 of 10 · 1 mark · no negative marking");
    expect(questionMeta(1, 5, { marksPerQuestion: 2, negativeMarking: 1 })).toBe("Question 1 of 5 · 2 marks · −1 for a wrong answer");
  });
});

describe("the End Test summary", () => {
  test("lists unanswered, marked and unsent questions by number", () => {
    const rows = new Map([
      [1, { round: 1, choice: "A", marked: false, rev: 1 }],
      [2, { round: 2, choice: null, marked: true, rev: 1 }],
      [3, { round: 3, choice: "B", marked: true, rev: 1 }],
    ]);
    const intents = new Map([[4, { choice: "C", marked: false }]]);
    expect(endTestSummary(snap({ rows, intents }), 5)).toEqual({ unanswered: [2, 5], marked: [2, 3], unsent: [4] });
  });
});
