import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { Battle, BattlePlayer, Exam } from "./battle-api";
import { BattleGone, BattleScreen } from "./battle-view";
import { type ExamStore, createExamStore } from "./exam-store";

const T0 = Date.parse("2026-11-25T10:00:00.000Z");
const at = (ms: number) => new Date(T0 + ms).toISOString();
const noop = () => {};

const player = (patch: Partial<BattlePlayer> = {}): BattlePlayer => ({
  playerId: "11111111-1111-4111-8111-111111111111",
  displayName: "ریحان Rehan",
  initials: "رR",
  role: "opponent",
  ready: true,
  score: 0,
  correct: 0,
  answered: 0,
  winner: false,
  ...patch,
});
const OPTIONS = [
  { key: "A", text: "OSPF" },
  { key: "B", text: "BGP" },
  { key: "C", text: "RIP" },
  { key: "D", text: "EIGRP" },
];
const EXAM: Exam = {
  secondsPerQuestion: 60,
  endsAt: at(540_000),
  cutoffAt: at(542_000),
  questions: Array.from({ length: 10 }, (_, index) => ({ round: index + 1, stem: `Question stem ${index + 1}`, options: OPTIONS })),
  responses: [
    { round: 1, choice: null, marked: false, rev: 1 },
    { round: 2, choice: "B", marked: false, rev: 2 },
    { round: 3, choice: null, marked: true, rev: 1 },
    { round: 4, choice: "A", marked: true, rev: 3 },
  ],
  submittedAt: null,
  opponentSubmitted: false,
};

function battle(patch: Partial<Battle>): Battle {
  return {
    battleId: "33333333-3333-4333-8333-333333333333",
    status: "active",
    endReason: null,
    result: null,
    program: { slug: "ccna", title: "CCNA" },
    month: 2,
    topicId: null,
    difficulty: "mixed",
    length: 10,
    roundsClosed: 0,
    expiresAt: null,
    startsAt: at(-60_000),
    endsAt: at(540_000),
    nextChangeAt: at(542_000),
    serverTime: at(0),
    me: player({ role: "challenger", displayName: "Asha", initials: "A" }),
    opponent: player(),
    question: null,
    rounds: [],
    review: null,
    format: "exam",
    repeatsFromPrevious: 0,
    exam: EXAM,
    xp: null,
    ...patch,
  };
}

function storeWith(exam: Exam, setup?: (store: ExamStore) => void): ExamStore {
  const store = createExamStore({
    send: () => new Promise(() => {}), // a save that never answers: always in flight
    timeout: () => new AbortController().signal,
    schedule: () => 0,
    cancel: noop,
    isOffline: () => false,
    onOnline: () => noop,
  });
  store.adopt(exam.responses);
  setup?.(store);
  return store;
}

