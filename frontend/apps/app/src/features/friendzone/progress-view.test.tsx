import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { Battle, BattlePlayer } from "./battle-api";
import { BattleScreen } from "./battle-view";
import type { Daily } from "./daily-api";
import { DailyScreen } from "./daily-view";
import { LevelBar, StatsBlock } from "./progress-view";

const T0 = Date.parse("2026-11-24T10:00:00.000Z");
const at = (ms: number) => new Date(T0 + ms).toISOString();
const noop = () => {};

function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}
const buttons = (html: string) => [...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((match) => text(match[1] ?? ""));
const SCHOOL = /\b(students?|homework|syllabus|ncert|cbse|textbooks?|teachers?|chapters?|doubts?|class \d{1,2}|exam boards?|schools?)\b/i;

const STATS = {
  battles: 6,
  wins: 3,
  losses: 2,
  draws: 1,
  correct: 21,
  answered: 28,
  xp: 220,
  level: 2,
  levelFloor: 100,
  nextLevelAt: 300,
  badges: [{ key: "first_battle", title: "First Battle", description: "Finish your first rewarded battle." }],
  dailiesCompleted: 4,
  dailyStreak: 2,
};

describe("level and stats", () => {
  test("the level bar is a labelled progressbar with its numbers in words", () => {
    const html = renderToStaticMarkup(<LevelBar page={STATS} />);
    expect(html).toContain('role="progressbar"');
    expect(html).toContain('aria-valuenow="120"');
    expect(html).toContain('aria-valuemax="200"');
    expect(text(html)).toBe("Level 2 120 / 200 XP to level 3");
  });

  test("a record, accuracy, dailies and earned badges with what earned them", () => {
    const shown = text(renderToStaticMarkup(<StatsBlock stats={STATS} own />));
    expect(shown).toContain("Battles 6 battles: 3 wins, 2 losses, 1 draw");
    expect(shown).toContain("Accuracy 75% (21 of 28)");
    expect(shown).toContain("Daily challenges 4 completed");
    expect(shown).toContain("Daily streak 2 days in a row");
    expect(shown).toContain("First Battle Finish your first rewarded battle.");
    expect(shown).not.toContain("Level 2"); // your own level is in the XP panel above it
  });

  test("a friend's stats carry their level; no badges reads plainly", () => {
    const shown = text(renderToStaticMarkup(<StatsBlock stats={{ ...STATS, badges: [] }} own={false} />));
    expect(shown).toContain("Level 2");
    expect(shown).toContain("No badges yet.");
  });
});

const player = (patch: Partial<BattlePlayer> = {}): BattlePlayer => ({
  playerId: "11111111-1111-4111-8111-111111111111",
  displayName: "Rehan",
  initials: "R",
  role: "opponent",
  ready: true,
  score: 300,
  correct: 3,
  answered: 5,
  winner: false,
  ...patch,
});
function finished(result: Battle["result"]): Battle {
  return {
    battleId: "33333333-3333-4333-8333-333333333333",
    status: "completed",
    endReason: result === "void" ? "absent" : "finished",
    result,
    program: { slug: "ccna", title: "CCNA" },
    month: 1,
    topicId: null,
    difficulty: "mixed",
    length: 5,
    roundsClosed: 5,
    expiresAt: null,
    startsAt: null,
    endsAt: null,
    nextChangeAt: null,
    serverTime: at(0),
    me: player({ role: "challenger", displayName: "Asha", winner: result === "points" }),
    opponent: player(),
    question: null,
    rounds: [],
    review: [],
  };
}
const result = (b: Battle) =>
  renderToStaticMarkup(<BattleScreen battle={b} offset={0} now={T0} busy={false} note={null} onClose={noop} onMove={noop} onRematch={noop} />);

describe("rematch", () => {
  test("offered after points, a draw or a forfeit; never after a void battle", () => {
    for (const outcome of ["points", "draw", "forfeit"] as const) expect(buttons(result(finished(outcome)))).toContain("Rematch");
    expect(buttons(result(finished("void")))).not.toContain("Rematch");
  });
});

const OPTIONS = [
  { key: "A", text: "22" },
  { key: "B", text: "23" },
];
function daily(patch: Partial<Daily>): Daily {
  return {
    available: true,
    challengeId: "44444444-4444-4444-8444-444444444444",
    date: "2026-11-24",
    program: { slug: "ccna", title: "CCNA" },
    startsAt: at(-36_000_000),
    closesAt: at(52_000_000),
    serverTime: at(0),
    attempt: null,
    question: null,
    review: null,
    friends: null,
    nextChangeAt: at(50_000_000),
    ...patch,
  };
}
const inProgress = (served: number) => ({ status: "in_progress" as const, served, answered: served - 1, correct: null, score: null, startedAt: at(-60_000), finishedAt: null });
const QUESTION = { position: 3, stem: "Which port does SSH use?", options: OPTIONS, seconds: 25, servedAt: at(-5000), closesAt: at(20_000), myChoice: null };
const screen = (d: Daily) => renderToStaticMarkup(<DailyScreen daily={d} offset={0} now={T0} busy={false} note={null} onNext={noop} onAnswer={noop} />);
const FRIENDS = [
  { playerId: "11111111-1111-4111-8111-111111111111", displayName: "ریحان Rehan", initials: "رR", status: "completed" as const, score: 640, correct: 7 },
  { playerId: "22222222-2222-4222-8222-222222222222", displayName: "Meera", initials: "M", status: "completed" as const, score: null, correct: null },
  { playerId: "55555555-5555-4555-8555-555555555555", displayName: "Li Lei", initials: "L", status: "in_progress" as const, score: null, correct: null },
];
const REVIEW = [
  { position: 1, stem: "Which port does SSH use?", options: OPTIONS, correctKey: "A", explanation: "SSH listens on TCP 22.", myChoice: "A", correct: true, points: 140, voided: false },
  { position: 2, stem: "Which port does Telnet use?", options: OPTIONS, correctKey: "B", explanation: "Telnet uses TCP 23.", myChoice: null, correct: null, points: 0, voided: true },
];

describe("the daily challenge, state by state", () => {
  test("no challenge today, said honestly", () => {
    expect(text(screen(daily({ available: false, challengeId: null })))).toContain("No daily challenge today.");
    expect(buttons(screen(daily({ available: false, challengeId: null })))).toEqual([]);
  });

  test("not started: what it is, and Start", () => {
    const html = screen(daily({}));
    expect(text(html)).toContain("Today's challenge: 10 questions from CCNA");
    expect(buttons(html)).toEqual(["Start today's challenge"]);
  });

  test("a question: its number, time, options as 44 px buttons and the answer keys", () => {
    const html = screen(daily({ attempt: inProgress(3), question: QUESTION }));
    const shown = text(html);
    expect(shown).toContain("Question 3 of 10");
    expect(shown).toContain("20 s");
    expect(html.match(/aria-pressed="false"/g)?.length).toBe(2);
    expect(shown).toContain("press 1–2 or A–B to answer while focus is in the challenge.");
    expect(buttons(html).join(" ")).not.toContain("Next question"); // answer first
  });

  test("locked: Answer locked, then Next question; Finish on the last", () => {
    const html = screen(daily({ attempt: inProgress(3), question: { ...QUESTION, myChoice: "B" } }));
    expect(text(html)).toContain("Answer locked: B. Answers show when you finish.");
    expect(buttons(html)).toContain("Next question");
    const last = screen(daily({ attempt: inProgress(10), question: { ...QUESTION, position: 10, myChoice: "A" } }));
    expect(buttons(last)).toContain("Finish");
  });

  test("between questions: what comes next, and its button", () => {
    const html = screen(daily({ attempt: inProgress(3) }));
    expect(text(html)).toContain("Ready for question 4 of 10.");
    expect(buttons(html)).toEqual(["Next question"]);
  });

  test("finished: the score, friends' results (a score only for those who show stats) and the review with each Why", () => {
    const html = screen(
      daily({
        attempt: { status: "finished", served: 10, answered: 9, correct: 7, score: 820, startedAt: at(-600_000), finishedAt: at(-1000) },
        friends: FRIENDS,
        review: REVIEW,
      }),
    );
    const shown = text(html);
    expect(shown).toContain("Finished 820 points · 7 of 10 correct");
    expect(shown).toContain("ریحان Rehan 640 points · 7 correct");
    expect(shown).toContain("Meera Completed");
    expect(shown).toContain("Li Lei In progress");
    expect(shown).toContain("Why: SSH listens on TCP 22.");
    expect(shown).toContain("Question 2 · Question withdrawn: no points");
    expect(shown).toContain("You didn't answer this one.");
    expect(html).toContain('<bdi dir="auto"');
  });

  test("secrecy: before finishing, no correct answer, no Why and no friends' results, whatever arrives", () => {
    const states = [
      daily({ attempt: inProgress(3), question: QUESTION }),
      daily({ attempt: inProgress(3), question: { ...QUESTION, myChoice: "A" } }),
      daily({ attempt: inProgress(3) }),
      // even if a response carried them early, an unfinished attempt never draws them
      daily({ attempt: inProgress(3), friends: FRIENDS, review: REVIEW }),
    ];
    for (const state of states) {
      const shown = text(screen(state));
      expect(shown).not.toMatch(/correct answer/i);
      expect(shown).not.toContain("Why");
      expect(shown).not.toContain("Rehan");
      expect(shown).not.toContain("points ·");
    }
  });

  test("no state uses school words or shows an email or an id", () => {
    for (const state of [daily({}), daily({ attempt: inProgress(3), question: QUESTION }), daily({ available: false, challengeId: null })]) {
      const shown = text(screen(state));
      expect(shown).not.toMatch(SCHOOL);
      expect(shown).not.toMatch(/@|[0-9a-f]{8}-[0-9a-f]{4}/i);
    }
  });
});
