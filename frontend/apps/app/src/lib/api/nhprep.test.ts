import { afterEach, describe, expect, test } from "bun:test";

import { cachedRead, clearNhprepCache, peekRead } from "./nhprep";

afterEach(clearNhprepCache);

describe("the per-tab NHprep cache", () => {
  test("shares a read in flight and keeps its result for the page's life", async () => {
    let calls = 0;
    const load = async () => {
      calls += 1;
      return { n: calls };
    };
    const [a, b] = await Promise.all([cachedRead("lesson:x", load), cachedRead("lesson:x", load)]);
    expect(a).toBe(b);
    expect(await cachedRead("lesson:x", load)).toEqual({ n: 1 });
    expect(calls).toBe(1);
    expect(peekRead<{ n: number }>("lesson:x")).toEqual({ n: 1 });
  });

  test("peeks nothing while a read is still in flight", () => {
    void cachedRead("lesson:slow", () => new Promise(() => {}));
    expect(peekRead("lesson:slow")).toBeUndefined();
  });

  test("forgets a failed read, so Try again asks again", async () => {
    let calls = 0;
    const flaky = async () => {
      calls += 1;
      if (calls === 1) throw new Error("down");
      return "ok";
    };
    await expect(cachedRead("items:x:note", flaky)).rejects.toThrow("down");
    expect(peekRead("items:x:note")).toBeUndefined();
    expect(await cachedRead("items:x:note", flaky)).toBe("ok");
    expect(calls).toBe(2);
  });
});
