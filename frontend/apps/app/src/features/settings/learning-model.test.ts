import { describe, expect, test } from "bun:test";

import { CONTRACT } from "@/lib/api/contract.gen";
import { FOCUS_AREAS_MAX, MAIN_GOALS, PLATFORMS_MAX } from "@/lib/api/types";
import { ASSISTANT_FIELDS, LEARNING_FIELDS, MAIN_GOAL_LABELS, PREFERENCES, TUTOR_FIELDS, reachesAssistant } from "./learning-labels";
import {
  addSubject,
  addTopic,
  canToggle,
  chosenText,
  editFor,
  engineeringPreview,
  moveSubject,
  removeSubject,
  toggleCode,
  toggleSubject,
  wholeNumber,
} from "./learning-model";

type Values = { grade: string; board: string; boardState: string; weakTopics: string[]; targetScorePercent: number };
type Loose = { [K in keyof Values]?: Values[K] | null | "" };

describe("editFor", () => {
  const fields = ["grade", "board", "boardState", "weakTopics", "targetScorePercent"] as const;
  const edit = (base: Loose, draft: Loose) => editFor<Loose>(base, draft, fields);

  test("only changed fields; a value is set, an emptied field is reset", () => {
    const base = { grade: "class_9", board: "state", boardState: "kerala", weakTopics: ["Optics"], targetScorePercent: 80 };
    const draft = { grade: "class_10", board: "cbse", boardState: "", weakTopics: [], targetScorePercent: 80 };
    expect(edit(base, draft)).toEqual({
      set: { grade: "class_10", board: "cbse" },
      reset: ["boardState", "weakTopics"],
    });
  });

  test("null, empty text and an empty list are all the same 'unset'", () => {
    expect(edit({ grade: null, weakTopics: [] }, { grade: "", weakTopics: [] })).toEqual({ set: {}, reset: [] });
  });

  test("zero is a value, not unset", () => {
    expect(edit({ targetScorePercent: null }, { targetScorePercent: 0 })).toEqual({
      set: { targetScorePercent: 0 },
      reset: [],
    });
  });
});

describe("weak topics", () => {
  const limits = { max: 3, maxLength: 10 };

  test("added clean, once, within the limits", () => {
    expect(addTopic([], "  Trig \n onometry ", { ...limits, maxLength: 60 })).toEqual({
      topics: ["Trig onometry"],
      error: null,
    });
    expect(addTopic(["Optics"], "optics", limits).error).toBe("That topic is already on the list.");
    expect(addTopic(["a", "b", "c"], "d", limits).error).toBe("You can list up to 3 topics.");
    expect(addTopic([], "x".repeat(11), limits).error).toBe("Keep a topic under 11 characters.");
    expect(addTopic(["a"], "   ", limits)).toEqual({ topics: ["a"], error: null });
  });
});

describe("the subject list", () => {
  const items = [
    { code: "mathematics", isPriority: false, needsImprovement: false },
    { code: "physics", isPriority: false, needsImprovement: false },
    { code: "chemistry", isPriority: false, needsImprovement: false },
  ];
  const codes = (list: typeof items) => list.map((item) => item.code);

  test("moves one place, and not past either end", () => {
    expect(codes(moveSubject(items, 2, -1))).toEqual(["mathematics", "chemistry", "physics"]);
    expect(codes(moveSubject(items, 0, -1))).toEqual(codes(items));
    expect(codes(moveSubject(items, 2, 1))).toEqual(codes(items));
  });

  test("adds once and within the limit; removes; flags one subject only", () => {
    expect(codes(addSubject(items, "biology", 15))).toEqual([...codes(items), "biology"]);
    expect(addSubject(items, "physics", 15)).toHaveLength(3);
    expect(addSubject(items, "biology", 3)).toHaveLength(3);
    expect(codes(removeSubject(items, "physics"))).toEqual(["mathematics", "chemistry"]);
    const flagged = toggleSubject(items, "physics", "isPriority");
    expect(flagged.map((item) => item.isPriority)).toEqual([false, true, false]);
  });
});

