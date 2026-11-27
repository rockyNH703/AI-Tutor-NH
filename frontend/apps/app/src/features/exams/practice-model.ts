/**
 * A practice exam's screen logic as plain data (practice-model.test.ts): how answers ride in Friend Zone's exam store
 * (one `choice` per question, so several keys travel as one sorted "A,C"), the truthful save line, the copy of unsent
 * answers kept on this device (sessionStorage: UI state, keyed by exam), and which answers were not counted.
 * Nothing here scores: the server's result does.
 */

import type { ExamResponse, ResponseItem } from "@/features/friendzone/battle-api";
import { stateOf } from "@/features/friendzone/exam-model";
import type { ExamSnapshot } from "@/features/friendzone/exam-store";
import type { ExamResponseItem, ExamRules, ReviewItem, ReviewOutcome } from "@/lib/api/practice-exams";

const SEPARATOR = ",";

/** Several keys as the store's one choice (sorted, so the same set always compares equal); none is null. */
export function choiceOf(keys: readonly string[]): string | null {
  return keys.length ? [...keys].sort().join(SEPARATOR) : null;
}

export function keysOf(choice: string | null | undefined): string[] {
  return choice ? choice.split(SEPARATOR) : [];
}

export function toggleKey(keys: readonly string[], key: string): string[] {
  return (keys.includes(key) ? keys.filter((item) => item !== key) : [...keys, key]).sort();
}

/**
 * The server's responses as store rows (round = index + 1). The exam has one `rev`, so every row carries it; a row
 * never touched (not visited, no answer, no mark) is left out, so its question stays "Not visited".
 */
export function rowsFrom(responses: readonly ExamResponseItem[], rev: number): ExamResponse[] {
  return responses
    .filter((row) => row.visited || row.keys.length > 0 || row.marked)
    .map((row) => ({ round: row.index + 1, choice: choiceOf(row.keys), marked: row.marked, rev }));
}

/** The store's queue on the wire: a set as its keys, a visit as an empty answer that was seen. */
export function toWire(items: readonly ResponseItem[]): ExamResponseItem[] {
  return items.map((item) =>
    item.op === "set"
      ? { index: item.round - 1, keys: keysOf(item.choice), marked: item.marked, visited: true }
      : { index: item.round - 1, keys: [], marked: false, visited: true },
  );
}

export type SaveLine = { kind: "saved" | "saving" | "unsaved" | "offline"; text: string };

const onDevice = (count: number) => (count === 1 ? "1 answer is" : `${count} answers are`);

/** What the status line says. Never "saved" for an answer the server doesn't have. */
export function saveLine(snapshot: Pick<ExamSnapshot, "intents" | "failed" | "inFlight" | "offline">, online: boolean): SaveLine {
  const pending = snapshot.intents.size;
  if (snapshot.offline || !online) {
    return pending > 0
      ? {
          kind: "offline",
          text: `You're offline: ${onDevice(pending)} only on this device and will be sent when you reconnect. Answers not sent before time runs out can't be counted.`,
        }
      : { kind: "offline", text: "You're offline. Every answer so far is saved." };
  }
  if (snapshot.failed.size > 0) return { kind: "unsaved", text: `${pending === 1 ? "1 answer" : `${pending} answers`} not saved.` };
  if (snapshot.inFlight || pending > 0) return { kind: "saving", text: "Saving…" };
  return { kind: "saved", text: "All answers saved" };
}

/** An unsent answer as kept on this device. */
export type MirrorItem = { index: number; keys: string[]; marked: boolean };
export type Mirror = { rev: number; items: MirrorItem[] };
type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export const mirrorKey = (examId: string) => `netsecai.exam.${examId}`;

const isMirrorItem = (value: unknown): value is MirrorItem => {
  const item = value as MirrorItem | null;
  return (
    typeof item === "object" &&
    item !== null &&
    Number.isInteger(item.index) &&
    item.index >= 0 &&
    typeof item.marked === "boolean" &&
    Array.isArray(item.keys) &&
    item.keys.every((key) => typeof key === "string")
  );
};

