import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { parseCredentials } from "./credentials";
import { carriesPassword, createPacer, describeUrl, maxInWindow, shapeOf } from "./gate";
import { categoryDone, durationSeconds, keyedQuestions, lessonRow, sourceDay, stripSourceRefs, trainerHints } from "./parse";
import { assertOutsideAnyRepo } from "./paths";

describe("assertOutsideAnyRepo", () => {
  const root = mkdtempSync(join(tmpdir(), "nhprep-paths-"));
  mkdirSync(join(root, "repo", "sub"), { recursive: true });
  writeFileSync(join(root, "repo", ".git"), "gitdir: elsewhere"); // a worktree's .git is a file
  mkdirSync(join(root, "data"));

  test("refuses a folder inside a working tree, at any depth", () => {
    expect(() => assertOutsideAnyRepo(join(root, "repo", "sub", "out"))).toThrow("outside every git working tree");
  });
  test("accepts a folder outside every repo", () => {
    expect(assertOutsideAnyRepo(join(root, "data", "run1"))).toBe(join(root, "data", "run1"));
  });
  test("refuses a relative path", () => {
    expect(() => assertOutsideAnyRepo("nhprep-data")).toThrow("absolute");
  });
});

describe("parseCredentials", () => {
  const pass = "pw-" + "Abc%123";
  test("one line with a separator, two lines, or glued", () => {
    expect(parseCredentials(`a.b@example.com ${pass}\n`)).toEqual({ email: "a.b@example.com", password: pass });
    expect(parseCredentials(`a.b@example.com:${pass}`)).toEqual({ email: "a.b@example.com", password: pass });
    expect(parseCredentials(`a.b@example.com\r\n${pass}\r\n`)).toEqual({ email: "a.b@example.com", password: pass });
    expect(parseCredentials(`a.b@example.com${pass}`)).toEqual({ email: "a.b@example.com", password: pass });
  });
  test("an unknown shape fails without echoing the text", () => {
    expect(() => parseCredentials(`no-address ${pass}`)).toThrow(/^(?!.*Abc).*not in a known shape/);
  });
});

describe("the pacing gate", () => {
  test("turns are at least the gap apart, in call order, however they were requested", async () => {
    let now = 0;
    const clock = {
      now: () => now,
      sleep: async (ms: number) => {
        now += ms;
      },
    };
    const turn = createPacer(1200, clock);
    const times: number[] = await Promise.all([turn(), turn(), turn()]);
    now += 5000; // a quiet spell: the next turn needs no wait
    times.push(await turn());
    expect(times).toEqual([0, 1200, 2400, 7400]);
  });
  test("maxInWindow counts the busiest second", () => {
    expect(maxInWindow([0, 100, 900, 1000, 1500, 3000])).toBe(3);
    expect(maxInWindow([])).toBe(0);
  });
});

describe("export v1 parsing", () => {
  test("durations, lesson rows and the slug's day", () => {
    expect([durationSeconds("1h 12m"), durationSeconds("34m 22s"), durationSeconds("45s"), durationSeconds("video"), durationSeconds("")]).toEqual([4320, 2062, 45, null, null]);
    expect(lessonRow("day-01-intromp4", ["2", "CCNP Routing Intro: Metrics, AD", "video", "1h 12m", "Free Preview"])).toEqual({
      slug: "day-01-intromp4",
      position: 2,
      title: "CCNP Routing Intro: Metrics, AD",
      type: "video",
      durationSeconds: 4320,
    });
    expect([sourceDay("day-07-eigrp-basicsmp4"), sourceDay("3mp4"), sourceDay("8file-directorymp4")]).toEqual([7, null, null]);
  });
  test("trainer hints stay raw", () => {
    expect(trainerHints("ccna-by-mrajay", "CCNA Complete Course", "Learn 8 CCNA BY Mr.AJAY through lessons")).toEqual({ titleParens: null, aboutBy: "Mr.AJAY", slugToken: "mrajay" });
    expect(trainerHints("tamil-ccna", "CCNA Course (Tamil)", "")).toEqual({ titleParens: "Tamil", aboutBy: null, slugToken: "ccna" });
  });
  test("study aids lose sourceRefs and nothing else", () => {
    expect(stripSourceRefs({ notes: { items: [{ id: "n1", markdown: "x", sourceRefs: [{ id: "s", start: 1 }] }], reason: null } })).toEqual({ notes: { items: [{ id: "n1", markdown: "x" }], reason: null } });
  });
  test("keyed questions: the index of the key, and unkeyed ones dropped", () => {
    const body = {
      questions: [
        { id: "q1", question: "Class B mask?", options: ["255.255.255.0", "255.255.0.0"], correctOption: "255.255.0.0", explanation: "16 bits" },
        { id: "q2", question: "no key", options: ["a", "b"], correctOption: "" },
        { id: "q3", question: "key not an option", options: ["a", "b"], correctOption: "c" },
      ],
    };
    const keyed = keyedQuestions("ccna", "CCNA", body);
    expect(keyed.map((q) => [q.id, q.correctIndex])).toEqual([
      ["q1", 1],
      ["q3", null],
    ]);
    expect(keyed[0]).toMatchObject({ type: "quiz_question", schema: 1, format: "single", difficulty: null, lessonExternalId: null, explanation: "16 bits" });
    expect(keyedQuestions("ccna", "CCNA", null)).toEqual([]);
  });
  test("a category stops when all reported are seen, or after 3 attempts with nothing new", () => {
    expect([categoryDone(40, 40, 0), categoryDone(39, 40, 2), categoryDone(39, 40, 3), categoryDone(0, null, 0)]).toEqual([true, false, true, false]);
  });
});

test("a password in a query string is caught, whatever the parameter is called", () => {
  expect(carriesPassword(new URL("https://nhprep.com/login?email=a%40b.c&password=x"))).toBe(true);
  expect(carriesPassword(new URL("https://nhprep.com/login?Passwd=x"))).toBe(true);
  expect(carriesPassword(new URL("https://nhprep.com/login?pwd=x"))).toBe(true);
  expect(carriesPassword(new URL("https://nhprep.com/login?callbackUrl=%2Fdashboard"))).toBe(false);
  expect(carriesPassword(new URL("https://accounts.google.com/x?passive=1209600"))).toBe(false);
});

test("shapes and URLs keep names and types, never values", () => {
  expect(shapeOf({ id: 7, title: "BGP", tags: ["a", "b"], video: null, nested: { ok: true } })).toEqual({
    id: "number",
    title: "string",
    tags: ["len 2", "string"],
    video: "null",
    nested: { ok: "boolean" },
  });
  const url = describeUrl("https://player.mediadelivery.net/embed/1/abc?token=" + "deadbeef&expires=1&t=5");
  expect(url).toEqual({ host: "player.mediadelivery.net", path: "/embed/1/abc", params: ["token", "expires", "t"] });
  expect(JSON.stringify(url)).not.toContain("deadbeef");
});