test("every preference is on exactly one page, and labelled for every value the API has", () => {
  const all = [...LEARNING_FIELDS, ...TUTOR_FIELDS];
  expect(new Set(all).size).toBe(all.length);
  expect(new Set<string>(all)).toEqual(new Set(Object.keys(CONTRACT.LearningPreferencesSet)));
  for (const field of all) {
    expect(Object.keys(PREFERENCES[field].options).sort()).toEqual([...CONTRACT.LearningPreferencesSet[field].enum].sort());
  }
});

test("the six preferences that reach the assistant, three on each page", () => {
  expect([...ASSISTANT_FIELDS].sort() as string[]).toEqual(
    ["answerMode", "explanationLevel", "language", "responseLength", "stepByStep", "visualExplanations"].sort(),
  );
  expect(LEARNING_FIELDS.filter(reachesAssistant)).toEqual(["language", "explanationLevel", "visualExplanations"]);
  expect(TUTOR_FIELDS.filter(reachesAssistant)).toEqual(["responseLength", "answerMode", "stepByStep"]);
});

test("every main goal the API has is labelled, for engineers, with its code unchanged", () => {
  expect(Object.keys(MAIN_GOAL_LABELS).sort()).toEqual([...MAIN_GOALS].sort());
  expect(MAIN_GOAL_LABELS.board_exams).toBe("Pass a certification exam");
  expect(MAIN_GOAL_LABELS.competitive_exam).toBe("Prepare for an advanced certification (CCNP, CCIE, OSCP)");
  expect(MAIN_GOAL_LABELS.homework_help).toBe("Get help with lab and work tasks");
  expect(new Set(Object.values(MAIN_GOAL_LABELS)).size).toBe(MAIN_GOALS.length);
});

describe("the Engineering Profile's multiple choices", () => {
  test("the limits are the contract's", () => {
    expect([FOCUS_AREAS_MAX, PLATFORMS_MAX]).toEqual([8, 12]);
  });

  test("a code is checked once, at the end, only while there is room; unchecking always works", () => {
    expect(toggleCode([], "vpn", 2)).toEqual(["vpn"]);
    expect(toggleCode(["vpn"], "linux", 2)).toEqual(["vpn", "linux"]);
    expect(toggleCode(["vpn", "linux"], "cloud", 2)).toEqual(["vpn", "linux"]);
    expect(toggleCode(["vpn", "linux"], "vpn", 2)).toEqual(["linux"]);
    expect(toggleCode(["vpn"], "", 2)).toEqual(["vpn"]);
    // A list over the limit (it cannot be saved that way, but never grows) can still be emptied.
    expect(toggleCode(["a", "b", "c"], "d", 2)).toEqual(["a", "b", "c"]);
    expect(toggleCode(["a", "b", "c"], "b", 2)).toEqual(["a", "c"]);
  });

  test("a full list disables only what is unchecked", () => {
    expect(canToggle(["a", "b"], "a", 2)).toBe(true);
    expect(canToggle(["a", "b"], "c", 2)).toBe(false);
    expect(canToggle(["a"], "c", 2)).toBe(true);
  });

  test("how full it is, in words", () => {
    expect(chosenText(0, 8)).toBe("0 of 8 chosen");
    expect(chosenText(7, 8)).toBe("7 of 8 chosen");
    expect(chosenText(8, 8)).toBe("8 of 8 chosen, the most you can choose");
  });

  test("the preview gets every field, unset ones cleared", () => {
    expect(engineeringPreview({ role: "soc_analyst", focusAreas: ["soc_siem"], platforms: null, certificationGoal: "" })).toEqual({
      role: "soc_analyst",
      experienceLevel: null,
      certificationGoal: null,
      focusAreas: ["soc_siem"],
      platforms: [],
    });
  });
});

test("a whole number within its limits, or nothing", () => {
  expect(wholeNumber("45", 600)).toBe(45);
  expect(wholeNumber("44.6", 600)).toBe(45);
  expect(wholeNumber("-5", 600)).toBe(0);
  expect(wholeNumber("9000", 600)).toBe(600);
  expect(wholeNumber("  ", 600)).toBeNull();
  expect(wholeNumber("abc", 600)).toBeNull();
});
