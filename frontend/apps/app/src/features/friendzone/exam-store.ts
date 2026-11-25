/**
 * An exam's save queue (plan sections 4 and 6, D3). Every pick, Clear and Mark is saved at once, with no debounce:
 * the screen changes first, then the change goes to the server. One request per battle is in flight at a time; later
 * changes to the same question replace the waiting one (only the latest intent is sent), and visits ride along.
 *
 * The server owns each question's `rev`: a change is sent on the `rev` it was based on (compare-and-set), so a late,
 * retried or duplicated request can never overwrite a newer pick. A question whose row comes back equal to what the
 * student wants is saved, whatever `applied` says. A conflict is rebased once (the same intent on the new `rev`); a
 * second one marks the question Not saved, and it is retried on the next flush.
 *
 * The store lives in a module-level map keyed by battle, so the queue survives the battle view unmounting (an in-page
 * exit). Saves never touch the view's `busy`.
 */

import { ApiError } from "@/lib/api/errors";
import type { Uuid } from "@/lib/api/types";
import { type ExamQuestion, type ExamResponse, type ResponseItem, type ResponsesResult, saveResponses } from "./battle-api";
import { backoff } from "./poll";

export type Intent = { choice: string | null; marked: boolean };

export type ExamSnapshot = {
  /** What the server holds, per round (the highest `rev` seen). */
  rows: ReadonlyMap<number, ExamResponse>;
  /** What the student wants and the server doesn't have yet, per round: shown at once, the Not saved overlay. */
  intents: ReadonlyMap<number, Intent>;
  /** Rounds seen and not yet recorded. */
  visits: ReadonlySet<number>;
  /** Rounds whose save failed (an error, a timeout, or a second conflict): retried. */
  failed: ReadonlySet<number>;
  inFlight: boolean;
  offline: boolean;
  /** Goes up when the server says the battle moved on (a 409, or an answer that is no longer active): read it again. */
  stale: number;
  /** The battle is gone for this account (404). */
  gone: boolean;
};

export type ExamStore = {
  subscribe(listener: () => void): () => void;
  getSnapshot(): ExamSnapshot;
  /**
   * The server's rows from a read of the battle (older rows never replace newer ones). With the paper, a change whose
   * choice is no longer among its question's options is dropped, so it can't keep the batch refused (409 `invalid_choice`).
   */
  adopt(rows: readonly ExamResponse[], questions?: readonly Pick<ExamQuestion, "round" | "options">[]): void;
  set(round: number, intent: Intent): void;
  visit(round: number): void;
  flush(): void;
  /** No more saves (the deadline, a submit): a request in flight still lands. */
  lock(): void;
  /** Resolves once no request is in flight. */
  settled(): Promise<void>;
  /** Everything still unsaved, for a submit (applied there as final values). */
  queue(): ResponseItem[];
  /** The battle no longer takes changes: stop, and say how many changes were still unsaved. */
  finish(): number;
};

export const SAVE_TIMEOUT_MS = 8000;
/** The deadline's own submit goes this long after the local deadline (plan section 2). */
export const AUTO_SUBMIT_DELAY_MS = 500;

export type StoreDeps = {
  send: (items: ResponseItem[], signal: AbortSignal) => Promise<ResponsesResult>;
  timeout?: () => AbortSignal;
  schedule?: (run: () => void, ms: number) => unknown;
  cancel?: (handle: unknown) => void;
  isOffline?: () => boolean;
  /** Listen for the browser coming back online; returns the unsubscribe. */
  onOnline?: (listener: () => void) => () => void;
};

const same = (row: ExamResponse | undefined, intent: Intent) => row !== undefined && row.choice === intent.choice && row.marked === intent.marked;

