/**
 * A battle's screen logic as plain data (battle-model.test.ts): which phase to show, the words for every state and
 * ending, what is announced, the answer shortcuts. Nothing here scores or decides: the server's answer does.
 */

import { ApiError, describeError } from "@/lib/api/errors";
import { isUuid } from "@/lib/api/routes";
import type { Uuid } from "@/lib/api/types";
import type { Battle, BattleOption, BattlePlayer, BattleQuestion, BattleSummary, Difficulty, Exam } from "./battle-api";
import { marksText } from "./exam-model";

export type Phase =
  /** A friend challenged you: Accept or Decline. */
  | { kind: "invited-in" }
  /** You challenged a friend: waiting for their accept. */
  | { kind: "invited-out" }
  /** Your friend accepted: press Ready. */
  | { kind: "ready" }
  /** You accepted: waiting for the challenger's Ready. */
  | { kind: "waiting-start" }
  /** The 3 seconds before the paper arrives. */
  | { kind: "countdown" }
  /** The exam is running and I haven't submitted. */
  | { kind: "exam"; exam: Exam }
  /** I submitted: waiting for the other player or the deadline. */
  | { kind: "submitted"; exam: Exam }
  | { kind: "result" }
  /** Declined, expired or cancelled: never played. */
  | { kind: "ended" };

export function battlePhase(battle: Battle): Phase {
  const challenger = battle.me.role === "challenger";
  switch (battle.status) {
    case "invited":
      return challenger ? { kind: "invited-out" } : { kind: "invited-in" };
    case "waiting":
      return challenger ? { kind: "ready" } : { kind: "waiting-start" };
    case "active": {
      // A round battle can't be open any more: the server ends any it finds ("upgraded").
      if (battle.format !== "exam") return { kind: "ended" };
      if (!battle.exam) return { kind: "countdown" };
      return battle.exam.submittedAt ? { kind: "submitted", exam: battle.exam } : { kind: "exam", exam: battle.exam };
    }
    case "completed":
      return { kind: "result" };
    default:
      return { kind: "ended" };
  }
}

/** A key for "something new is on screen": a new phase. */
export function phaseKey(phase: Phase): string {
  return phase.kind;
}

export function nameOf(player: Pick<BattlePlayer, "displayName">): string {
  return player.displayName ?? "A former member";
}

export const DIFFICULTY: Record<Difficulty, string> = { mixed: "Mixed", easy: "Easy", medium: "Medium", hard: "Hard" };

export function difficultyLabel(value: string): string {
  return DIFFICULTY[value as Difficulty] ?? value;
}

export function battleMeta(battle: Pick<Battle, "program" | "month" | "difficulty" | "length">): string {
  return `${battle.program.title} · Month ${battle.month} · ${difficultyLabel(battle.difficulty)} · ${battle.length} questions`;
}

export function accuracy(player: Pick<BattlePlayer, "correct" | "answered">): string {
  return player.answered > 0 ? `${Math.round((player.correct / player.answered) * 100)}%` : "–";
}

export type Outcome = { headline: string; detail: string };

/** How an exam battle ended, for you (marks: +4 correct, −1 incorrect). */
function examOutcome(battle: Pick<Battle, "result" | "endReason" | "me" | "opponent">): Outcome {
  const final = `Final marks ${marksText(battle.me.score)} to ${marksText(battle.opponent.score)}.`;
  switch (battle.result) {
    case "void":
      if (battle.endReason === "absent") return { headline: "No result", detail: "Neither of you answered a question." };
      return { headline: "No result", detail: "This battle ended early, so it doesn't count." };
    case "forfeit":
      return battle.me.winner
        ? { headline: "You won", detail: "The other member left the battle." }
        : { headline: "You lost", detail: "This battle was decided by forfeit." };
    case "draw":
      return { headline: "Draw", detail: "Same marks, the same number of incorrect answers and the same submit time." };
    default:
      if (battle.me.winner) return { headline: "You won", detail: final };
      if (battle.opponent.winner) return { headline: "You lost", detail: final };
      return { headline: "Finished", detail: final };
  }
}

