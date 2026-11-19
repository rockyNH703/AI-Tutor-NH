import { describe, expect, test } from "bun:test";

import { DRIVERS, KINDS } from "./drivers";
import { blockOf, fixture } from "./support";
import { COPY, at } from "./ui";

/** The learning specs' own logic, without a browser: fixtures read as the specs read them, and what the drivers assume. */

describe("fixtures", () => {
  test("every kind fixture becomes the same questions as a mock exam, by one word", () => {
    for (const kind of KINDS) {
      const practice = fixture(`kind-${kind}`);
      const exam = fixture(`kind-${kind}`, "exam");
      expect(exam.message).toContain('"mode":"exam"');
      expect(exam.message.length).toBe(practice.message.length - "practice".length + "exam".length);
      expect(exam.refusal).toBeNull();
    }
    expect(() => fixture("v1-quiz", "exam")).toThrow(/no practice mode/);
  });

  test("blockOf reads the practice block of a reply, and refuses one without", () => {
    expect(blockOf("exam").mode).toBe("exam");
    expect(blockOf("v1-mcq").options).toHaveLength(4);
    expect(blockOf("quiz-30").questions).toHaveLength(30);
    expect(() => blockOf("flashcards")).toThrow(/no practice block/);
  });

  test("what the drivers assume about their fixtures holds", () => {
    const lines = blockOf("kind-lines").questions[0];
    expect(lines.answer).toEqual([3, 5]); // drivers pick options 2 and 4 (0-based)
    expect(lines.listing.content.split("\n").length).toBeGreaterThanOrEqual(5);
    const order = blockOf("kind-order").questions[0].items as string[];
    expect([...order].reverse()).not.toEqual(order);
    expect(blockOf("kind-match").questions[0].extra.length).toBeGreaterThan(0);
    expect(blockOf("kind-multi").questions[0].answer).toEqual(["A", "B", "C"]);
    expect(blockOf("kind-numeric").questions[0].answer).toBe(-67);
    expect(blockOf("kind-cli").questions[0].prompt).toBe("R1#");
    expect(blockOf("kind-diagram").questions[0].answer).toEqual(["SW1", "FW1", "R1"]);
    for (const kind of KINDS) {
      const driver = DRIVERS[kind];
      expect(driver.fixture).toBe(`kind-${kind}`);
      expect(driver.explanation.length).toBeGreaterThan(20);
      expect(blockOf(driver.fixture).questions[1].options[0]).toBe("ARP"); // every kind's second question
    }
    const exam = blockOf("exam").questions;
    expect(exam[3].answers[0].length).toBeGreaterThanOrEqual(280); // the 300-character command
    expect(Math.max(...exam[0].options.map((option: string) => option.length))).toBeGreaterThanOrEqual(390);
  });
});

describe("ui", () => {
  test("the plan's copy", () => {
    expect(COPY.progress(4, 5, 7, 9)).toBe("Question 4 of 5 · 7 of 9 points so far");
    expect(COPY.triesLeft(2)).toBe("Not quite: 2 tries left.");
    expect(COPY.triesLeft(1)).toBe("Not quite: 1 try left.");
    expect(COPY.score.test("You scored 7 of 12 points: 3 of 5 questions fully right, 1 partly.")).toBe(true);
  });

  test("widths: desktop Chrome at 1440 and 834, Pixel 7 (touch) at phone widths, never a browser switch", () => {
    expect(at(1440)).toMatchObject({ viewport: { width: 1440 }, isMobile: false, hasTouch: false });
    expect(at(834)).toMatchObject({ viewport: { width: 834 }, isMobile: false });
    const phone = at(320);
    expect(phone).toMatchObject({ viewport: { width: 320 }, isMobile: true, hasTouch: true });
    expect("defaultBrowserType" in phone).toBe(false);
  });
});
