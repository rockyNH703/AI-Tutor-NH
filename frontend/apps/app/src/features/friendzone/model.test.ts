import { describe, expect, test } from "bun:test";

import { ApiError, describeError } from "@/lib/api/errors";
import type { FriendZoneMe, FriendZoneProfile } from "./api";
import {
  type MeLoad,
  changedFields,
  cleanName,
  failureCopy,
  fieldsOf,
  foundAction,
  hubState,
  initials,
  nameProblem,
  programsLine,
  searchForView,
  shownView,
  showsSwitch,
  viewFromParam,
} from "./model";

const PROFILE: FriendZoneProfile = {
  playerId: "11111111-1111-4111-8111-111111111111",
  displayName: "Asha K",
  discoverable: false,
  showProgram: false,
  showStats: false,
  version: 2,
  qa: false,
};
const ME: FriendZoneMe = {
  enabled: true,
  eligible: true,
  member: true,
  suspended: false,
  profile: PROFILE,
  serverTime: "2026-11-24T10:00:00Z",
};
const ready = (patch: Partial<FriendZoneMe>): MeLoad => ({ status: "ready", me: { ...ME, ...patch } });

describe("the hub's state", () => {
  test("follows /me: off, not eligible, suspended, not joined, member", () => {
    expect(hubState({ status: "loading" }).kind).toBe("loading");
    expect(hubState({ status: "error", error: new Error("x") }).kind).toBe("error");
    expect(hubState(ready({ enabled: false, eligible: false, member: false, profile: null })).kind).toBe("off");
    expect(hubState(ready({ eligible: false, member: false, profile: null })).kind).toBe("ineligible");
    expect(hubState(ready({ suspended: true })).kind).toBe("suspended");
    expect(hubState(ready({ member: false, profile: null })).kind).toBe("join");
    expect(hubState(ready({}))).toEqual({ kind: "member", profile: PROFILE });
  });

  test("a member without a profile in the answer is asked to join, never shown an empty profile", () => {
    expect(hubState(ready({ profile: null })).kind).toBe("join");
  });
});

describe("the Practice | Friend Zone switch", () => {
  test("reads only ?zone=friends (the notifications' link) as Friend Zone", () => {
    expect(viewFromParam("friends")).toBe("friend-zone");
    expect(viewFromParam("friend-zone")).toBe("practice");
    expect(viewFromParam(null)).toBe("practice");
    expect(viewFromParam("battles")).toBe("practice");
  });

  test("is hidden, and Practice shown, when Friend Zone is off for the account", () => {
    const off = ready({ enabled: false, eligible: false, member: false, profile: null });
    expect(showsSwitch(off, "friend-zone")).toBe(false);
    expect(shownView(off, "friend-zone")).toBe("practice");
  });

  test("shows once Friend Zone is on, whatever the account's state in it", () => {
    for (const load of [ready({}), ready({ eligible: false }), ready({ suspended: true }), ready({ member: false, profile: null })]) {
      expect(showsSwitch(load, "practice")).toBe(true);
      expect(shownView(load, "friend-zone")).toBe("friend-zone");
    }
  });

  test("while /me loads or fails, an address asking for Friend Zone keeps it; Practice stays without the switch", () => {
    const failed: MeLoad = { status: "error", error: new Error("x") };
    for (const load of [{ status: "loading" } as MeLoad, failed]) {
      expect(shownView(load, "friend-zone")).toBe("friend-zone");
      expect(showsSwitch(load, "friend-zone")).toBe(true);
      expect(shownView(load, "practice")).toBe("practice");
      expect(showsSwitch(load, "practice")).toBe(false);
    }
  });

  test("writes the view beside the month and removes it for Practice", () => {
    expect(searchForView("?month=3", "friend-zone")).toBe("?month=3&zone=friends");
    expect(searchForView("?month=3&zone=friends", "practice")).toBe("?month=3");
    expect(searchForView("?zone=friends", "practice")).toBe("");
    expect(searchForView("", "friend-zone")).toBe("?zone=friends");
  });
});

