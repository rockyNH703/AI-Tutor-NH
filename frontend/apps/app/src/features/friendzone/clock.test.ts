import { describe, expect, test } from "bun:test";

import type { Battle } from "./battle-api";
import { clockOffset, fractionLeft, msUntil, secondsLeft, serverNow } from "./clock";
import { POLL, backoff, isTerminal, nextPollDelay } from "./poll";

const T0 = Date.parse("2026-11-24T10:00:00.000Z");
const at = (ms: number) => new Date(T0 + ms).toISOString();

describe("the server clock", () => {
  test("the offset is the server's time against the middle of the request", () => {
    // Device 5 s behind; the request took 200 ms each way.
    expect(clockOffset(at(0), T0 - 5000 - 200, T0 - 5000 + 200)).toBe(5000);
    expect(clockOffset("not a time", T0, T0)).toBe(0);
  });

  test("deadlines count on the server's time, whatever the device's clock says", () => {
    const offset = 5000; // the device is 5 s behind
    const deviceNow = T0 - 5000; // = server T0
    expect(serverNow(offset, deviceNow)).toBe(T0);
    expect(msUntil(at(12_000), offset, deviceNow)).toBe(12_000);
    expect(msUntil(null, offset, deviceNow)).toBeNull();
    expect(msUntil("nonsense", offset, deviceNow)).toBeNull();
  });

  test("seconds left round up, as a timer shows them, and never go below 0", () => {
    expect(secondsLeft(at(4200), 0, T0)).toBe(5);
    expect(secondsLeft(at(4000), 0, T0)).toBe(4);
    expect(secondsLeft(at(1), 0, T0)).toBe(1);
    expect(secondsLeft(at(-3000), 0, T0)).toBe(0);
    expect(secondsLeft(null, 0, T0)).toBe(0);
  });

  test("the bar's share of the window stays within 0 and 1", () => {
    expect(fractionLeft(at(15_000), 30, 0, T0)).toBe(0.5);
    expect(fractionLeft(at(45_000), 30, 0, T0)).toBe(1);
    expect(fractionLeft(at(-1), 30, 0, T0)).toBe(0);
    expect(fractionLeft(at(10_000), 0, 0, T0)).toBe(0);
  });
});

function battle(patch: Partial<Battle>): Battle {
  const player = { playerId: null, displayName: "A", initials: "A", role: "challenger" as const, ready: true, score: 0, correct: 0, answered: 0, winner: false };
  return {
    battleId: "33333333-3333-4333-8333-333333333333",
    status: "active",
    endReason: null,
    result: null,
    program: { slug: "ccna", title: "CCNA" },
    month: 1,
    topicId: null,
    difficulty: "mixed",
    length: 5,
    roundsClosed: 0,
    expiresAt: null,
    startsAt: null,
    endsAt: null,
    nextChangeAt: null,
    serverTime: at(0),
    me: player,
    opponent: { ...player, role: "opponent" },
    question: null,
    rounds: [],
    review: null,
    ...patch,
  };
}
const question = { round: 1, stem: "?", options: [], seconds: 30, opensAt: at(0), closesAt: at(30_000), myChoice: null };
const live = { offset: 0, localNow: T0, hidden: false, failures: 0 };

describe("the poll scheduler", () => {
  test("stops once the battle is over", () => {
    for (const status of ["completed", "declined", "expired", "cancelled"] as const) {
      expect(isTerminal(status)).toBe(true);
      expect(nextPollDelay(battle({ status }), live)).toBeNull();
    }
  });

  test("invited every 3 s, waiting every 2 s, an open round every second", () => {
    expect(nextPollDelay(battle({ status: "invited", nextChangeAt: at(600_000) }), live)).toBe(POLL.invited);
    expect(nextPollDelay(battle({ status: "waiting", nextChangeAt: at(180_000) }), live)).toBe(POLL.waiting);
    expect(nextPollDelay(battle({ question, nextChangeAt: at(30_000) }), live)).toBe(POLL.roundOpen);
  });

  test("reads exactly at the next change when it comes sooner (the round's close, an expiry)", () => {
    expect(nextPollDelay(battle({ question, nextChangeAt: at(400) }), live)).toBe(400 + POLL.margin);
    expect(nextPollDelay(battle({ status: "invited", nextChangeAt: at(1200) }), live)).toBe(1200 + POLL.margin);
    expect(nextPollDelay(battle({ question, nextChangeAt: at(-500) }), live)).toBe(POLL.floor); // overdue: soon, not now
  });

  test("the countdown and the reveal wait once, for the next change", () => {
    expect(nextPollDelay(battle({ nextChangeAt: at(3000) }), live)).toBe(3000 + POLL.margin);
    expect(nextPollDelay(battle({ roundsClosed: 1, nextChangeAt: at(2500) }), live)).toBe(2500 + POLL.margin);
    expect(nextPollDelay(battle({ nextChangeAt: null }), live)).toBe(POLL.roundOpen);
  });

  test("a hidden tab reads every 10 s", () => {
    expect(nextPollDelay(battle({ question, nextChangeAt: at(30_000) }), { ...live, hidden: true })).toBe(POLL.hidden);
  });

  test("failures back off 1, 2, 4, 8 s, then 10 s at most, before anything else", () => {
    expect([1, 2, 3, 4, 5, 9].map(backoff)).toEqual([1000, 2000, 4000, 8000, 10_000, 10_000]);
    expect(nextPollDelay(battle({ question }), { ...live, failures: 2, hidden: true })).toBe(2000);
  });

  test("uses the server's clock: a device 5 s behind still reads at the close", () => {
    const behind = { ...live, offset: 5000, localNow: T0 - 5000 };
    expect(nextPollDelay(battle({ question, nextChangeAt: at(300) }), behind)).toBe(300 + POLL.margin);
  });
});
