/**
 * An exam battle's screen logic as plain data (exam-model.test.ts): the palette's states and counts, the countdown,
 * the warnings, the answer shortcuts' guard, and the words for saving, submitting and the result. Nothing here scores
 * or decides: the server's answer does.
 */

import type { Battle, BattlePlayer, BattleStatus, ExamResponse } from "./battle-api";
import type { ExamSnapshot, Intent } from "./exam-store";

export type PaletteState = "not-visited" | "not-answered" | "answered" | "marked" | "answered-marked";

/** In the legend's order. Each state has its own shape and glyph (exam-palette.tsx); colour comes second. */
export const PALETTE_STATES: readonly PaletteState[] = ["answered", "not-answered", "marked", "answered-marked", "not-visited"];

export const PALETTE_LABEL: Record<PaletteState, string> = {
  answered: "Answered",
  "not-answered": "Not answered",
  marked: "Marked for review",
  "answered-marked": "Answered and marked for review",
  "not-visited": "Not visited",
};

/** What a question is, from the server's row and the student's unsaved change (which wins: it is shown at once). */
export function paletteState(row: Pick<ExamResponse, "choice" | "marked"> | undefined, intent: Intent | undefined, visitPending = false): PaletteState {
  const shown = intent ?? row;
  if (!shown) return visitPending ? "not-answered" : "not-visited";
  if (shown.choice) return shown.marked ? "answered-marked" : "answered";
  return shown.marked ? "marked" : "not-answered";
}

export type ExamCounts = Record<PaletteState, number> & { notSaved: number };

export function stateOf(snapshot: Pick<ExamSnapshot, "rows" | "intents" | "visits">, round: number): PaletteState {
  return paletteState(snapshot.rows.get(round), snapshot.intents.get(round), snapshot.visits.has(round));
}

export function examCounts(snapshot: Pick<ExamSnapshot, "rows" | "intents" | "visits">, total: number): ExamCounts {
  const counts: ExamCounts = { answered: 0, "not-answered": 0, marked: 0, "answered-marked": 0, "not-visited": 0, notSaved: snapshot.intents.size };
  for (let round = 1; round <= total; round += 1) counts[stateOf(snapshot, round)] += 1;
  return counts;
}

/** A pill's accessible name: its number, its state, and Not saved when its change hasn't reached the server. */
export function pillLabel(round: number, state: PaletteState, unsaved: boolean): string {
  return `Question ${round}, ${PALETTE_LABEL[state].toLowerCase()}${unsaved ? ", not saved" : ""}`;
}

/** The four numbers a summary shows; they add up to the paper (answered includes answered and marked, D6). */
export function summaryCounts(counts: ExamCounts) {
  return {
    answered: counts.answered + counts["answered-marked"],
    notAnswered: counts["not-answered"],
    marked: counts.marked,
    notVisited: counts["not-visited"],
  };
}

export function summaryText(counts: ExamCounts): string {
  const { answered, notAnswered, marked, notVisited } = summaryCounts(counts);
  return `Answered ${answered} (${counts["answered-marked"]} of them marked for review), not answered ${notAnswered}, marked for review without an answer ${marked}, not visited ${notVisited}.`;
}

