import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { ExamResult, PracticeExam } from "@/lib/api/practice-exams";
import { ExamResults } from "./exam-results";

const OPTIONS = [
  { key: "A", text: "0.0.0.255" },
  { key: "B", text: "255.255.255.0" },
  { key: "C", text: "0.0.255.255" },
];

const RESULT: ExamResult = {
  score: 1,
  total: 3,
  percentage: 33,
  correct: 1,
  incorrect: 1,
  unanswered: 1,
  autoSubmitted: true,
  submittedAt: "2026-11-27T10:10:00Z",
  byTopic: [
    { topic: "ACL fundamentals", correct: 1, total: 2 },
    { topic: "ACL configuration", correct: 0, total: 1 },
  ],
  review: [
    { index: 0, keys: ["A"], correctKeys: ["A"], explanation: "A wildcard mask inverts the subnet mask.", outcome: "correct" },
    { index: 1, keys: ["B"], correctKeys: ["C"], explanation: "Match the first two octets.", outcome: "incorrect" },
    { index: 2, keys: [], correctKeys: ["A", "C"], explanation: "Both match.", outcome: "unanswered" },
  ],
};

const EXAM = {
  examId: "33333333-3333-4333-8333-333333333333",
  status: "submitted",
  program: { slug: "networking-ai", title: "Full Stack Networking with AI" },
  month: 1,
  title: "ACL fundamentals",
  difficulty: "mixed",
  length: 3,
  minutes: 3,
  startedAt: "2026-11-27T10:00:00Z",
  endsAt: "2026-11-27T10:03:00Z",
  cutoffAt: "2026-11-27T10:03:02Z",
  serverTime: "2026-11-27T10:04:00Z",
  rev: 4,
  rules: { marksPerQuestion: 1, negativeMarking: 0 },
  questions: [0, 1, 2].map((index) => ({
    index,
    stem: `Stem ${index + 1}`,
    options: OPTIONS,
    multi: index === 2,
    quiz: "Wildcard masks in ACLs",
    topic: "ACL fundamentals",
  })),
  responses: [],
  result: RESULT,
} satisfies PracticeExam & { result: ExamResult };

const render = (notCounted: number[] = [], topicHref: string | null = "/programs/networking-ai/quizzes?month=1&topic=t") =>
  renderToStaticMarkup(<ExamResults exam={EXAM} notCounted={notCounted} topicHref={topicHref} quizzesHref="/programs/networking-ai/quizzes?month=1" />);

describe("ExamResults", () => {
  test("the score, percentage and counts are the server's", () => {
    const html = render();
    expect(html).toContain("1 / 3");
    expect(html).toContain("33%");
    expect(html).toMatch(/<dt[^>]*>Correct<\/dt><dd[^>]*>1<\/dd>/);
    expect(html).toMatch(/<dt[^>]*>Unanswered<\/dt><dd[^>]*>1<\/dd>/);
    expect(html).toContain("Submitted automatically when time ran out.");
  });

  test("a table per Topic (the UI's name for a chapter), with a header per row", () => {
    const html = render();
    expect(html).toContain(">By topic<");
    expect(html).toContain('<th scope="row" class="py-2 pr-3 font-medium text-foreground"><bdi dir="auto">ACL fundamentals</bdi></th>');
    expect(html).toContain("50%");
  });

  test("the review shows your answer, the correct one, the explanation and the outcome", () => {
    const html = render();
    expect(html).toContain("Question 2 of 3");
    expect(html).toContain("Incorrect");
    expect(html).toContain("Match the first two octets.");
    expect(html).toContain("Your answer");
    expect(html).toContain("Correct answer");
    expect(html).toContain("No answer");
    expect(html).toContain("A, C");
    expect(html).toContain("Quiz: <bdi");
    expect(html).not.toContain("Topic: ");
  });

  test("answers that never reached the server are named Not counted, never saved", () => {
    const html = render([2]);
    expect(html).toContain("1 answer was not counted");
    expect(html).toContain("Question 3: Not counted (not saved before time ran out).");
    expect(html.match(/Not counted \(not saved before time ran out\)/g)?.length).toBe(2);
    expect(render()).not.toContain("Not counted");
  });

  test("a void question (changed after the start) says Not graded, shows no key, and keeps the paper's numbering", () => {
    const review = [
      ...RESULT.review.slice(0, 2),
      { index: 2, keys: ["A"], correctKeys: [], explanation: "", outcome: "void" as const },
    ];
    const exam = { ...EXAM, result: { ...RESULT, total: 2, unanswered: 0, review } };
    const html = renderToStaticMarkup(<ExamResults exam={exam} notCounted={[]} topicHref={null} quizzesHref="/q" />);
    expect(html).toContain("1 / 2");
    expect(html).toContain("Question 3 of 3");
    expect(html).toContain("Not graded");
    expect(html).toContain("isn&#x27;t graded");
    expect(html.match(/Correct answer<\/dt>/g)?.length).toBe(2); // only the graded questions list a key
  });

  test("Back to topic and Back to Quizzes &amp; Games; no topic link without a topic", () => {
    const html = render();
    expect(html).toContain('href="/programs/networking-ai/quizzes?month=1&amp;topic=t"');
    expect(html).toContain("Back to Quizzes &amp; Games");
    expect(render([], null)).not.toContain("Back to topic");
  });
});
