import { describe, expect, test } from "bun:test";

import type { ProgramClaim } from "@/lib/api/programs";
import {
  PROGRAM_AREAS,
  endedClaims,
  monthFromParam,
  placeText,
  programChatPath,
  programRoute,
  programTabs,
} from "./program-nav";

const PROGRAM = {
  title: "Full Stack Networking with AI",
  months: [
    { month: 1, title: "Networking Fundamentals & CCNA Core" },
    { month: 2, title: "Advanced Routing & Switching (CCNP ENCOR)" },
    { month: 3, title: "Network Security & Firewall Technologies (PaloAlto & FortiGate)" },
  ],
};

describe("programRoute: which program and area an address is in", () => {
  test("the landing page, each area, and the chats that belong to an area", () => {
    expect(programRoute("/programs/networking-ai")).toEqual({ slug: "networking-ai", area: null });
    for (const { path } of PROGRAM_AREAS) {
      expect(programRoute(`/programs/networking-ai/${path}`)).toEqual({ slug: "networking-ai", area: path });
    }
    expect(programRoute("/programs/networking-ai/qna/c/11111111-1111-4111-8111-111111111111")?.area).toBe("qna");
    // A month's tutor belongs to Course Module, its interview to Mock Interviews.
    expect(programRoute("/programs/networking-ai/month/2")?.area).toBe("modules");
    expect(programRoute("/programs/networking-ai/month/2/c/11111111-1111-4111-8111-111111111111")?.area).toBe("modules");
    expect(programRoute("/programs/networking-ai/month/2/interview")?.area).toBe("interviews");
    expect(programRoute("/programs/networking-ai/month/2/interview/c/x")?.area).toBe("interviews");
  });

  test("anything else is not a program address, or no known area", () => {
    expect(programRoute(null)).toBeNull();
    expect(programRoute("/")).toBeNull();
    expect(programRoute("/programs")).toBeNull();
    expect(programRoute("/programsx/a")).toBeNull();
    expect(programRoute("/programs/networking-ai/unknown")).toEqual({ slug: "networking-ai", area: null });
  });
});

describe("programTabs: the program bar", () => {
  test("Overview (the program's own page) first, then the five areas, each with its address", () => {
    expect(programTabs("networking-ai")).toEqual([
      { area: null, label: "Overview", href: "/programs/networking-ai" },
      { area: "modules", label: "Course Module", href: "/programs/networking-ai/modules" },
      { area: "qna", label: "QnA", href: "/programs/networking-ai/qna" },
      { area: "quizzes", label: "Quizzes & Games", href: "/programs/networking-ai/quizzes" },
      { area: "interviews", label: "Mock Interviews", href: "/programs/networking-ai/interviews" },
      { area: "videos", label: "Videos & Links", href: "/programs/networking-ai/videos" },
    ]);
  });

  test("every tab's area is what programRoute reads from its address, so exactly one is current", () => {
    for (const tab of programTabs("p")) expect(programRoute(tab.href)?.area).toBe(tab.area);
  });
});

describe("endedClaims: the claims My Programs shows as Access ended", () => {
  const claim = (slug: string, state: ProgramClaim["state"], claimedAt: string): ProgramClaim => ({
    program: { slug, title: slug.toUpperCase() },
    state,
    claimedAt,
  });

  test("expired and revoked claims, newest first, once per program", () => {
    const claims = [
      claim("b", "revoked", "2026-11-03T00:00:00Z"),
      claim("a", "expired", "2026-11-02T00:00:00Z"),
      claim("b", "expired", "2026-11-01T00:00:00Z"),
    ];
    expect(endedClaims(claims, []).map((c) => [c.program.slug, c.state])).toEqual([
      ["b", "revoked"],
      ["a", "expired"],
    ]);
  });

  test("an active claim is not ended, and a program another invoice still opens is not shown as ended", () => {
    const claims = [claim("a", "active", "2026-11-03T00:00:00Z"), claim("a", "expired", "2026-11-01T00:00:00Z")];
    expect(endedClaims(claims, ["a"])).toEqual([]);
    expect(endedClaims([claim("c", "revoked", "2026-11-01T00:00:00Z")], ["a"]).map((c) => c.program.slug)).toEqual(["c"]);
    expect(endedClaims([], [])).toEqual([]);
  });
});

describe("monthFromParam: the month in ?month=", () => {
  const months = [1, 2, 3, 4, 5];
  test("a month the program has is used; anything else falls back to its first month", () => {
    expect(monthFromParam("3", months)).toBe(3);
    expect(monthFromParam("8", months)).toBe(1); // a 5-month program has no Month 8
    expect(monthFromParam("99", months)).toBe(1);
    expect(monthFromParam("abc", months)).toBe(1);
    expect(monthFromParam("", months)).toBe(1);
    expect(monthFromParam(undefined, months)).toBe(1);
    expect(monthFromParam(["2", "3"], months)).toBe(1);
    expect(monthFromParam("2", [])).toBe(1);
  });
});

describe("placeText: what a program chat says about its place", () => {
  test("a month tutor: its month, the way back to Course Module, no suggested questions", () => {
    const text = placeText({ program: "networking-ai", month: 2, mode: "tutor" }, PROGRAM);
    expect(text.label).toBe("Month 2");
    expect(text.title).toBe("Advanced Routing & Switching (CCNP ENCOR)");
    expect(text.back).toEqual({ href: "/programs/networking-ai/modules", label: "Course Module" });
    expect(text.greeting).toBe("What shall we learn in Month 2?");
    expect("suggestions" in text).toBe(false); // the greeting only (owner, 2026-11-26)
    expect(text.placeholder).toBe("Ask your Month 2 tutor…");
    expect(text.tabTitle).toBe("Month 2 tutor");
  });

  test("a mock interview goes back to Mock Interviews", () => {
    const text = placeText({ program: "networking-ai", month: 3, mode: "interview" }, PROGRAM);
    expect(text.label).toBe("Month 3");
    expect(text.title).toBe("Mock interview: Network Security & Firewall Technologies (PaloAlto & FortiGate)");
    expect(text.back).toEqual({ href: "/programs/networking-ai/interviews", label: "Mock Interviews" });
    expect("suggestions" in text).toBe(false);
    expect(text.tabTitle).toBe("Month 3 mock interview");
  });

  test("Q&A is a top-level section: no way back, no suggested questions", () => {
    const text = placeText({ program: "networking-ai", month: null, mode: "tutor" }, PROGRAM);
    expect(text.label).toBeNull();
    expect(text.title).toBe("Program Q&A");
    expect(text.back).toBeNull();
    expect("suggestions" in text).toBe(false);
    expect(text.tabTitle).toBe("Program Q&A");
  });

  test("before the program's titles arrive, nothing is invented: generic lines", () => {
    const text = placeText({ program: "networking-ai", month: 2, mode: "tutor" }, null);
    expect(text.label).toBeNull(); // the title already says "Month 2"
    expect(text.title).toBe("Month 2");
    expect(text.greeting).toBe("What shall we learn in Month 2?");
    expect(placeText({ program: "networking-ai", month: null, mode: "tutor" }, null).greeting).toBe(
      "Ask anything about your program.",
    );
  });
});

test("programChatPath: where a place's chat lives", () => {
  expect(programChatPath("p", 2, "tutor")).toBe("/programs/p/month/2");
  expect(programChatPath("p", 2, "interview")).toBe("/programs/p/month/2/interview");
  expect(programChatPath("p", null, "tutor")).toBe("/programs/p/qna");
});
