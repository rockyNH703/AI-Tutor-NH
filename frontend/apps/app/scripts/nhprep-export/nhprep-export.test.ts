import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { parseCredentials } from "./credentials";
import { carriesPassword, createPacer, describeUrl, maxInWindow, shapeOf } from "./gate";
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
