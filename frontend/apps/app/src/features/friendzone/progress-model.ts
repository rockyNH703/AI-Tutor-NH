/**
 * Phase 2's screen logic as plain data (progress-model.test.ts): XP and levels, a member's record, the daily
 * challenge's phase and its polling. The server awards and decides everything; this only words and schedules it.
 */

import { ApiError, describeError } from "@/lib/api/errors";
import type { PlayerStats, XpPage } from "./api";
import type { BattleResult } from "./battle-api";
import { msUntil } from "./clock";
import type { Daily, DailyQuestion } from "./daily-api";
import { POLL, backoff } from "./poll";

const XP_REASONS: Record<string, string> = {
  battle: "Battle completed",
  win: "Battle won",
  draw: "Battle drawn",
  daily: "Daily challenge completed",
  perfect: "Perfect daily challenge",
  streak_3: "3-day daily streak",
  streak_7: "7-day daily streak",
  streak_14: "14-day daily streak",
  streak_30: "30-day daily streak",
};

/** A readable reason; one the app doesn't know yet reads as plain XP rather than an internal key. */
export function xpReason(reason: string): string {
  return XP_REASONS[reason] ?? "XP earned";
}

export type LevelProgress = { level: number; into: number; span: number; fraction: number; text: string };

/** Where the XP stands inside the current level: "120 / 300 XP to level 3". */
export function levelProgress(page: Pick<XpPage, "xp" | "level" | "levelFloor" | "nextLevelAt">): LevelProgress {
  const span = Math.max(1, page.nextLevelAt - page.levelFloor);
  const into = Math.min(span, Math.max(0, page.xp - page.levelFloor));
  return { level: page.level, into, span, fraction: into / span, text: `${into} / ${span} XP to level ${page.level + 1}` };
}

export function record(stats: Pick<PlayerStats, "battles" | "wins" | "losses" | "draws">): string {
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  return `${plural(stats.battles, "battle", "battles")}: ${plural(stats.wins, "win", "wins")}, ${plural(stats.losses, "loss", "losses")}, ${plural(stats.draws, "draw", "draws")}`;
}

export function statsAccuracy(stats: Pick<PlayerStats, "correct" | "answered">): string {
  return stats.answered > 0 ? `${Math.round((stats.correct / stats.answered) * 100)}% (${stats.correct} of ${stats.answered})` : "No answers yet";
}

export function streakText(days: number): string {
  return days === 0 ? "No current streak" : `${days} ${days === 1 ? "day" : "days"} in a row`;
}

/** Rematch only after a battle decided by play: points, a draw or a forfeit, never a void. */
export function canRematch(status: string, result: BattleResult | null): boolean {
  return status === "completed" && (result === "points" || result === "draw" || result === "forfeit");
}

// ------------------------------------------------------------------ the daily challenge

export type DailyPhase =
  | { kind: "unavailable" }
  | { kind: "start" }
  | { kind: "question"; question: DailyQuestion }
  /** In progress, nothing open: the last one was answered and closed, or its time ran out. */
  | { kind: "between"; next: number }
  | { kind: "finished" }
  /** The day ended before you started. */
  | { kind: "closed" };

export function dailyPhase(daily: Daily, offset: number, localNow: number): DailyPhase {
  if (!daily.available || !daily.challengeId) return { kind: "unavailable" };
  const attempt = daily.attempt;
  if (attempt?.status === "finished") return { kind: "finished" };
  if (daily.question) return { kind: "question", question: daily.question };
  if (attempt?.status === "in_progress") return { kind: "between", next: attempt.served + 1 };
  const left = msUntil(daily.closesAt, offset, localNow);
  return left !== null && left <= 0 ? { kind: "closed" } : { kind: "start" };
}

export function dailyKey(phase: DailyPhase): string {
  return phase.kind === "question" ? `question:${phase.question.position}` : phase.kind;
}

/**
 * When to read the daily again: exactly at `nextChangeAt`. With a question open that is its window's end (a hidden
 * tab waits 10 s instead); otherwise the day's change (a new challenge, or the attempt's close), so the Start screen
 * never outlives its day. Failures back off as a battle's do.
 */
export function nextDailyPoll(daily: Daily, context: { offset: number; localNow: number; hidden: boolean; failures: number }): number | null {
  if (context.failures > 0) return backoff(context.failures);
  if (daily.question && context.hidden) return POLL.hidden;
  const until = msUntil(daily.nextChangeAt, context.offset, context.localNow);
  if (until === null) return daily.question ? POLL.roundOpen : null;
  return Math.min(MAX_TIMER, Math.max(POLL.floor, until + POLL.margin));
}

/** setTimeout's longest delay (about 24.8 days); a day's change is far below it, this only guards bad data. */
const MAX_TIMER = 2_147_483_647;

/** What a failed daily request says, in the daily's own words; `reload` when the challenge moved on. */
export function dailyFailure(error: unknown): { message: string; reload: boolean } {
  if (!(error instanceof ApiError)) return { message: describeError(error).message, reload: false };
  switch (error.code) {
    case "not_found":
      return { message: "Today's challenge isn't available any more.", reload: true };
    case "round_closed":
      return { message: "Time's up for this question; your answer arrived after it closed.", reload: true };
    case "already_answered":
    case "challenge_closed":
      return { message: describeError(error).message, reload: true };
    case "rate_limited":
      return { message: "Too many requests at once. Try again in a moment.", reload: false };
    default:
      return { message: describeError(error).message, reload: false };
  }
}

export function dailyScoreLine(daily: Daily): string {
  const attempt = daily.attempt;
  if (!attempt || attempt.status !== "finished") return "";
  return `${attempt.score ?? 0} points · ${attempt.correct ?? 0} of ${attempt.served} correct`;
}