export function createExamStore({
  send,
  timeout = () => AbortSignal.timeout(SAVE_TIMEOUT_MS),
  schedule = (run, ms) => setTimeout(run, ms),
  cancel = (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  isOffline = () => typeof navigator !== "undefined" && navigator.onLine === false,
  onOnline = (listener) => {
    if (typeof window === "undefined") return () => {};
    window.addEventListener("online", listener);
    return () => window.removeEventListener("online", listener);
  },
}: StoreDeps): ExamStore {
  const rows = new Map<number, ExamResponse>();
  const intents = new Map<number, Intent>();
  const visits = new Set<number>();
  const failed = new Set<number>();
  const conflicts = new Map<number, number>();
  const listeners = new Set<() => void>();
  let waiters: (() => void)[] = [];
  let inFlight = false;
  let offline = false;
  let locked = false;
  let finished = false;
  let stale = 0;
  let gone = false;
  let failures = 0;
  let retry: unknown = null;
  let snapshot: ExamSnapshot = build();

  function build(): ExamSnapshot {
    return { rows: new Map(rows), intents: new Map(intents), visits: new Set(visits), failed: new Set(failed), inFlight, offline, stale, gone };
  }
  function notify() {
    snapshot = build();
    for (const listener of listeners) listener();
  }
  function merge(row: ExamResponse) {
    const known = rows.get(row.round);
    if (!known || row.rev >= known.rev) rows.set(row.round, row);
    if (rows.has(row.round)) visits.delete(row.round);
  }
  function clearRetry() {
    if (retry !== null) cancel(retry);
    retry = null;
  }
  function later() {
    clearRetry();
    if (locked) return;
    retry = schedule(() => {
      retry = null;
      flush();
    }, backoff(Math.max(1, failures)));
  }

  function items(): ResponseItem[] {
    const list: ResponseItem[] = [];
    for (const [round, intent] of intents) list.push({ round, op: "set", ...intent, baseRev: rows.get(round)?.rev ?? 0 });
    for (const round of visits) if (!intents.has(round) && !rows.has(round)) list.push({ round, op: "visit" });
    return list;
  }

  function settle() {
    inFlight = false;
    const done = waiters;
    waiters = [];
    for (const resolve of done) resolve();
  }

  function received(result: ResponsesResult, sent: ResponseItem[], sentIntents: Map<number, Intent>) {
    for (const row of result.responses) merge(row);
    sent.forEach((item, index) => {
      if (item.op === "visit") return void visits.delete(item.round);
      const intent = intents.get(item.round);
      if (!intent) return;
      if (same(rows.get(item.round), intent)) {
        intents.delete(item.round);
        failed.delete(item.round);
        conflicts.delete(item.round);
      } else if (intent === sentIntents.get(item.round) && !result.items[index]?.applied) {
        const count = (conflicts.get(item.round) ?? 0) + 1;
        conflicts.set(item.round, count);
        if (count >= 2) failed.add(item.round); // rebased once already: Not saved, retried on the next flush
      }
      // else: a newer intent arrived while this was in flight; it goes next
    });
    failures = 0;
    offline = false;
    if (result.status !== "active" || result.submittedAt) {
      locked = true; // the battle moved on (submitted in another tab, or ended): read it again
      stale += 1;
    }
  }

  function failedWith(error: unknown, sent: ResponseItem[]) {
    for (const item of sent) if (item.op === "set" && intents.has(item.round)) failed.add(item.round);
    if (error instanceof ApiError && error.code === "not_found") {
      gone = true;
      locked = true;
      return;
    }
    if (error instanceof ApiError && error.status === 409) {
      if (error.code === "battle_not_open") locked = true;
      stale += 1; // re-read; the screen chooses its words from the new state
      return;
    }
    failures += 1;
    offline = isOffline();
    // A timeout or no connection (both a network error), a 429 or a server error: not saved, retried with backoff.
    const retryable = !(error instanceof ApiError) || error.code === "network_error" || error.status === 429 || error.status >= 500;
    if (retryable) later();
  }

  function flush() {
    if (inFlight || locked || finished) return;
    const sent = items();
    if (sent.length === 0) return;
    if (isOffline()) {
      offline = true;
      for (const item of sent) if (item.op === "set") failed.add(item.round);
      notify();
      return; // waits for "online" (or Save again)
    }
    clearRetry();
    inFlight = true;
    const sentIntents = new Map(intents);
    notify();
    send(sent, timeout()).then(
      (result) => {
        settle();
        if (finished) return notify();
        received(result, sent, sentIntents);
        notify();
        if ([...intents.keys()].some((round) => !failed.has(round)) || visits.size > 0) flush();
        else if (failed.size > 0) later();
      },
      (error) => {
        settle();
        if (finished) return notify();
        failedWith(error, sent);
        notify();
      },
    );
  }

  const stopOnline = onOnline(() => {
    offline = false;
    notify();
    flush();
  });

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSnapshot: () => snapshot,
    adopt(list, questions) {
      for (const row of list) merge(row);
      const options = questions && new Map(questions.map((question) => [question.round, question.options]));
      let dropped = false;
      for (const [round, intent] of intents) {
        const gone = options !== undefined && intent.choice !== null && !options.get(round)?.some((option) => option.key === intent.choice);
        if (gone || (!inFlight && same(rows.get(round), intent))) {
          intents.delete(round);
          failed.delete(round);
          conflicts.delete(round);
          dropped ||= gone;
        }
      }
      notify();
      if (dropped) flush(); // the rest of the batch goes now
    },
    set(round, intent) {
      if (locked || finished) return;
      const shown = intents.get(round) ?? rows.get(round);
      if (shown && shown.choice === intent.choice && shown.marked === intent.marked) return; // nothing changed
      if (same(rows.get(round), intent) && !inFlight) {
        intents.delete(round); // back to what the server has: nothing to send
        failed.delete(round);
      } else intents.set(round, intent);
      conflicts.delete(round);
      notify();
      flush();
    },
    visit(round) {
      if (locked || finished || rows.has(round) || intents.has(round) || visits.has(round)) return;
      visits.add(round);
      notify();
      flush();
    },
    flush,
    lock() {
      locked = true;
      clearRetry();
    },
    settled: () => (inFlight ? new Promise<void>((resolve) => waiters.push(resolve)) : Promise.resolve()),
    queue: items,
    finish() {
      const lost = intents.size;
      finished = true;
      locked = true;
      clearRetry();
      stopOnline();
      intents.clear();
      visits.clear();
      failed.clear();
      notify();
      return lost;
    },
  };
}

/** The deadline (plan section 2): lock now, then send the whole queue as the auto-submit a moment later. */
export function runDeadline(
  store: Pick<ExamStore, "lock" | "queue">,
  submit: (items: ResponseItem[], auto: true) => void,
  schedule: (run: () => void, ms: number) => unknown = (run, ms) => setTimeout(run, ms),
): unknown {
  store.lock();
  return schedule(() => submit(store.queue(), true), AUTO_SUBMIT_DELAY_MS);
}

const stores = new Map<string, ExamStore>();

/** This battle's store, created on first use and kept across unmounts until the battle no longer takes changes. */
export function examStore(battleId: Uuid): ExamStore {
  let store = stores.get(battleId);
  if (!store) {
    store = createExamStore({ send: (items, signal) => saveResponses(battleId, items, signal) });
    stores.set(battleId, store);
  }
  return store;
}

/** The battle is over (or submitted): stop its store and forget it. Returns how many changes were still unsaved. */
export function dropExamStore(battleId: Uuid): number {
  const store = stores.get(battleId);
  stores.delete(battleId);
  return store ? store.finish() : 0;
}

/** For tests: whether a store exists for this battle. */
export function hasExamStore(battleId: Uuid): boolean {
  return stores.has(battleId);
}
