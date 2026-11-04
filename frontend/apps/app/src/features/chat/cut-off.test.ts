import { describe, expect, test } from "bun:test";

import { cutOffNote } from "./cut-off";

describe("cutOffNote", () => {
  test("a reply cut at the length limit or by a content filter says so", () => {
    expect(cutOffNote("length")).toContain("cut off");
    expect(cutOffNote("content_filter")).toContain("content filter");
  });

  test("a finished reply, or one without a reason, has no note", () => {
    expect(cutOffNote("stop")).toBeNull();
    expect(cutOffNote(undefined)).toBeNull();
  });
});
