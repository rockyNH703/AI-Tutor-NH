/**
 * A practice exam's save queue: Friend Zone's `createExamStore` (latest change per question, one request at a time,
 * backoff on network errors, waiting for "online" when offline), with a `send` that speaks this exam's API
 * (exam-saver.test.ts):
 *
 * - every save goes on the exam's one `rev` (compare-and-set), and the new `rev` is kept;
 * - a 409 is rebased once: the exam is read again and the same answers go on its current `rev`; a second 409, or an
 *   exam that is no longer in progress, is the store's conflict (Not saved, and the screen re-reads);
 * - every answer not yet on the server is mirrored to sessionStorage, so a reload replays it and the result can say
 *   which answers never arrived. Finishing stops the mirror without clearing it: the results page clears it once it
 *   has shown its notice.
 */

import type { ResponseItem, ResponsesResult } from "@/features/friendzone/battle-api";
import { type ExamStore, type StoreDeps, createExamStore } from "@/features/friendzone/exam-store";
import { ApiError } from "@/lib/api/errors";
import type { ExamResponseItem, PracticeExam, SaveResult } from "@/lib/api/practice-exams";
import type { Uuid } from "@/lib/api/types";
import { type MirrorItem, choiceOf, clearMirror, keysOf, readMirror, rowsFrom, toWire, writeMirror } from "./practice-model";

export type SaverDeps = Omit<StoreDeps, "send"> & {
  examId: Uuid;
  rev: number;
  put: (baseRev: number, items: ExamResponseItem[], signal: AbortSignal) => Promise<SaveResult>;
  /** The exam as the server has it now, after a 409. */
  reread: () => Promise<Pick<PracticeExam, "rev" | "status">>;
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null;
};

export type ExamSaver = {
  store: ExamStore;
  rev(): number;
  /** The server's answers from a read of the exam. */
  adopt(exam: Pick<PracticeExam, "rev" | "responses">): void;
  /** Put the answers this device kept back in the queue (after a reload); returns how many differ from the server's. */
  replay(): number;
  /** Everything not yet on the server, as the submit's items. */
  wire(): ExamResponseItem[];
  /** What this device holds right now. */
  mirrored(): MirrorItem[];
  /** No more saves or mirror writes; the mirror is kept for the results' notice. */
  finish(): void;
  clearMirror(): void;
};

export function createExamSaver({ examId, rev: firstRev, put, reread, storage, ...storeDeps }: SaverDeps): ExamSaver {
  let rev = firstRev;
  // read before anything is mirrored: the first adopt rewrites the mirror from the (still empty) queue
  const kept = readMirror(storage, examId).items;

  async function send(items: ResponseItem[], signal: AbortSignal): Promise<ResponsesResult> {
    const body = toWire(items);
    let saved: SaveResult;
    try {
      saved = await put(rev, body, signal);
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 409)) throw error;
      const now = await reread();
      if (now.status !== "in_progress") throw error; // submitted, or past the cutoff: nothing to rebase onto
      rev = now.rev;
      saved = await put(rev, body, signal); // a second 409 goes to the store: Not saved, re-read
    }
    rev = Math.max(rev, saved.rev);
    const rows = items.map((item) =>
      item.op === "set"
        ? { round: item.round, choice: item.choice, marked: item.marked, rev: saved.rev }
        : { round: item.round, choice: null, marked: false, rev: saved.rev },
    );
    return {
      serverTime: saved.serverTime,
      status: "active",
      submittedAt: null,
      opponentSubmitted: false,
      responses: rows,
      items: items.map((item) => ({ round: item.round, applied: true, rev: saved.rev })),
    };
  }

  const store = createExamStore({ send, ...storeDeps });
  const mirrorNow = () =>
    writeMirror(
      storage,
      examId,
      rev,
      [...store.getSnapshot().intents].map(([round, intent]) => ({ index: round - 1, keys: keysOf(intent.choice), marked: intent.marked })),
    );
  let stopMirror: (() => void) | null = store.subscribe(mirrorNow);

  return {
    store,
    rev: () => rev,
    adopt(exam) {
      rev = Math.max(rev, exam.rev);
      store.adopt(rowsFrom(exam.responses, exam.rev));
    },
    replay() {
      let changed = 0;
      const rows = store.getSnapshot().rows;
      for (const item of kept) {
        const row = rows.get(item.index + 1);
        const choice = choiceOf(item.keys);
        if (row && row.choice === choice && row.marked === item.marked) continue;
        changed += 1;
        store.set(item.index + 1, { choice, marked: item.marked });
      }
      return changed;
    },
    wire: () => toWire(store.queue()),
    mirrored: () => readMirror(storage, examId).items,
    finish() {
      stopMirror?.();
      stopMirror = null;
      store.finish();
    },
    clearMirror: () => clearMirror(storage, examId),
  };
}