/** What this device holds for the exam; anything unreadable (private mode, a bad value) reads as nothing. */
export function readMirror(storage: Store | null, examId: string): Mirror {
  try {
    const parsed = JSON.parse(storage?.getItem(mirrorKey(examId)) ?? "null") as { rev?: unknown; items?: unknown } | null;
    if (!parsed || !Array.isArray(parsed.items)) return { rev: 0, items: [] };
    const items = parsed.items.filter(isMirrorItem).map(({ index, keys, marked }) => ({ index, keys, marked }));
    return { rev: typeof parsed.rev === "number" ? parsed.rev : 0, items };
  } catch {
    return { rev: 0, items: [] };
  }
}

export function writeMirror(storage: Store | null, examId: string, rev: number, items: readonly MirrorItem[]): void {
  try {
    if (items.length) storage?.setItem(mirrorKey(examId), JSON.stringify({ rev, items }));
    else storage?.removeItem(mirrorKey(examId));
  } catch {
    // storage full or blocked: the answers are still in the page's queue, only a reload can't bring them back
  }
}

export function clearMirror(storage: Store | null, examId: string): void {
  writeMirror(storage, examId, 0, []);
}

export function sessionStore(): Store | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export const NOT_COUNTED = "Not counted (not saved before time ran out)";

export const OUTCOME_LABEL: Record<ReviewOutcome, string> = {
  correct: "Correct",
  incorrect: "Incorrect",
  unanswered: "Unanswered",
  void: "Not graded",
};

const sameKeys = (a: readonly string[], b: readonly string[]) => choiceOf(a) === choiceOf(b);

/**
 * The questions whose last answer never counted: the server said so (`lateItems`), or this device still held an
 * answer the result doesn't have. A clear or a mark only (no keys) is not an answer to count.
 */
export function notCounted(review: readonly ReviewItem[], mirrored: readonly MirrorItem[], lateItems: readonly number[]): number[] {
  const byIndex = new Map(review.map((item) => [item.index, item]));
  const late = new Set(lateItems);
  for (const item of mirrored) {
    const counted = byIndex.get(item.index);
    if (item.keys.length > 0 && (!counted || !sameKeys(counted.keys, item.keys))) late.add(item.index);
  }
  return [...late].sort((a, b) => a - b);
}

/** "1 mark · no negative marking", from the exam's own rules. */
export function rulesText(rules: ExamRules): string {
  const marks = `${rules.marksPerQuestion} ${rules.marksPerQuestion === 1 ? "mark" : "marks"}`;
  const negative = rules.negativeMarking > 0 ? `−${rules.negativeMarking} for a wrong answer` : "no negative marking";
  return `${marks} · ${negative}`;
}

/** "Question 3 of 10 · 1 mark · no negative marking": the question heading's whole text. */
export function questionMeta(number: number, total: number, rules: ExamRules): string {
  return `Question ${number} of ${total} · ${rulesText(rules)}`;
}

/** "3, 7 and 9", for the questions a summary lists. */
export function numberList(numbers: readonly number[]): string {
  if (numbers.length <= 1) return numbers.join("");
  return `${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
}

/** What End Test lists, by question number: no answer yet, marked for review, and answers not on the server. */
export function endTestSummary(snapshot: Pick<ExamSnapshot, "rows" | "intents" | "visits">, total: number) {
  const unanswered: number[] = [];
  const marked: number[] = [];
  for (let round = 1; round <= total; round += 1) {
    const state = stateOf(snapshot, round);
    if (state !== "answered" && state !== "answered-marked") unanswered.push(round);
    if (state === "marked" || state === "answered-marked") marked.push(round);
  }
  return { unanswered, marked, unsent: [...snapshot.intents.keys()].sort((a, b) => a - b) };
}
