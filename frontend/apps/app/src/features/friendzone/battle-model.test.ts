import { describe, expect, test } from "bun:test";

import { ApiError, describeError } from "@/lib/api/errors";
import type { Battle, BattlePlayer, BattleSummary } from "./battle-api";
import {
  accuracy,
  battleFailure,
  battlePhase,
  outcome,
  phaseKey,
  roundAnnouncement,
  shortcutChoice,
  summaryLine,
  warningFor,
} from "./battle-model";
import { battleFromParam, searchForBattle, searchForView } from "./model";

const player = (patch: Partial<BattlePlayer> = {}): BattlePlayer => ({
  playerId: "11111111-1111-4111-8111-111111111111",
  displayName: "Rehan",
  initials: "R",
  role: "challenger",
  ready: true,
  score: 0,
  correct: 0,
  answered: 0,
  winner: false,
  ...patch,
});
const base = { status: "active" as const, result: null, endReason: null, me: player(), opponent: player({ role: "opponent" }) };
const OPTIONS = ["A", "B", "C", "D"].map((key) => ({ key, text: `Option ${key}` }));
const question = { round: 2, stem: "?", options: OPTIONS, seconds: 30, opensAt: "", closesAt: "", myChoice: null };

describe("the phase", () => {
  const phase = (patch: Partial<Battle>) => battlePhase({ ...base, rounds: [], question: null, ...patch } as Battle);
  test("invitations and waiting depend on who you are", () => {
    expect(phase({ status: "invited" }).kind).toBe("invited-out");
    expect(phase({ status: "invited", me: player({ role: "opponent" }) }).kind).toBe("invited-in");
    expect(phase({ status: "waiting" }).kind).toBe("ready");
    expect(phase({ status: "waiting", me: player({ role: "opponent" }) }).kind).toBe("waiting-start");
  });

  test("an active exam: the countdown before the paper, the exam, then the waiting screen once I submitted", () => {
    const exam = { secondsPerQuestion: 60, endsAt: "", cutoffAt: "", questions: [], responses: [], submittedAt: null, opponentSubmitted: false };
    expect(phase({ format: "exam" }).kind).toBe("countdown");
    expect(phase({ format: "exam", exam }).kind).toBe("exam");
    expect(phase({ format: "exam", exam: { ...exam, submittedAt: "2026-11-25T10:05:00Z" } }).kind).toBe("submitted");
    expect(phaseKey(phase({ format: "exam", exam }))).toBe("exam");
  });

  test("an open round battle can't be played any more (the server ends it): it reads as ended", () => {
    expect(phase({ question }).kind).toBe("ended");
    expect(phase({ format: "rounds" }).kind).toBe("ended");
  });

  test("completed is the result; declined, expired and cancelled are endings", () => {
    expect(phase({ status: "completed" }).kind).toBe("result");
    for (const status of ["declined", "expired", "cancelled"] as const) expect(phase({ status }).kind).toBe("ended");
  });
});

describe("outcomes", () => {
  test("an exam's words: marks with a minus sign, and no round-battle wording", () => {
    const exam = (patch: Partial<Battle>) => outcome({ ...base, status: "completed", format: "exam", ...patch } as Battle);
    expect(exam({ result: "points", me: player({ winner: true, score: 12 }), opponent: player({ role: "opponent", score: -3 }) })).toEqual({
      headline: "You won",
      detail: "Final marks 12 to −3.",
    });
    expect(exam({ result: "void", endReason: "absent" }).detail).toBe("Neither of you answered a question.");
    // An exam's other voids all read "ended" (I21): the API never sends `deleted` for one, and no reason is named.
    for (const endReason of ["ended", "deleted"]) expect(exam({ result: "void", endReason }).detail).toBe("This battle ended early, so it doesn't count.");
    expect(exam({ result: "draw" }).detail).toContain("Same marks");
    for (const result of ["points", "draw", "void", "forfeit"] as const) expect(exam({ result }).detail).not.toMatch(/round/i);
  });

  const done = (patch: Partial<Battle>) => outcome({ ...base, status: "completed", ...patch } as Battle);
  test("points: won, lost, with the score", () => {
    expect(done({ result: "points", me: player({ winner: true, score: 300 }), opponent: player({ role: "opponent", score: 120 }) })).toEqual({
      headline: "You won",
      detail: "Final score 300 to 120.",
    });
    expect(done({ result: "points", opponent: player({ role: "opponent", winner: true }) }).headline).toBe("You lost");
    expect(done({ result: "draw" }).headline).toBe("Draw");
  });

  test("forfeit and void say why, and a block reads like any other early end", () => {
    expect(done({ result: "forfeit", me: player({ winner: true }) }).detail).toBe("Rehan missed three rounds in a row.");
    expect(done({ result: "forfeit" }).headline).toBe("You lost");
    expect(done({ result: "void", endReason: "absent" }).detail).toBe("Both of you missed three rounds in a row.");
    expect(done({ result: "void", endReason: "blocked" })).toEqual(done({ result: "void", endReason: "unfriended" }));
    expect(done({ result: "void", endReason: "suspended" })).toEqual(done({ result: "void", endReason: "blocked" }));
  });

  test("never played", () => {
    const ended = (patch: Partial<Battle>) => outcome({ ...base, ...patch } as Battle);
    expect(ended({ status: "declined" }).detail).toBe("Rehan couldn't play this time.");
    expect(ended({ status: "expired", endReason: "expired" }).headline).toBe("Expired");
    expect(ended({ status: "cancelled", endReason: "not_enough_questions" }).detail).toContain("No checked questions for this choice yet");
    expect(ended({ status: "cancelled", endReason: "blocked" })).toEqual(ended({ status: "cancelled", endReason: "unfriended" }));
  });

  test("a member whose account is gone has no name, and is not invented", () => {
    expect(outcome({ ...base, status: "declined", opponent: player({ displayName: null }) } as Battle).detail).toBe(
      "A former member couldn't play this time.",
    );
  });
});

