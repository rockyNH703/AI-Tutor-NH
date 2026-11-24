import { describe, expect, test } from "bun:test";

import {
  PROGRAM_AREAS,
  isExpanded,
  monthFromParam,
  parseSaved,
  placeText,
  programChatPath,
  programRoute,
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

describe("isExpanded: a program group's state in the sidebar", () => {
  test("a saved choice wins; without one, the group is open while you are inside it", () => {
    expect(isExpanded({}, "a", "a")).toBe(true);
    expect(isExpanded({}, "a", "b")).toBe(false);
    expect(isExpanded({}, "a", null)).toBe(false);
    expect(isExpanded({ a: false }, "a", "a")).toBe(false);
    expect(isExpanded({ a: true }, "a", null)).toBe(true);
  });

  test("saved state is read defensively: bad JSON or non-boolean values are ignored", () => {
    expect(parseSaved(null)).toEqual({});
    expect(parseSaved("not json")).toEqual({});
    expect(parseSaved("[1,2]")).toEqual({});
    expect(parseSaved('{"a":true,"b":"yes","c":false}')).toEqual({ a: true, c: false });
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
  test("a month tutor: its month, the way back to Course Module, suggestions that name the month's topic", () => {
    const text = placeText({ program: "networking-ai", month: 2, mode: "tutor" }, PROGRAM);
    expect(text.label).toBe("Month 2");
    expect(text.title).toBe("Advanced Routing & Switching (CCNP ENCOR)");
    expect(text.back).toEqual({ href: "/programs/networking-ai/modules", label: "Course Module" });
    expect(text.greeting).toBe("What shall we learn in Month 2?");
    expect(text.suggestions).toEqual([
      "Give me an overview of Advanced Routing & Switching (CCNP ENCOR)",
      "What are the key concepts in Advanced Routing & Switching (CCNP ENCOR)?",
    ]);
    expect(text.placeholder).toBe("Ask your Month 2 tutor…");
    expect(text.tabTitle).toBe("Month 2 tutor");
  });

  test("a mock interview goes back to Mock Interviews and starts the interview", () => {
    const text = placeText({ program: "networking-ai", month: 3, mode: "interview" }, PROGRAM);
    expect(text.label).toBe("Month 3");
    expect(text.title).toBe("Mock interview: Network Security & Firewall Technologies (PaloAlto & FortiGate)");
    expect(text.back).toEqual({ href: "/programs/networking-ai/interviews", label: "Mock Interviews" });
    expect(text.suggestions).toEqual(["Start my mock interview"]);
    expect(text.tabTitle).toBe("Month 3 mock interview");
  });

  test("Q&A is a top-level section: no way back, suggestions from the first months", () => {
    const text = placeText({ program: "networking-ai", month: null, mode: "tutor" }, PROGRAM);
    expect(text.label).toBeNull();
    expect(text.title).toBe("Program Q&A");
    expect(text.back).toBeNull();
    expect(text.suggestions).toEqual([
      "Give me an overview of Month 1: Networking Fundamentals & CCNA Core",
      "Give me an overview of Month 2: Advanced Routing & Switching (CCNP ENCOR)",
    ]);
    expect(text.tabTitle).toBe("Program Q&A");
  });

  test("before the program's titles arrive, nothing is invented: no suggestions, generic lines", () => {
    const text = placeText({ program: "networking-ai", month: 2, mode: "tutor" }, null);
    expect(text.label).toBeNull(); // the title already says "Month 2"
    expect(text.title).toBe("Month 2");
    expect(text.suggestions).toEqual([]);
    expect(text.greeting).toBe("What shall we learn in Month 2?");
    expect(placeText({ program: "networking-ai", month: null, mode: "tutor" }, null).suggestions).toEqual([]);
  });
});

test("programChatPath: where a place's chat lives", () => {
  expect(programChatPath("p", 2, "tutor")).toBe("/programs/p/month/2");
  expect(programChatPath("p", 2, "interview")).toBe("/programs/p/month/2/interview");
  expect(programChatPath("p", null, "tutor")).toBe("/programs/p/qna");
});