/** How a battle ended, for you. Blocking, unfriending and suspension all read the same: "ended early". */
export function outcome(battle: Pick<Battle, "status" | "result" | "endReason" | "me" | "opponent" | "format">): Outcome {
  const them = nameOf(battle.opponent);
  const challenger = battle.me.role === "challenger";
  if (battle.status === "completed" && battle.format === "exam") return examOutcome(battle);
  if (battle.status === "completed") {
    switch (battle.result) {
      case "void":
        if (battle.endReason === "absent") return { headline: "No result", detail: "Both of you missed three rounds in a row." };
        if (battle.endReason === "deleted") return { headline: "No result", detail: "The other member left before answering." };
        return { headline: "No result", detail: "This battle ended early, so it doesn't count." };
      case "forfeit":
        if (battle.me.winner)
          return {
            headline: "You won",
            detail: battle.endReason === "deleted" ? "The other member left the battle." : `${them} missed three rounds in a row.`,
          };
        return { headline: "You lost", detail: "You missed three rounds in a row, so the battle ended." };
      case "draw":
        return { headline: "Draw", detail: "You finished level: same score, same correct answers, same time." };
      default:
        if (battle.me.winner) return { headline: "You won", detail: `Final score ${battle.me.score} to ${battle.opponent.score}.` };
        if (battle.opponent.winner) return { headline: "You lost", detail: `Final score ${battle.me.score} to ${battle.opponent.score}.` };
        return { headline: "Finished", detail: `Final score ${battle.me.score} to ${battle.opponent.score}.` };
    }
  }
  if (battle.status === "declined")
    return challenger
      ? { headline: "Not accepted", detail: `${them} couldn't play this time.` }
      : { headline: "Declined", detail: "You declined this challenge." };
  if (battle.status === "expired") return { headline: "Expired", detail: "This battle wasn't started in time." };
  if (battle.endReason === "not_enough_questions")
    return { headline: "Couldn't start", detail: "No checked questions for this choice yet, so the battle couldn't start." };
  if (battle.endReason === "cancelled")
    return challenger
      ? { headline: "Cancelled", detail: "You cancelled this challenge." }
      : { headline: "Cancelled", detail: `${them} cancelled this challenge.` };
  return { headline: "No longer available", detail: "This battle can't go ahead." };
}

/** One line for a battle in a list. */
export function summaryLine(summary: BattleSummary): string {
  const where = `Month ${summary.month} · ${summary.length} questions`;
  const challenger = summary.role === "challenger";
  switch (summary.status) {
    case "invited":
      return challenger ? `Waiting for them to accept · ${where}` : `Challenged you · ${where}`;
    case "waiting":
      return challenger ? `Accepted: press Ready to start · ${where}` : `Waiting for the challenger to start · ${where}`;
    case "active":
      return `In progress · ${where}`;
    case "completed": {
      if (summary.result === "void") return `No result · ${where}`;
      if (summary.result === "draw") return `Draw · ${where}`;
      const lost = summary.opponent.winner;
      return `${lost ? "Lost" : "Won"}${summary.result === "forfeit" ? " by forfeit" : ""} · ${where}`;
    }
    default:
      return `Not played · ${where}`;
  }
}

export function roundAnnouncement(question: Pick<BattleQuestion, "round" | "seconds">, length: number): string {
  return `Question ${question.round} of ${length}, ${question.seconds} seconds.`;
}

export function lockedText(choice: string): string {
  return `Answer locked: ${choice}`;
}

/** The warning said (and shown) as time runs out: at 10 seconds and at 5. */
export function warningFor(secondsLeft: number): 10 | 5 | null {
  if (secondsLeft <= 5) return 5;
  if (secondsLeft <= 10) return 10;
  return null;
}

/** An answer shortcut: 1-8 or A-H, for an option this question has; anything else is null. */
export function shortcutChoice(key: string, options: readonly BattleOption[]): string | null {
  if (/^[1-8]$/.test(key)) return options[Number(key) - 1]?.key ?? null;
  if (/^[a-h]$/i.test(key)) {
    const letter = key.toUpperCase();
    return options.some((option) => option.key === letter) ? letter : null;
  }
  return null;
}

/** What a failed battle request says; `reload` when the battle's state is out of date. */
export function battleFailure(error: unknown, action: "create" | "move" | "answer"): { message: string; reload: boolean } {
  if (!(error instanceof ApiError)) return { message: describeError(error).message, reload: false };
  switch (error.code) {
    case "not_found":
      return {
        message: action === "create" ? "This friend can't be challenged in this program." : "This battle isn't available any more.",
        reload: true,
      };
    case "rate_limited":
      return {
        message: action === "create" ? "You've started the most battles allowed today. Try again tomorrow." : "Too many requests at once. Try again in a moment.",
        reload: false,
      };
    case "battle_exists":
    case "battle_not_open":
    case "round_closed":
    case "already_answered":
      return { message: describeError(error).message, reload: true };
    default:
      return { message: describeError(error).message, reload: false };
  }
}

/** 409 `battle_exists`: the pair's open battle, from the error's `current` (the BFF passes exactly `{battleId}`). */
export function existingBattle(error: unknown): Uuid | null {
  if (!(error instanceof ApiError) || error.code !== "battle_exists") return null;
  const id = (error.current as { battleId?: unknown } | undefined)?.battleId;
  return typeof id === "string" && isUuid(id) ? id : null;
}