describe("words", () => {
  test("announcements", () => {
    expect(roundAnnouncement(question, 5)).toBe("Question 2 of 5, 30 seconds.");
    expect([30, 11, 10, 6, 5, 1].map(warningFor)).toEqual([null, null, 10, 10, 5, 5]);
  });

  test("accuracy, or a dash before any answer", () => {
    expect(accuracy({ correct: 2, answered: 3 })).toBe("67%");
    expect(accuracy({ correct: 0, answered: 0 })).toBe("–");
  });

  test("list lines", () => {
    const summary = (patch: Partial<BattleSummary>): BattleSummary => ({
      battleId: "33333333-3333-4333-8333-333333333333",
      status: "invited",
      result: null,
      role: "opponent",
      opponent: player({ role: "challenger" }),
      program: { slug: "ccna", title: "CCNA" },
      month: 2,
      length: 5,
      createdAt: "",
      endedAt: null,
      expiresAt: null,
      ...patch,
    });
    expect(summaryLine(summary({}))).toBe("Challenged you · Month 2 · 5 questions");
    expect(summaryLine(summary({ role: "challenger", status: "waiting" }))).toContain("press Ready");
    expect(summaryLine(summary({ status: "completed", result: "points" }))).toContain("Won");
    expect(summaryLine(summary({ status: "completed", result: "forfeit", opponent: player({ winner: true }) }))).toContain("Lost by forfeit");
    expect(summaryLine(summary({ status: "expired" }))).toContain("Not played");
  });
});

describe("answer shortcuts", () => {
  test("1-8 pick by position and A-H by letter, only for options the question has", () => {
    expect(shortcutChoice("1", OPTIONS)).toBe("A");
    expect(shortcutChoice("4", OPTIONS)).toBe("D");
    expect(shortcutChoice("5", OPTIONS)).toBeNull();
    expect(shortcutChoice("c", OPTIONS)).toBe("C");
    expect(shortcutChoice("E", OPTIONS)).toBeNull();
    for (const key of ["0", "9", "x", "Enter", " ", "ab"]) expect(shortcutChoice(key, OPTIONS)).toBeNull();
  });
});

describe("failure copy", () => {
  test("every battle code has the app's own words", () => {
    for (const code of [
      "not_enough_questions",
      "battle_exists",
      "invite_limit",
      "busy_in_battle",
      "battle_not_open",
      "round_closed",
      "already_answered",
      "invalid_choice",
    ]) {
      const error = new ApiError(409, code);
      expect(error.code).toBe(code as never);
      expect(battleFailure(error, "answer").message).toBe(describeError(error).message);
    }
    expect(describeError(new ApiError(409, "not_enough_questions")).message).toContain("No checked questions for this choice yet.");
  });

  test("states that moved on are read again; a 404 says what it means for the action", () => {
    for (const code of ["battle_not_open", "round_closed", "already_answered"]) expect(battleFailure(new ApiError(409, code), "answer").reload).toBe(true);
    expect(battleFailure(new ApiError(404, "not_found"), "create").message).toBe("This friend can't be challenged in this program.");
    expect(battleFailure(new ApiError(404, "not_found"), "move").message).toBe("This battle isn't available any more.");
    expect(battleFailure(new ApiError(409, "invite_limit"), "create").reload).toBe(false);
  });
});

describe("the battle in the address", () => {
  const ID = "33333333-3333-4333-8333-333333333333";
  test("only an id opens a battle", () => {
    expect(battleFromParam(ID)).toBe(ID);
    expect(battleFromParam("../x")).toBeNull();
    expect(battleFromParam(null)).toBeNull();
  });

  test("opening one keeps the month and the Friend Zone side; closing keeps the rest; Practice drops it", () => {
    expect(searchForBattle("?month=2", ID)).toBe(`?month=2&zone=friends&battle=${ID}`);
    expect(searchForBattle(`?month=2&zone=friends&battle=${ID}`, null)).toBe("?month=2&zone=friends");
    expect(searchForView(`?zone=friends&battle=${ID}&month=3`, "practice")).toBe("?month=3");
  });
});
