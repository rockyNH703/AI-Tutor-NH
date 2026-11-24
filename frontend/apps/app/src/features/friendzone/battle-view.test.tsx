import { describe, expect, test } from "bun:test";
import { Dialog } from "@v1/ui/dialog";
import { renderToStaticMarkup } from "react-dom/server";

import type { Battle, BattlePlayer, BattleSummary } from "./battle-api";
import { BattleScreen } from "./battle-view";
import { SummaryRow } from "./battles-section";
import { ChallengeForm } from "./challenge-dialog";

const T0 = Date.parse("2026-11-24T10:00:00.000Z");
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
const QUESTION = {
  round: 2,
  stem: "Which protocol runs between autonomous systems?",
  options: OPTIONS,
  seconds: 30,
  opensAt: at(-10_000),
  closesAt: at(20_000),
  myChoice: null,
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
    length: 5,
    roundsClosed: 0,
    expiresAt: null,
    startsAt: at(2000),
    endsAt: null,
    nextChangeAt: null,
    serverTime: at(0),
    me: player({ role: "challenger", displayName: "Asha", initials: "A" }),
    opponent: player(),
    question: null,
    rounds: [],
    review: null,
    ...patch,
  };
}

function screen(b: Battle, now = T0): string {
  return renderToStaticMarkup(<BattleScreen battle={b} offset={0} now={now} busy={false} note={null} onClose={noop} onMove={noop} onAnswer={noop} />);
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
const SCHOOL = /\b(students?|homework|syllabus|ncert|cbse|textbooks?|teachers?|chapters?|doubts?|class \d{1,2}|exam boards?|schools?)\b/i;
const opponentSide = { me: player({ role: "opponent", displayName: "Asha" }), opponent: player({ role: "challenger" }) };

describe("before the start", () => {
  test("invited, as the one challenged: Accept and play, or Decline", () => {
    const html = screen(battle({ status: "invited", expiresAt: at(9 * 60_000), ...opponentSide }));
    expect(text(html)).toContain("challenged you to a live battle of 5 questions. It expires in 9 minutes.");
    expect(buttons(html)).toEqual(["Back to Friend Zone", "Accept and play", "Decline"]);
    expect(html).toContain('<bdi dir="auto">ریحان Rehan</bdi>');
  });

  test("invited, as the challenger: waiting, with Cancel", () => {
    const html = screen(battle({ status: "invited", expiresAt: at(60_000) }));
    expect(text(html)).toContain("Waiting for ریحان Rehan to accept. It expires in 1 minute.");
    expect(buttons(html)).toEqual(["Back to Friend Zone", "Cancel challenge"]);
  });

  test("waiting: the challenger gets Ready; the one who accepted waits", () => {
    expect(buttons(screen(battle({ status: "waiting", expiresAt: at(120_000) })))).toEqual(["Back to Friend Zone", "Ready", "Cancel"]);
    const waiting = screen(battle({ status: "waiting", expiresAt: at(120_000), ...opponentSide }));
    expect(text(waiting)).toContain("Waiting for ریحان Rehan to start the battle.");
    expect(buttons(waiting)).toEqual(["Back to Friend Zone"]);
  });

  test("the countdown shows 3-2-1 from the server's start time", () => {
    expect(text(screen(battle({ startsAt: at(2500) })))).toContain("Get ready 3");
    expect(text(screen(battle({ startsAt: at(400) })))).toContain("Get ready 1");
  });
});

describe("a round", () => {
  test("open: the question, its time, every option a 44 px button with its key, and the shortcuts' help", () => {
    const html = screen(battle({ question: QUESTION }));
    const shown = text(html);
    expect(shown).toContain("Question 2 of 5");
    expect(shown).toContain("20 s");
    expect(html).toContain('tabindex="-1"'); // the question heading takes focus at the round's start
    expect(html.match(/aria-pressed="false"/g)?.length).toBe(4);
    expect(html.match(/min-h-11 w-full/g)?.length).toBe(4);
    expect(shown).toContain("One tap is final.");
    expect(shown).toContain("press 1–4 or A–D to answer while focus is in the battle");
    expect(html).toContain("motion-reduce:hidden"); // the bar is not drawn under the device's reduced motion (the app's: a hook)
  });

  test("10 seconds or less: the words and an icon, never colour alone", () => {
    const html = screen(battle({ question: { ...QUESTION, closesAt: at(9200) } }));
    expect(text(html)).toContain("10 seconds left");
    expect(html).toContain("lucide-alarm-clock");
  });

  test("locked: the pick is marked in words, and every option stops answering", () => {
    const html = screen(battle({ question: { ...QUESTION, myChoice: "B" } }));
    expect(text(html)).toContain("Your answer");
    expect(text(html)).toContain("Answer locked: B. The answer shows when the round closes.");
    expect(html.match(/aria-disabled="true"/g)?.length).toBe(4);
    expect(html).toMatch(/aria-pressed="true"[^>]*>[\s\S]*?BGP/);
  });

  test("secrecy: nothing shows which option is correct while the round is open", () => {
    for (const myChoice of [null, "B"]) {
      const shown = text(screen(battle({ question: { ...QUESTION, myChoice }, rounds: [] })));
      expect(shown).not.toMatch(/correct/i);
      expect(shown).not.toContain("Why");
    }
  });

  test("the reveal: the key, both picks and the points; a withdrawn question scores nothing", () => {
    const reveal = screen(
      battle({
        roundsClosed: 1,
        rounds: [{ round: 1, voided: false, correctKey: "B", myChoice: "B", theirChoice: "A", myPoints: 128, theirPoints: 0 }],
        me: player({ role: "challenger", displayName: "Asha", score: 128 }),
      }),
    );
    expect(text(reveal)).toContain("Round 1 closed Correct answer: B. You chose B, 128 points. ریحان Rehan chose A, 0 points.");
    expect(text(reveal)).toContain("You 128");
    const voided = screen(battle({ roundsClosed: 1, rounds: [{ round: 1, voided: true, correctKey: "B", myChoice: "B", theirChoice: null, myPoints: 0, theirPoints: 0 }] }));
    expect(text(voided)).toContain("Question withdrawn: no points.");
  });
});

describe("the end", () => {
  const review = [
    {
      round: 1,
      stem: "Which protocol runs between autonomous systems?",
      options: OPTIONS,
      correctKey: "B",
      explanation: "BGP is the exterior gateway protocol.",
      voided: false,
      myChoice: "B",
      theirChoice: "A",
    },
  ];

  test("completed: who won, the scores, accuracy and the review with each Why; no Rematch yet", () => {
    const html = screen(
      battle({
        status: "completed",
        result: "points",
        startsAt: null,
        me: player({ role: "challenger", displayName: "Asha", score: 640, correct: 4, answered: 5, winner: true }),
        opponent: player({ score: 380, correct: 3, answered: 4 }),
        review,
      }),
    );
    const shown = text(html);
    expect(shown).toContain("You won Final score 640 to 380.");
    expect(shown).toContain("Accuracy 80% 75%");
    expect(shown).toContain("Correct 4 of 5 3 of 4");
    expect(shown).toContain("Why: BGP is the exterior gateway protocol.");
    expect(shown).toContain("Correct answer · You");
    expect(html).toContain('<th scope="col"');
    expect(buttons(html)).toEqual(["Back to Friend Zone"]);
    expect(shown).not.toMatch(/rematch/i);
  });

  test("void: no result, said plainly", () => {
    const shown = text(screen(battle({ status: "completed", result: "void", endReason: "absent", review: [] })));
    expect(shown).toContain("No result Both of you missed three rounds in a row.");
  });

  test("expired and couldn't-start endings", () => {
    expect(text(screen(battle({ status: "expired", endReason: "expired" })))).toContain("Expired This battle wasn't started in time.");
    expect(text(screen(battle({ status: "cancelled", endReason: "not_enough_questions" })))).toContain("No checked questions for this choice yet");
  });

  test("no state uses school words or shows an email or an id", () => {
    const states = [
      battle({ status: "invited", ...opponentSide }),
      battle({ question: QUESTION }),
      battle({ status: "completed", result: "points", review }),
      battle({ status: "expired" }),
    ];
    for (const state of states) {
      const shown = text(screen(state));
      expect(shown).not.toMatch(SCHOOL);
      expect(shown).not.toMatch(/@|[0-9a-f]{8}-[0-9a-f]{4}/i);
    }
  });
});

describe("the list and the challenge", () => {
  test("a battle row names the opponent in their own direction and what it waits for", () => {
    const summary: BattleSummary = {
      battleId: "33333333-3333-4333-8333-333333333333",
      status: "invited",
      result: null,
      role: "opponent",
      opponent: player({ role: "challenger" }),
      program: { slug: "ccna", title: "CCNA" },
      month: 2,
      length: 10,
      createdAt: at(0),
      endedAt: null,
      expiresAt: at(60_000),
    };
    const html = renderToStaticMarkup(<SummaryRow summary={summary} />);
    expect(html).toContain('<bdi dir="auto">ریحان Rehan</bdi>');
    expect(text(html)).toContain("Challenged you · Month 2 · 10 questions");
  });

  test("the challenge form: month, optional topic, difficulty Mixed and length 5 picked first", () => {
    const program = {
      slug: "ccna",
      title: "CCNA",
      months: [
        { month: 1, title: "Basics", material: { documents: 1, lessons: 1, questions: 1 } },
        { month: 2, title: "Routing", material: { documents: 1, lessons: 1, questions: 1 } },
      ],
    };
    const friend = { playerId: "11111111-1111-4111-8111-111111111111", displayName: "Rehan", initials: "R", relation: "friend" as const, avatarUrl: null, programs: null };
    const html = renderToStaticMarkup(
      <Dialog open>
        <ChallengeForm friend={friend} program={program} month={2} onClose={noop} onCreated={noop} onExists={noop} />
      </Dialog>,
    );
    const shown = text(html);
    expect(shown).toContain("Month 2: Routing");
    expect(html).toMatch(/<option value="2" selected="">/);
    expect(shown).toContain("Loading topics…");
    expect(html).toMatch(/checked="" value="mixed"/);
    expect(html).toMatch(/checked="" value="5"/);
    expect(html).not.toContain("min-h-10"); // every pill is a 44 px target
    expect(buttons(html)).toEqual(["Cancel", "Send challenge"]);
  });
});
