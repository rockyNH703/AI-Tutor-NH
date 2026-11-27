import { describe, expect, test } from "bun:test";

import type { BankTopic } from "@/lib/api/programs";
import type { Mastery, MasteryState } from "@/lib/api/types";
import { UNCLASSIFIED, groupQuizzes, masteryByTopic, searchQuizzes, topicKey, topicProgress } from "./quiz-topics";

/** Index with a check (the project compiles with noUncheckedIndexedAccess). */
function at<T>(items: T[], index: number): T {
  const item = items[index];
  if (item === undefined) throw new Error(`no item ${index}`);
  return item;
}

const quiz = (topicId: string, title: string, chapter: string, book = "ACL Deep Dive"): BankTopic => ({
  topicId,
  title,
  chapter,
  book,
});

const MONTH: BankTopic[] = [
  quiz("T1", "Standard vs extended ACLs", "ACL fundamentals"),
  quiz("T2", "ACL processing order", "ACL fundamentals"),
  quiz("T3", "Configuring ACL entries", "ACL configuration and application"),
  quiz("T4", "Verifying ACLs with show commands", "ACL verification and troubleshooting"),
  quiz("T5", "Wildcard masks", "ACL fundamentals"),
  quiz("T6", "OSPF areas", "OSPF basics", "CCNA Core"),
  quiz("T7", "Loose quiz", "  "),
];

const mastery = (topics: [string, MasteryState][]) =>
  ({ subjects: [{ chapters: [{ topics: topics.map(([topicId, state]) => ({ topicId, state })) }] }] }) as unknown as Mastery;

describe("groupQuizzes", () => {
  test("groups by track then domain, in the API's order, every quiz exactly once", () => {
    const tracks = groupQuizzes(MONTH);
    expect(tracks.map((track) => track.title)).toEqual(["ACL Deep Dive", "CCNA Core"]);
    expect(at(tracks, 0).topics.map((topic) => topic.title)).toEqual([
      "ACL fundamentals",
      "ACL configuration and application",
      "ACL verification and troubleshooting",
      UNCLASSIFIED,
    ]);
    expect(at(at(tracks, 0).topics, 0).quizzes.map((item) => item.topicId)).toEqual(["T1", "T2", "T5"]);
    const all = tracks.flatMap((track) => track.topics.flatMap((topic) => topic.quizzes.map((item) => item.topicId)));
    expect(all.sort()).toEqual(MONTH.map((item) => item.topicId).sort());
  });

  test("a quiz without a domain is kept, under Other topics, not dropped", () => {
    const other = at(groupQuizzes(MONTH), 0).topics.find((topic) => topic.title === UNCLASSIFIED);
    expect(other?.quizzes.map((item) => item.topicId)).toEqual(["T7"]);
  });

  test("an empty month has no topics", () => {
    expect(groupQuizzes([])).toEqual([]);
  });

  test("a large month stays grouped: 300 quizzes over 12 domains", () => {
    const many = Array.from({ length: 300 }, (_, n) => quiz(`Q${n}`, `Quiz ${n}`, `Domain ${n % 12}`));
    const topics = at(groupQuizzes(many), 0).topics;
    expect(topics).toHaveLength(12);
    expect(topics.reduce((sum, topic) => sum + topic.quizzes.length, 0)).toBe(300);
  });

  test("topic keys are stable, readable and distinct per track", () => {
    expect(topicKey("ACL Deep Dive", "ACL fundamentals")).toBe("acl-deep-dive-acl-fundamentals");
    expect(topicKey("A", "Same")).not.toBe(topicKey("B", "Same"));
    expect(topicKey("", "")).toBe("topic");
  });
});

describe("progress", () => {
  test("counts practised and mastered quizzes of a topic from the practice record only", () => {
    const topic = at(at(groupQuizzes(MONTH), 0).topics, 0);
    const states = masteryByTopic(mastery([["T1", "mastered"], ["T2", "weak"], ["T6", "mastered"]]));
    expect(topicProgress(topic, states)).toEqual({ practised: 2, mastered: 1 });
  });

  test("no record, no progress", () => {
    expect(topicProgress(at(at(groupQuizzes(MONTH), 0).topics, 0), masteryByTopic(null))).toEqual({ practised: 0, mastered: 0 });
  });
});

describe("searchQuizzes", () => {
  const tracks = groupQuizzes(MONTH);
  test("matches every word across a quiz's title, domain and track", () => {
    expect(searchQuizzes(tracks, "wildcard").map((hit) => hit.quiz.topicId)).toEqual(["T5"]);
    expect(searchQuizzes(tracks, "acl verification show").map((hit) => hit.quiz.topicId)).toEqual(["T4"]);
    expect(searchQuizzes(tracks, "ccna").map((hit) => hit.quiz.topicId)).toEqual(["T6"]);
  });

  test("an empty query or no match gives nothing", () => {
    expect(searchQuizzes(tracks, "   ")).toEqual([]);
    expect(searchQuizzes(tracks, "bgp")).toEqual([]);
  });
});