function screen(b: Battle, store?: ExamStore, now = T0, busy = false): string {
  return renderToStaticMarkup(
    <BattleScreen battle={b} offset={0} now={now} busy={busy} note={null} onClose={noop} onMove={noop} store={store ?? (b.exam ? storeWith(b.exam) : undefined)} />,
  );
}
function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}
const buttons = (html: string) => [...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((match) => text(match[1] ?? ""));
const pills = (html: string) => [...html.matchAll(/<button[^>]*aria-label="(Question \d+[^"]*)"[^>]*>([\s\S]*?)<\/button>/g)];
const SCHOOL = /\b(students?|homework|syllabus|ncert|cbse|textbooks?|teachers?|chapters?|doubts?|class \d{1,2}|exam boards?|schools?)\b/i;

describe("the exam screen", () => {
  test("the header: title, the opponent's status, one countdown (role timer), save status, Questions and Submit; no hide-timer", () => {
    const html = screen(battle({}));
    const shown = text(html);
    expect(shown).toContain("Battle with ریحان Rehan");
    expect(shown).toContain("ریحان Rehan : In progress");
    expect(html.match(/role="timer"/g)).toHaveLength(1);
    expect(html).toContain('aria-label="Time left: 9 minutes"');
    expect(shown).toContain("9:00");
    expect(buttons(html)).toContain("Questions");
    expect(buttons(html)).toContain("Submit");
    expect(shown).not.toMatch(/hide (the )?timer/i);
    expect(html.match(/role="status"/g)).toHaveLength(1); // one live region (mirrored only inside an open sheet or dialog)
  });

  test("the question: native radios in 44 px labels, the help line, and the four actions that wrap at 320 px", () => {
    const html = screen(battle({}));
    const shown = text(html);
    expect(shown).toContain("Question 1 of 10");
    expect(html.match(/type="radio"/g)).toHaveLength(4);
    expect(html.match(/<label class="flex min-h-11/g)).toHaveLength(4);
    expect(shown).toContain("Keys 1–4 or A–D choose an option, and so do the arrow keys on the options.");
    expect(shown).toContain("A wrong answer costs 1 mark; Clear Response removes an answer.");
    for (const label of ["Previous", "Clear Response", "Mark for Review & Next", "Save & Next"]) expect(buttons(html)).toContain(label);
    expect(html.match(/h-auto min-h-11 whitespace-normal/g)?.length).toBeGreaterThanOrEqual(4);
    expect(html).toMatch(/aria-disabled="true"[^>]*>Previous/); // focus stays: aria-disabled, never disabled
    expect(html).not.toMatch(/<button[^>]* disabled=""/);
  });

  test("saving never makes the screen busy: options and actions stay usable while a save is in flight", () => {
    const store = storeWith(EXAM, (s) => s.set(1, { choice: "C", marked: false }));
    expect(store.getSnapshot().inFlight).toBe(true);
    const html = screen(battle({}), store);
    expect(text(html)).toContain("Saving…");
    expect(html).not.toContain('disabled=""');
    expect(html).toContain('aria-label="Question 1, answered, not saved"');
    expect(text(html)).toContain("Question 3 of 10"); // opens at the lowest unanswered question
    expect(html).not.toMatch(/aria-disabled="true"[^>]*>Save &amp; Next/);
  });

  test("the palette: every state by shape and glyph with words, Not saved on top, the current one marked", () => {
    const store = storeWith(EXAM, (s) => s.set(5, { choice: "D", marked: false }));
    const html = screen(battle({}), store);
    const labels = pills(html).map((match) => match[1]);
    expect(labels.slice(0, 6)).toEqual([
      "Question 1, not answered",
      "Question 2, answered",
      "Question 3, marked for review",
      "Question 4, answered and marked for review",
      "Question 5, answered, not saved",
      "Question 6, not visited",
    ]);
    expect(html).toMatch(/aria-label="Question 1, not answered" aria-current="step"/);
    const glyphs = pills(html).map((match) => (match[2]?.match(/lucide-([a-z-]+)/) ?? [])[1] ?? "none");
    expect(glyphs.slice(0, 6)).toEqual(["minus", "check", "bookmark", "bookmark-check", "triangle-alert", "none"]);
    expect(html).toContain("rounded-full"); // marked states are round, the rest square: shape before colour
    expect(html).toContain("border-dashed"); // not visited
    const legend = text(html.slice(html.indexOf('aria-label="Legend"')));
    expect(legend).toContain("Answered 2"); // 2 and 5 (unsaved, shown at once)
    expect(legend).toContain("Not visited 5");
    expect(legend).toContain("Not saved 1");
  });

  test("time up: the options lock and the time-up heading replaces the question", () => {
    const html = screen(battle({}), undefined, T0 + 540_000);
    expect(text(html)).toContain("Time's up: submitting…");
    expect(html).not.toContain('type="radio"');
    expect(html).toMatch(/aria-disabled="true"[^>]*>[\s\S]*?Submit/);
  });

  test("after time up nothing stays live: the pills are aria-disabled and there is no Save again", () => {
    const offline = (live: boolean) => {
      const store = createExamStore({
        send: () => new Promise(() => {}),
        timeout: () => new AbortController().signal,
        schedule: () => 0,
        cancel: noop,
        isOffline: () => true,
        onOnline: () => noop,
      });
      store.adopt(EXAM.responses);
      store.set(5, { choice: "D", marked: false });
      return screen(battle({}), store, live ? T0 : T0 + 540_000);
    };
    const before = offline(true);
    expect(buttons(before)).toContain("Save again");
    expect(pills(before).some((match) => match[0].includes('aria-disabled="true"'))).toBe(false);
    const after = offline(false);
    expect(buttons(after)).not.toContain("Save again");
    expect(pills(after).every((match) => match[0].includes('aria-disabled="true"'))).toBe(true);
  });

  test("the current pill is told apart from keyboard focus: a thicker brand ring and an underlined number", () => {
    const current = pills(screen(battle({}))).find((match) => match[0].includes('aria-current="step"'))?.[0] ?? "";
    expect(current).toContain("ring-[3px] ring-brand");
    expect(current).toContain("underline");
    expect(current).not.toContain("ring-2 ring-foreground"); // the focus ring is ring-2 in the foreground colour
  });

  test("one root for every phase, so the live region is never remounted between countdown, exam and result", () => {
    const root = /^<div class="flex flex-col gap-4"><p role="status"/;
    expect(screen(battle({}))).toMatch(root);
    expect(screen(battle({ startsAt: at(2_000), exam: null }))).toMatch(root);
    expect(screen(battle({ status: "completed", result: "points", exam: null }))).toMatch(root);
  });

  test("before the start: How it works, the marking rule, and the exam's length and time", () => {
    const html = screen(battle({ status: "invited", exam: null, me: player({ role: "opponent", displayName: "Asha" }), opponent: player({ role: "challenger" }) }));
    const shown = text(html);
    expect(shown).toContain("challenged you to an exam battle of 10 questions (10 minutes)");
    expect(shown).toContain("How it works");
    expect(shown).toContain("+4 for a correct answer, −1 for an incorrect one, 0 for no answer.");
    expect(shown).toContain("When time runs out, your saved answers are submitted for you.");
    const ready = text(screen(battle({ status: "waiting", exam: null })));
    expect(ready).toContain("How it works");
  });

  test("submitted: my own counts, the opponent's status, and when the result comes", () => {
    const html = screen(battle({ exam: { ...EXAM, submittedAt: at(0), opponentSubmitted: true } }));
    const shown = text(html);
    expect(shown).toContain("Submitted");
    expect(shown).toContain("Answered 2 Not answered 1 Marked, no answer 1 Not visited 6");
    expect(shown).toContain("ریحان Rehan : Submitted");
    expect(shown).toContain("The result shows when you both have submitted, or at the deadline (9:00 left).");
  });

  test("no exam state uses school words", () => {
    for (const state of [battle({}), battle({ status: "invited", exam: null }), battle({ exam: { ...EXAM, submittedAt: at(0) } })])
      expect(text(screen(state))).not.toMatch(SCHOOL);
  });
});

describe("the exam's result", () => {
  const review = [
    {
      round: 1,
      stem: "Which protocol runs between autonomous systems?",
      options: OPTIONS,
      correctKey: "B",
      explanation: "BGP is the exterior gateway protocol.",
      voided: false,
      myChoice: "B",
      theirChoice: null,
    },
    { round: 2, stem: null, options: OPTIONS, correctKey: null, explanation: null, voided: true, myChoice: "A", theirChoice: "C" },
  ];
  const done = (patch: Partial<Battle>) =>
    battle({
      status: "completed",
      result: "points",
      exam: null,
      endReason: "finished",
      startsAt: at(0),
      endsAt: at(600_000),
      rounds: Array.from({ length: 10 }, (_, index) => ({
        round: index + 1,
        voided: index === 1,
        correctKey: null,
        myChoice: null,
        theirChoice: null,
        myPoints: 0,
        theirPoints: 0,
      })),
      me: player({ role: "challenger", displayName: "Asha", score: 11, correct: 3, answered: 4, winner: true, timeTakenSeconds: 245 }),
      opponent: player({ score: -3, correct: 0, answered: 3, timeTakenSeconds: 600 }),
      review,
      xp: [
        { reason: "battle", amount: 20 },
        { reason: "win", amount: 30 },
      ],
      ...patch,
    });

  test("marks with a minus sign, correct, incorrect, unanswered, accuracy and time taken for both", () => {
    const shown = text(screen(done({})));
    expect(shown).toContain("You won Final marks 11 to −3.");
    expect(shown).toContain("Marks 11 −3");
    expect(shown).toContain("Correct 3 0");
    expect(shown).toContain("Incorrect 1 3");
    expect(shown).toContain("Unanswered 5 6"); // 9 counted (one voided)
    expect(shown).toContain("Accuracy 75% 0%");
    expect(shown).toContain("Time taken 4:05 Time ran out");
  });

  test("Your XP: the lines and a total, or none with the rule", () => {
    expect(text(screen(done({})))).toContain("Your XP Battle played +20 Win +30 Total +50 XP");
    const none = text(screen(done({ xp: [] })));
    expect(none).toContain("No XP for this battle.");
    expect(none).toContain("XP needs answers to at least half the questions");
  });

  test("the review: key, both picks with an icon and words; a voided question shows no stem or key", () => {
    const html = screen(done({}));
    const shown = text(html);
    expect(shown).toContain("You: B, correct");
    expect(shown).toContain("ریحان Rehan : not answered");
    expect(html).toContain("lucide-circle-check");
    expect(html).toContain("lucide-skip-forward");
    expect(shown).toContain("Question 2 Changed or withdrawn after the exam started: not scored.");
    expect(shown).toContain("Why: BGP is the exterior gateway protocol.");
  });

  test("a rematch that repeats questions says how many", () => {
    expect(text(screen(battle({ status: "invited", exam: null, repeatsFromPrevious: 3 })))).toContain("3 questions repeat from your last battle.");
    expect(text(screen(battle({ status: "invited", exam: null })))).not.toContain("repeat from");
  });

  test("a legacy round battle keeps its own words and table", () => {
    const shown = text(screen(done({ format: "rounds", xp: null, me: player({ role: "challenger", score: 640, correct: 4, answered: 5, winner: true }), opponent: player({ score: 380, correct: 3, answered: 4 }) })));
    expect(shown).toContain("Final score 640 to 380.");
    expect(shown).toContain("Score 640 380");
    expect(shown).not.toContain("Your XP");
    expect(shown).not.toContain("Time taken");
  });

  test("a 404 while the exam was running says Friend Zone is unavailable", () => {
    expect(text(renderToStaticMarkup(<BattleGone unavailable />))).toContain("Friend Zone is unavailable right now");
    expect(text(renderToStaticMarkup(<BattleGone />))).toContain("This battle isn't available");
  });
});