/** m:ss, for the countdown and the time taken. */
export function clockText(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** "4 minutes 30 seconds", for words read aloud. */
export function durationWords(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  const parts = [minutes > 0 && `${minutes} ${minutes === 1 ? "minute" : "minutes"}`, (rest > 0 || minutes === 0) && `${rest} ${rest === 1 ? "second" : "seconds"}`];
  return parts.filter(Boolean).join(" ");
}

export type Countdown = { endsAt: string; left: number };

/** The countdown never goes up within one exam, even when a new read moves the clock offset (plan section 6). */
export function countdownLeft(previous: Countdown | null, endsAt: string, raw: number): number {
  return previous && previous.endsAt === endsAt ? Math.min(previous.left, raw) : raw;
}

/** The warning level at this many seconds left: 5 minutes, then 1 minute. */
export function warnLevel(left: number): 5 | 1 | null {
  if (left <= 60) return 1;
  if (left <= 300) return 5;
  return null;
}

export type ShortcutContext = {
  key: string;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  repeat: boolean;
  /** The element the key was pressed in. */
  target: { closest(selector: string): unknown } | null;
  /** That element is inside the exam region. */
  inRegion: boolean;
  /** A sheet or dialog is open. */
  layerOpen: boolean;
  locked: boolean;
};

export const PALETTE_ATTRIBUTE = "data-exam-palette";
const TEXT_ENTRY = "textarea, select, [contenteditable]:not([contenteditable='false']), input:not([type=radio]):not([type=checkbox])";

/** Whether 1-8 / A-H may pick an option now (plan section 6, Keyboard). A focused radio is allowed. */
export function shortcutAllowed(context: ShortcutContext): boolean {
  if (!context.inRegion || context.layerOpen || context.locked || context.repeat) return false;
  if (context.altKey || context.ctrlKey || context.metaKey || context.shiftKey) return false;
  const target = context.target;
  if (!target) return false;
  return !target.closest(`[${PALETTE_ATTRIBUTE}]`) && !target.closest(TEXT_ENTRY);
}

export type SaveStatus = { kind: "saved" | "saving" | "unsaved" | "offline"; text: string };

export function saveStatus(snapshot: Pick<ExamSnapshot, "intents" | "failed" | "inFlight" | "offline">): SaveStatus {
  const pending = snapshot.intents.size;
  if (snapshot.offline) return { kind: "offline", text: pending > 0 ? `Offline: ${pending} not saved` : "Offline" };
  if (snapshot.failed.size > 0) return { kind: "unsaved", text: `${pending} not saved` };
  if (snapshot.inFlight || pending > 0) return { kind: "saving", text: "Saving…" };
  return { kind: "saved", text: "Saved" };
}

/** What the live region says when the save status changes (Saving… is not said). */
export function saveAnnouncement(status: SaveStatus): string | null {
  if (status.kind === "saving") return null;
  if (status.kind === "saved") return "All answers saved.";
  return `${status.text}.`;
}

function answers(count: number): string {
  return count === 1 ? "1 answer wasn't" : `${count} answers weren't`;
}

/**
 * After a 409, the battle is read again and the words come from its new state, never naming why (I21). Null: nothing
 * to say (the screen itself shows the new state, as the waiting screen does after a submit in another tab).
 */
export function staleMessage(battle: StaleBattle, unsaved: number): string | null {
  if (battle.status === "active") return null;
  const played = battle.status === "completed" && (battle.result !== "void" || battle.endReason === "absent");
  if (!played) return "This battle has ended.";
  if (unsaved === 0) return null;
  // "time ran out" only when I was submitted at the deadline; a submit from another tab finished it otherwise
  return `${answers(unsaved)} saved before ${ranOut(battle) ? "time ran out" : "the battle finished"}.`;
}

type StaleBattle = Pick<Battle, "status" | "result" | "endReason" | "exam" | "startsAt" | "endsAt"> & { me: Pick<BattlePlayer, "timeTakenSeconds"> };

function ranOut(battle: StaleBattle): boolean {
  return timeTakenText(battle.me, battle) === "Time ran out";
}

/**
 * What a battle read says, if anything: after a re-read asked for by a 409 (`asked`), or a submit the server didn't
 * apply (`submitApplied: false`, as when time ran out first), the words come from the new state (staleMessage).
 */
export function readMessage(battle: StaleBattle & Pick<Battle, "submitApplied">, unsaved: number, asked: boolean): string | null {
  return asked || battle.submitApplied === false ? staleMessage(battle, unsaved) : null;
}

/** Marks with a real minus sign: "−3". */
export function marksText(marks: number): string {
  return marks < 0 ? `−${Math.abs(marks)}` : String(marks);
}

/** Time taken, m:ss, or "Time ran out" when the submit came at the deadline. */
export function timeTakenText(player: Pick<BattlePlayer, "timeTakenSeconds">, battle: Pick<Battle, "startsAt" | "endsAt">): string {
  const taken = player.timeTakenSeconds;
  if (taken === null || taken === undefined) return "–";
  const start = Date.parse(battle.startsAt ?? "");
  const end = Date.parse(battle.endsAt ?? "");
  if (!Number.isNaN(start) && !Number.isNaN(end) && taken >= Math.floor((end - start) / 1000)) return "Time ran out";
  return clockText(taken);
}

const XP_REASON: Record<string, string> = { battle: "Battle played", win: "Win", draw: "Draw" };

export function xpLabel(reason: string): string {
  return XP_REASON[reason] ?? "Battle XP";
}

export const XP_RULE = "XP needs answers to at least half the questions, and counts for up to 5 battles a day (2 with the same friend).";

/** The questions that count: all but the voided ones (rounds carry `voided` once the exam is completed). */
export function countedQuestions(battle: Pick<Battle, "length" | "rounds">): number {
  return battle.rounds.length > 0 ? battle.rounds.filter((round) => !round.voided).length : battle.length;
}

/** True exactly once: the first time a battle seen open turns completed (then the streak and the bell refresh). */
export function completionTracker(): (status: BattleStatus) => boolean {
  let sawOpen = false;
  let fired = false;
  return (status) => {
    if (status !== "completed") {
      if (status === "active") sawOpen = true;
      return false;
    }
    if (!sawOpen || fired) return false;
    fired = true;
    return true;
  };
}

/** The question to show first: `&q=` when it names one, else the lowest unanswered, else the first. */
export function firstQuestion(param: string | null, total: number, answered: (round: number) => boolean): number {
  const asked = Number(param);
  if (Number.isInteger(asked) && asked >= 1 && asked <= total) return asked;
  for (let round = 1; round <= total; round += 1) if (!answered(round)) return round;
  return 1;
}

export function questionAnnouncement(round: number, total: number, state: PaletteState): string {
  return `Question ${round} of ${total}, ${PALETTE_LABEL[state].toLowerCase()}.`;
}

/**
 * Why the Questions sheet or the submit dialog closed. Their openers are plain buttons, not Radix triggers, so Radix has
 * nothing to give focus back to and the exam chooses: a pill goes to the question's heading, a dismissal (Escape, the
 * overlay, Close, Keep answering) back to the button that opened it, and the deadline or a submit to nothing here (the
 * time-up or waiting heading takes focus itself).
 */
export type LayerClose = "pill" | "dismiss" | "deadline" | "submit";

export function focusAfterClose(cause: LayerClose): "heading" | "opener" | null {
  if (cause === "pill") return "heading";
  return cause === "dismiss" ? "opener" : null;
}