describe("name hints", () => {
  test("clean the name as the API does: NFKC and one space between words", () => {
    expect(cleanName("  Asha \t  K  ")).toBe("Asha K");
    expect(cleanName("ＡＳＨＡ")).toBe("ASHA"); // full-width letters, NFKC
  });

  test("accept letters, marks, digits, spaces and . _ ' - in any script", () => {
    for (const name of ["Asha K", "O'Neil-Ray", "dev_ops.99", "आशा", "José", "李雷"]) expect(nameProblem(name)).toBeNull();
  });

  test("refuse empty, too short, too long, address-like and other characters", () => {
    expect(nameProblem("   ")).toBe("Enter a name.");
    expect(nameProblem("A")).toBe("Use 2 to 30 characters.");
    expect(nameProblem("x".repeat(31))).toBe("Use 2 to 30 characters.");
    expect(nameProblem("x".repeat(30))).toBeNull();
    for (const name of ["asha@example.com", "https://x.io", "www.asha", "Asha 🙂", "Asha​", "Asha‮K"])
      expect(nameProblem(name)).toBe("Use letters, numbers, spaces and . _ ' - only.");
  });

  test("initials take the first letter of the first two words", () => {
    expect(initials("asha k rao")).toBe("AK");
    expect(initials("Ravi")).toBe("R");
    expect(initials("आशा कुमार")).toBe("आक");
  });
});

describe("profile edits", () => {
  test("send only what changed, with the name cleaned", () => {
    const before = fieldsOf(PROFILE);
    expect(changedFields(before, before)).toEqual({});
    expect(changedFields(before, { ...before, displayName: "  Asha   K " })).toEqual({});
    expect(changedFields(before, { ...before, displayName: "Asha R", showStats: true })).toEqual({
      displayName: "Asha R",
      showStats: true,
    });
  });
});

describe("failure copy", () => {
  test("a refused name belongs to the name field", () => {
    const error = new ApiError(422, "validation_error", { fields: [{ field: "displayName", reason: "invalid" }] });
    expect(failureCopy(error, "join")).toEqual({ message: "That name can't be used. Choose a different one.", onName: true, reload: false });
  });

  test("already joined, edited elsewhere and not available read /me again", () => {
    for (const code of ["already_member", "version_conflict", "not_found"]) {
      const failure = failureCopy(new ApiError(code === "not_found" ? 404 : 409, code), "save");
      expect(failure.reload).toBe(true);
      expect(failure.onName).toBe(false);
    }
    expect(failureCopy(new ApiError(409, "version_conflict"), "save").message).toContain("latest version is shown");
  });

  test("the limit names what was limited", () => {
    expect(failureCopy(new ApiError(429, "rate_limited"), "join").message).toContain("tried to join");
    expect(failureCopy(new ApiError(429, "rate_limited"), "save").message).toContain("changed your profile");
  });

  test("anything else uses the app's own copy, never the API's text", () => {
    const failure = failureCopy(new ApiError(503, "service_unavailable"), "save");
    expect(failure).toEqual({ message: describeError(new ApiError(503, "service_unavailable")).message, onName: false, reload: false });
    expect(failureCopy(new ApiError(422, "validation_error"), "save").onName).toBe(false);
  });

  test("already_member is a known code with its own copy", () => {
    expect(new ApiError(409, "already_member").code).toBe("already_member");
    expect(describeError(new ApiError(409, "already_member")).title).toBe("Already joined");
  });
});

describe("friends: what a found member offers", () => {
  test("one thing per relation, never a dead button", () => {
    expect(foundAction("none")).toEqual({ kind: "send", label: "Send friend request" });
    expect(foundAction("incoming")).toEqual({ kind: "accept", label: "Accept their request" });
    for (const relation of ["outgoing", "friend", "self", "blocked"] as const) expect(foundAction(relation).kind).toBe("note");
  });

  test("a friend's programs, or that they are hidden", () => {
    expect(programsLine(null)).toBe("Program hidden");
    expect(programsLine([{ slug: "ccna", title: "CCNA" }, { slug: "sec", title: "Security" }])).toBe("CCNA, Security");
  });

  test("every miss in search is the one no-match line, word for word", () => {
    expect(failureCopy(new ApiError(404, "no_match"), "search").message).toBe(
      "No match. Your friend needs to join Friend Zone and turn on 'Let classmates find me'.",
    );
  });

  test("stale states read the lists again; limits say what to do", () => {
    for (const code of ["already_friends", "request_pending"]) expect(failureCopy(new ApiError(409, code), "request").reload).toBe(true);
    expect(failureCopy(new ApiError(404, "not_found"), "answer")).toEqual({
      message: "That's no longer available. The lists are up to date now.",
      onName: false,
      reload: true,
    });
    expect(failureCopy(new ApiError(409, "request_limit"), "request").message).toContain("Cancel some");
    expect(failureCopy(new ApiError(409, "friend_limit"), "request").message).toContain("Remove a friend");
    expect(failureCopy(new ApiError(429, "rate_limited"), "search").message).toContain("search limit");
    expect(failureCopy(new ApiError(429, "rate_limited"), "report").message).toContain("block the member");
  });
});
