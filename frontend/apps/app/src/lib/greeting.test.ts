import { describe, expect, test } from "bun:test";

import { GREETINGS, greetingPicker } from "./greeting";

describe("greetingPicker", () => {
  test("random across the pool, never the previous chat's greeting twice running", () => {
    let seed = 7;
    const random = () => {
      seed = (seed * 16807) % 2147483647;
      return (seed - 1) / 2147483646;
    };
    const pick = greetingPicker(GREETINGS, random);
    const picks = Array.from({ length: 300 }, (_, n) => pick(`chat-${n}`));
    for (let n = 1; n < picks.length; n++) expect(picks[n]).not.toBe(picks[n - 1]);
    expect(new Set(picks)).toEqual(new Set(GREETINGS));
  });

  test("the same chat keeps its greeting", () => {
    const pick = greetingPicker(GREETINGS, () => 0.99);
    const first = pick("draft-1");
    expect(pick("draft-2")).not.toBe(first);
    expect(pick("draft-1")).toBe(first);
  });

  test("one greeting, or none, never fails", () => {
    const one = greetingPicker(["Only one."], () => 0.5);
    expect(one("a")).toBe("Only one.");
    expect(one("b")).toBe("Only one.");
    expect(greetingPicker([], () => 0.5)("c")).toBe(GREETINGS[0]);
    expect(greetingPicker(GREETINGS, () => 1)("d")).toBe(GREETINGS.at(-1) as string); // random() at its top edge stays in range
  });
});
