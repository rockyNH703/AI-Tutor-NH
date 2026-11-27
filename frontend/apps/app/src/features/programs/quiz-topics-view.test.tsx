import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { BankTopic } from "@/lib/api/programs";
import { groupQuizzes } from "./quiz-topics";
import { TopicQuizzes } from "./quiz-topics-view";

const QUIZZES: BankTopic[] = [
  { topicId: "CCNA-D1-T01", title: "Wildcard masks in ACLs", chapter: "ACL fundamentals", book: "ACL Deep Dive" },
  { topicId: "CCNA-D1-T02", title: "ACL processing order", chapter: "ACL fundamentals", book: "ACL Deep Dive" },
];

function render() {
  const topic = groupQuizzes(QUIZZES)[0]?.topics[0];
  if (!topic) throw new Error("no topic");
  return renderToStaticMarkup(<TopicQuizzes slug="networking-ai" month={2} topic={topic} states={new Map()} known={false} onBack={() => {}} />);
}

describe("a Topic's quizzes", () => {
  test("each quiz card opens the test setup with its month, Topic and Quiz, never the chat", () => {
    const html = render();
    expect(html).toContain('href="/programs/networking-ai/quizzes/setup?month=2&amp;topic=acl-deep-dive-acl-fundamentals&amp;quiz=CCNA-D1-T01"');
    expect(html).toContain('href="/programs/networking-ai/quizzes/setup?month=2&amp;topic=acl-deep-dive-acl-fundamentals&amp;quiz=CCNA-D1-T02"');
    expect(html).not.toContain("/month/2");
    expect(html).toContain('aria-label="Quiz me on Wildcard masks in ACLs"');
  });

  test("Take a topic test covers every quiz of the Topic (no quiz in the address)", () => {
    const html = render();
    expect(html).toContain("Take a topic test");
    expect(html).toContain('href="/programs/networking-ai/quizzes/setup?month=2&amp;topic=acl-deep-dive-acl-fundamentals"');
  });

  test("the hierarchy's names: the Topic is the heading, its items are quizzes", () => {
    const html = render();
    expect(html).toContain("ACL fundamentals");
    expect(html).toContain("2 quizzes");
    expect(html).toContain('aria-label="ACL fundamentals quizzes"');
  });
});
