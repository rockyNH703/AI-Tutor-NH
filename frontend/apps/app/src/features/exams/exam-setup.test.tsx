import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { ExamOptions } from "@/lib/api/practice-exams";
import { SetupForm } from "./exam-setup";


const OPTIONS: ExamOptions = {
  program: { slug: "networking-ai", title: "Full Stack Networking with AI" },
  month: 1,
  quizzes: [
    { topicId: "T1", title: "Wildcard masks in ACLs", chapter: "ACL fundamentals", book: "ACL Deep Dive", available: { easy: 3, medium: 4, hard: 0, total: 7 } },
    { topicId: "T2", title: "ACL processing order", chapter: "ACL fundamentals", book: "ACL Deep Dive", available: { easy: 1, medium: 1, hard: 0, total: 2 } },
    { topicId: "T3", title: "Applying ACLs to interfaces", chapter: "ACL configuration", book: "ACL Deep Dive", available: { easy: 0, medium: 0, hard: 0, total: 0 } },
    { topicId: "T4", title: "OSPF areas", chapter: "OSPF basics", book: "CCNA Core", available: { easy: 5, medium: 5, hard: 5, total: 15 } },
  ],
  lengths: [5, 10, 15, 20, 25, 30],
  maxQuestions: 30,
  durations: { secondsPerQuestion: 60, multipliers: [1, 1.5, 2], maxMinutes: 60 },
  rules: { marksPerQuestion: 1, negativeMarking: 0 },
};

const render = (quizId: string | null) =>
  renderToStaticMarkup(
    <SetupForm slug="networking-ai" month={1} topic="acl-deep-dive-acl-fundamentals" quizId={quizId} track="ACL Deep Dive" options={OPTIONS} onStale={() => {}} onStarted={() => {}} />,
  );

describe("the test setup", () => {
  test("Quizzes of the Track only, under their Topic headings; the hierarchy's names", () => {
    const html = render("T1");
    expect(html).toContain("Quizzes in this test");
    expect(html).toContain("Track: <bdi");
    expect(html).toContain("Topic: <bdi");
    expect(html).toContain("ACL configuration");
    expect(html).not.toContain("OSPF areas");
    expect(html).toContain("No questions yet");
  });

  test("the clicked Quiz is chosen; its counts drive difficulty, count and time", () => {
    const html = render("T1");
    expect(html).toMatch(/checked=""[^>]*\/><span[^>]*>Wildcard masks in ACLs/);
    expect(html).not.toMatch(/checked=""[^>]*\/><span[^>]*>ACL processing order/);
    expect(html).toContain("· 7"); // Mixed
    expect(html).toContain("· none available"); // Hard
    // 7 available: 5 is the default pill, 10 and up are disabled
    expect(html).toMatch(/checked=""[^>]*\/><span class="tabular-nums">5</);
    expect(html).toMatch(/disabled=""[^>]*\/><span class="tabular-nums">10<\/span><span class="sr-only">\(not enough questions\)/);
    expect(html).toContain("Or type a number (1 to 7)");
    expect(html).toContain("5 minutes");
    expect(html).toContain("8 minutes"); // 5 × 60 s × 1.5, rounded up
  });

  test("a topic test chooses every Quiz of the Topic", () => {
    const html = render(null);
    expect(html).toMatch(/checked=""[^>]*\/><span[^>]*>ACL processing order/);
    expect(html).toContain("Wildcard masks in ACLs, ACL processing order");
    expect(html).toContain("· 9");
  });

  test("the summary states the scoring rule", () => {
    const html = render("T1");
    expect(html).toContain("1 mark each · no negative marking");
    expect(html).toContain("Start test");
  });
});
