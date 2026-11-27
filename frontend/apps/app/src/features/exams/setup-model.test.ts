import { describe, expect, test } from "bun:test";

import type { ExamOptions, ExamQuiz } from "@/lib/api/practice-exams";
import {
  SCORING,
  availableByDifficulty,
  clampCount,
  defaultCount,
  defaultSelection,
  durationMinutes,
  durationOptions,
  keyOf,
  quizzesHref,
  setupHref,
  topicHref,
  trackFor,
  trackTopics,
} from "./setup-model";

const quiz = (topicId: string, chapter: string, available: [number, number, number], book = "ACL Deep Dive"): ExamQuiz => ({
  topicId,
  title: `Quiz ${topicId}`,
  chapter,
  book,
  available: { easy: available[0], medium: available[1], hard: available[2], total: available[0] + available[1] + available[2] },
});

const QUIZZES: ExamQuiz[] = [
  quiz("T1", "ACL fundamentals", [3, 5, 1]),
  quiz("T2", "ACL fundamentals", [2, 0, 0]),
  quiz("T3", "ACL configuration", [0, 4, 4]),
  quiz("T4", "OSPF basics", [6, 6, 6], "CCNA Core"),
];

const DURATIONS: ExamOptions["durations"] = { secondsPerQuestion: 60, multipliers: [1, 1.5, 2], maxMinutes: 60 };

describe("hierarchy and defaults", () => {
  test("a Topic's key is the same one the Quizzes & Games page uses (track + topic)", () => {
    expect(keyOf(QUIZZES[0] as ExamQuiz)).toBe("acl-deep-dive-acl-fundamentals");
  });

  test("the Track is the clicked Quiz's, else the Topic's; null when neither is in the month", () => {
    expect(trackFor(QUIZZES, "acl-deep-dive-acl-fundamentals", "T4")).toBe("CCNA Core");
    expect(trackFor(QUIZZES, "acl-deep-dive-acl-configuration", null)).toBe("ACL Deep Dive");
    expect(trackFor(QUIZZES, "nothing-here", "T9")).toBeNull();
  });

  test("the Track's Quizzes are grouped under their Topics, other Tracks left out", () => {
    const topics = trackTopics(QUIZZES, "ACL Deep Dive");
    expect(topics.map((topic) => topic.title)).toEqual(["ACL fundamentals", "ACL configuration"]);
    expect(topics.flatMap((topic) => topic.quizzes.map((item) => item.topicId))).toEqual(["T1", "T2", "T3"]);
  });

  test("the clicked Quiz is preselected; without one, every Quiz of the Topic", () => {
    expect([...defaultSelection(QUIZZES, "acl-deep-dive-acl-fundamentals", "T2")]).toEqual(["T2"]);
    expect([...defaultSelection(QUIZZES, "acl-deep-dive-acl-fundamentals", null)]).toEqual(["T1", "T2"]);
  });
});

describe("availability", () => {
  test("counts per difficulty add up only the chosen Quizzes; Mixed is their total", () => {
    expect(availableByDifficulty(QUIZZES, new Set(["T1", "T2"]))).toEqual({ mixed: 11, easy: 5, medium: 5, hard: 1 });
    expect(availableByDifficulty(QUIZZES, new Set())).toEqual({ mixed: 0, easy: 0, medium: 0, hard: 0 });
  });

  test("the custom count is clamped to 1..min(30, available), with a message saying why", () => {
    expect(clampCount("12", 40, 30)).toEqual({ value: 12, message: null });
    expect(clampCount("45", 40, 30)).toEqual({ value: 30, message: "A test has at most 30 questions." });
    expect(clampCount("9", 7, 30)).toEqual({ value: 7, message: "Only 7 questions are available for this choice." });
    expect(clampCount("0", 7, 30)).toEqual({ value: 1, message: "Enter a number from 1 to 7." });
    expect(clampCount("abc", 7, 30)).toEqual({ value: 1, message: "Enter a number from 1 to 7." });
    expect(clampCount("3", 0, 30)).toEqual({ value: 0, message: "No questions are available for this choice." });
  });

  test("the default count is 10 when it fits, else the largest pill that fits, else what is available", () => {
    expect(defaultCount([5, 10, 15], 40, 30)).toBe(10);
    expect(defaultCount([5, 10, 15], 8, 30)).toBe(5);
    expect(defaultCount([5, 10, 15], 3, 30)).toBe(3);
  });
});

describe("duration", () => {
  test("seconds per question × count × multiplier, rounded up to minutes, capped", () => {
    expect(durationMinutes(10, DURATIONS, 1)).toBe(10);
    expect(durationMinutes(7, DURATIONS, 1.5)).toBe(11); // 10.5 → 11
    expect(durationMinutes(30, DURATIONS, 2)).toBe(60);
    expect(durationMinutes(30, { ...DURATIONS, maxMinutes: 45 }, 2)).toBe(45);
    expect(durationMinutes(1, { ...DURATIONS, secondsPerQuestion: 20 }, 1)).toBe(1);
  });

  test("options keep the multipliers' order and drop ones the cap makes equal", () => {
    expect(durationOptions(10, DURATIONS)).toEqual([
      { multiplier: 1, minutes: 10 },
      { multiplier: 1.5, minutes: 15 },
      { multiplier: 2, minutes: 20 },
    ]);
    expect(durationOptions(30, { ...DURATIONS, maxMinutes: 30 })).toEqual([{ multiplier: 1, minutes: 30 }]);
  });

  test("the scoring rule is stated as the bank scores", () => {
    expect(SCORING).toBe("1 mark each · no negative marking");
  });
});

describe("addresses", () => {
  test("a quiz card opens the setup with its month, Topic and Quiz; a topic test leaves the Quiz out", () => {
    expect(setupHref("networking-ai", 2, "acl-deep-dive-acl-fundamentals", "CCNA-D1-T01")).toBe(
      "/programs/networking-ai/quizzes/setup?month=2&topic=acl-deep-dive-acl-fundamentals&quiz=CCNA-D1-T01",
    );
    expect(setupHref("networking-ai", 2, "acl-deep-dive-acl-fundamentals")).toBe(
      "/programs/networking-ai/quizzes/setup?month=2&topic=acl-deep-dive-acl-fundamentals",
    );
  });

  test("back links return to the Topic page and to Quizzes & Games", () => {
    expect(topicHref("networking-ai", 2, "a-b")).toBe("/programs/networking-ai/quizzes?month=2&topic=a-b");
    expect(quizzesHref("networking-ai", 2)).toBe("/programs/networking-ai/quizzes?month=2");
  });
});
