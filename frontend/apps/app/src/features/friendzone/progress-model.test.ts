import { describe, expect, test } from "bun:test";

import { ApiError } from "@/lib/api/errors";
import { existingBattle } from "./battle-model";
import type { Daily } from "./daily-api";
import { POLL } from "./poll";
import { canRematch, dailyFailure, dailyPhase, levelProgress, nextDailyPoll, record, statsAccuracy, streakText, xpReason } from "./progress-model";

const T0 = Date.parse("2026-11-24T10:00:00.000Z");
const at = (ms: number) => new Date(T0 + ms).toISOString();

describe("XP and levels", () => {
  test("every reason the server gives has a readable label; an unknown one never shows its key", () => {
    for (const reason of ["battle", "win", "draw", "daily", "perfect", "streak_3", "streak_7", "streak_14", "streak_30"])
      expect(xpReason(reason)).not.toContain("_");
    expect(xpReason("streak_7")).toBe("7-day daily streak");
    expect(xpReason("something_new")).toBe("XP earned");
  });

  test("the level's span in words, clamped, for level n at 50·n·(n−1)", () => {
    // Level 2 spans 100 to 300 XP.
    expect(levelProgress({ xp: 220, level: 2, levelFloor: 100, nextLevelAt: 300 })).toEqual({
      level: 2,
      into: 120,
      span: 200,
      fraction: 0.6,
      text: "120 / 200 XP to level 3",
    });
    expect(levelProgress({ xp: 0, level: 1, levelFloor: 0, nextLevelAt: 100 }).text).toBe("0 / 100 XP to level 2");
    expect(levelProgress({ xp: 999, level: 2, levelFloor: 100, nextLevelAt: 300 }).fraction).toBe(1);
    expect(levelProgress({ xp: 5, level: 1, levelFloor: 0, nextLevelAt: 0 }).span).toBe(1); // never divides by 0
  });

  test("a record, accuracy and the daily streak, in words", () => {
    expect(record({ battles: 4, wins: 1, losses: 2, draws: 1 })).toBe("4 battles: 1 win, 2 losses, 1 draw");
    expect(statsAccuracy({ correct: 7, answered: 10 })).toBe("70% (7 of 10)");
    expect(statsAccuracy({ correct: 0, answered: 0 })).toBe("No answers yet");
    expect([0, 1, 5].map(streakText)).toEqual(["No current streak", "1 day in a row", "5 days in a row"]);
  });
});

describe("rematch", () => {
  test("only after a battle decided by play, never a void one", () => {
    expect(canRematch("completed", "points")).toBe(true);
    expect(canRematch("completed", "draw")).toBe(true);
    expect(canRematch("completed", "forfeit")).toBe(true);
    expect(canRematch("completed", "void")).toBe(false);
    expect(canRematch("cancelled", null)).toBe(false);
  });

  test("battle_exists opens the battle the error names, only when it is an id", () => {
    const id = "33333333-3333-4333-8333-333333333333";
    expect(existingBattle(new ApiError(409, "battle_exists", { current: { battleId: id } }))).toBe(id);
    expect(existingBattle(new ApiError(409, "battle_exists"))).toBeNull(); // the list fallback takes over
    expect(existingBattle(new ApiError(409, "battle_exists", { current: { battleId: "../x" } }))).toBeNull();
    expect(existingBattle(new ApiError(409, "invite_limit", { current: { battleId: id } }))).toBeNull();
  });
});

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
const attempt = (status: "not_started" | "in_progress" | "finished", served = 0) => ({
  status,
  served,
  answered: served,
  correct: status === "finished" ? 7 : null,
  score: status === "finished" ? 820 : null,
  startedAt: null,
  finishedAt: null,
});
const question = { position: 3, stem: "?", options: [], seconds: 25, servedAt: at(-5000), closesAt: at(20_000), myChoice: null };

describe("the daily challenge's phase", () => {
  test("unavailable, start, a question, between questions, finished, closed", () => {
    expect(dailyPhase(daily({ available: false, challengeId: null }), 0, T0).kind).toBe("unavailable");
    expect(dailyPhase(daily({}), 0, T0).kind).toBe("start");
    expect(dailyPhase(daily({ attempt: attempt("not_started") }), 0, T0).kind).toBe("start");
    expect(dailyPhase(daily({ attempt: attempt("in_progress", 3), question }), 0, T0).kind).toBe("question");
    expect(dailyPhase(daily({ attempt: attempt("in_progress", 3) }), 0, T0)).toEqual({ kind: "between", next: 4 });
    expect(dailyPhase(daily({ attempt: attempt("finished", 10) }), 0, T0).kind).toBe("finished");
    expect(dailyPhase(daily({ closesAt: at(-1) }), 0, T0).kind).toBe("closed");
  });

  test("reads at nextChangeAt: a question's end, or the day's change so Start never outlives its day", () => {
    const live = { offset: 0, localNow: T0, hidden: false, failures: 0 };
    expect(nextDailyPoll(daily({ question, nextChangeAt: at(22_000) }), live)).toBe(22_000 + POLL.margin);
    // Start (and unavailable) used to stop polling, so midnight never came: now they read at the day's change.
    expect(nextDailyPoll(daily({ nextChangeAt: at(3_600_000) }), live)).toBe(3_600_000 + POLL.margin);
    expect(nextDailyPoll(daily({ available: false, challengeId: null, nextChangeAt: at(60_000) }), live)).toBe(60_000 + POLL.margin);
    expect(nextDailyPoll(daily({ attempt: attempt("finished", 10), nextChangeAt: at(5000) }), live)).toBe(5000 + POLL.margin);
    expect(nextDailyPoll(daily({ question }), { ...live, hidden: true })).toBe(POLL.hidden);
    expect(nextDailyPoll(daily({}), { ...live, failures: 2 })).toBe(2000);
  });

  test("daily failures speak of the challenge, never of a battle", () => {
    expect(dailyFailure(new ApiError(404, "not_found"))).toEqual({ message: "Today's challenge isn't available any more.", reload: true });
    expect(dailyFailure(new ApiError(409, "round_closed")).message).toContain("this question");
    for (const code of ["not_found", "round_closed", "already_answered", "challenge_closed", "rate_limited"])
      expect(dailyFailure(new ApiError(409, code)).message).not.toMatch(/battle/i);
    expect(dailyFailure(new ApiError(409, "challenge_closed")).reload).toBe(true);
  });
});
