import { describe, expect, test } from "bun:test";

import { ApiError } from "@/lib/api/errors";
import type { ExamResponse, ResponseItem, ResponsesResult } from "./battle-api";
import { AUTO_SUBMIT_DELAY_MS, type StoreDeps, createExamStore, dropExamStore, examStore, hasExamStore, runDeadline } from "./exam-store";

/** A fake server: each request waits until the test answers it. */
function harness(extra: Partial<StoreDeps> = {}) {
  const calls: { items: ResponseItem[]; resolve: (result: ResponsesResult) => void; reject: (error: unknown) => void }[] = [];
  const timers: { run: () => void; ms: number }[] = [];
  const store = createExamStore({
    send: (items) => new Promise((resolve, reject) => calls.push({ items, resolve, reject })),
    timeout: () => new AbortController().signal,
    schedule: (run, ms) => timers.push({ run, ms }),
    cancel: () => {},
    isOffline: () => false,
    onOnline: () => () => {},
    ...extra,
  });
  return { store, calls, timers };
}

const row = (round: number, choice: string | null, rev: number, marked = false): ExamResponse => ({ round, choice, marked, rev });
function answer(responses: ExamResponse[], items: ResponsesResult["items"], patch: Partial<ResponsesResult> = {}): ResponsesResult {
  return { serverTime: "2026-11-25T10:00:00Z", status: "active", submittedAt: null, opponentSubmitted: false, responses, items, ...patch };
}
const flushed = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("saving as answers change", () => {
  test("a pick is shown at once (Not saved), sent on its revision, and saved when the row matches", async () => {
    const { store, calls } = harness();
    store.adopt([row(1, null, 1)]);
    store.set(1, { choice: "B", marked: false });
    expect(store.getSnapshot().intents.get(1)).toEqual({ choice: "B", marked: false });
    expect(calls[0]?.items).toEqual([{ round: 1, op: "set", choice: "B", marked: false, baseRev: 1 }]);
    calls[0]?.resolve(answer([row(1, "B", 2)], [{ round: 1, applied: true, rev: 2 }]));
    await flushed();
    expect(store.getSnapshot().intents.size).toBe(0);
    expect(store.getSnapshot().rows.get(1)?.rev).toBe(2);
  });

  test("one request at a time; later changes to a question replace the waiting one, and visits ride along", async () => {
    const { store, calls } = harness();
    store.set(1, { choice: "A", marked: false });
    store.set(1, { choice: "C", marked: false });
    store.set(1, { choice: "D", marked: true });
    store.visit(2);
    expect(calls).toHaveLength(1);
    calls[0]?.resolve(answer([row(1, "A", 1)], [{ round: 1, applied: true, rev: 1 }]));
    await flushed();
    expect(calls).toHaveLength(2);
    expect(calls[1]?.items).toEqual([
      { round: 1, op: "set", choice: "D", marked: true, baseRev: 1 },
      { round: 2, op: "visit" },
    ]);
  });

  test("a conflict is rebased once on the new revision; a second one marks the question Not saved", async () => {
    const { store, calls, timers } = harness();
    store.set(3, { choice: "B", marked: false });
    calls[0]?.resolve(answer([row(3, "A", 4)], [{ round: 3, applied: false, rev: 4 }])); // another tab wrote rev 4
    await flushed();
    expect(calls[1]?.items).toEqual([{ round: 3, op: "set", choice: "B", marked: false, baseRev: 4 }]);
    expect(store.getSnapshot().failed.has(3)).toBe(false);
    calls[1]?.resolve(answer([row(3, "C", 5)], [{ round: 3, applied: false, rev: 5 }]));
    await flushed();
    expect(store.getSnapshot().failed.has(3)).toBe(true);
    expect(store.getSnapshot().intents.get(3)?.choice).toBe("B");
    expect(timers).toHaveLength(1); // retried on the next flush
  });

  test("a stale answer that already matches (a replay) counts as saved", async () => {
    const { store, calls } = harness();
    store.set(2, { choice: null, marked: true });
    calls[0]?.resolve(answer([row(2, null, 3, true)], [{ round: 2, applied: false, rev: 3 }]));
    await flushed();
    expect(store.getSnapshot().intents.size).toBe(0);
    expect(calls).toHaveLength(1);
  });

  test("an older read never replaces a newer row", () => {
    const { store } = harness();
    store.adopt([row(1, "B", 3)]);
    store.adopt([row(1, "A", 2)]);
    expect(store.getSnapshot().rows.get(1)?.choice).toBe("B");
  });
});

describe("failures", () => {
  test("a timeout means not saved, then a retry with backoff", async () => {
    const timers: number[] = [];
    const store = createExamStore({
      // As callRoute does: an aborted fetch (here, the timeout) becomes a network error.
      send: (_items, signal) => new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new ApiError(0, "network_error")))),
      timeout: () => AbortSignal.timeout(5),
      schedule: (_run, ms) => timers.push(ms),
      cancel: () => {},
      isOffline: () => false,
      onOnline: () => () => {},
    });
    store.set(1, { choice: "A", marked: false });
    expect(store.getSnapshot().inFlight).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 30));
    const snapshot = store.getSnapshot();
    expect(snapshot.inFlight).toBe(false);
    expect(snapshot.failed.has(1)).toBe(true);
    expect(snapshot.intents.get(1)?.choice).toBe("A");
    expect(timers).toEqual([1000]);
  });

  test("offline: nothing is sent until the browser is back online", async () => {
    let offline = true;
    let online: () => void = () => {};
    const onOnline = (listener: () => void) => {
      online = listener;
      return () => {};
    };
    const { store, calls } = harness({ isOffline: () => offline, onOnline });
    store.set(1, { choice: "A", marked: false });
    expect(calls).toHaveLength(0);
    expect(store.getSnapshot().offline).toBe(true);
    offline = false;
    online();
    expect(calls).toHaveLength(1);
  });

  test("a 409 stops saving and asks for a re-read; a 404 says the battle is gone", async () => {
    const conflict = harness();
    conflict.store.set(1, { choice: "A", marked: false });
    conflict.calls[0]?.reject(new ApiError(409, "battle_not_open"));
    await flushed();
    expect(conflict.store.getSnapshot().stale).toBe(1);
    conflict.store.set(2, { choice: "B", marked: false });
    expect(conflict.calls).toHaveLength(1);

    const gone = harness();
    gone.store.set(1, { choice: "A", marked: false });
    gone.calls[0]?.reject(new ApiError(404, "not_found"));
    await flushed();
    expect(gone.store.getSnapshot().gone).toBe(true);
  });

  test("a re-read drops a pick whose option is gone, so the rest of the batch is saved (no endless 409 invalid_choice)", async () => {
    const { store, calls } = harness();
    const options = [{ key: "A", text: "a" }, { key: "B", text: "b" }];
    store.set(1, { choice: "C", marked: false }); // option C was withdrawn after the paper was read
    store.set(2, { choice: "B", marked: true });
    calls[0]?.reject(new ApiError(409, "invalid_choice"));
    await flushed();
    expect(store.getSnapshot().stale).toBe(1);
    store.adopt([], [{ round: 1, options }, { round: 2, options }]);
    expect(store.getSnapshot().intents.has(1)).toBe(false);
    expect(store.getSnapshot().failed.has(1)).toBe(false);
    expect(calls[1]?.items).toEqual([{ round: 2, op: "set", choice: "B", marked: true, baseRev: 0 }]);
    // A clear (no choice) and a pick still on the paper are kept.
    store.set(3, { choice: null, marked: true });
    store.adopt([], [{ round: 2, options }, { round: 3, options }]);
    expect(store.getSnapshot().intents.has(2)).toBe(true);
    expect(store.getSnapshot().intents.has(3)).toBe(true);
  });

  test("an answer that says the battle moved on (submitted elsewhere) stops saving and asks for a re-read", async () => {
    const { store, calls } = harness();
    store.set(1, { choice: "A", marked: false });
    calls[0]?.resolve(answer([], [{ round: 1, applied: false, rev: 0 }], { submittedAt: "2026-11-25T10:03:00Z" }));
    await flushed();
    expect(store.getSnapshot().stale).toBe(1);
    store.set(2, { choice: "B", marked: false });
    expect(calls).toHaveLength(1);
  });
});

describe("surviving the view, and the deadline", () => {
  test("the queue lives per battle outside the view: unmounting and coming back finds it", () => {
    const id = "44444444-4444-4444-8444-444444444444";
    const before = examStore(id);
    before.lock(); // no request from this test
    expect(examStore(id)).toBe(before);
    expect(hasExamStore(id)).toBe(true);
    dropExamStore(id);
    expect(hasExamStore(id)).toBe(false);
  });

  test("an unsaved change survives while its request is still out, and lands after the view is gone", async () => {
    const { store, calls } = harness();
    store.set(4, { choice: "C", marked: false });
    // (the view unmounts here: nothing in the store depends on it)
    calls[0]?.resolve(answer([row(4, "C", 1)], [{ round: 4, applied: true, rev: 1 }]));
    await flushed();
    expect(store.getSnapshot().rows.get(4)?.choice).toBe("C");
  });

  test("at the deadline: locked at once, then the auto-submit carries the whole queue, and no save follows it", async () => {
    const { store, calls } = harness();
    store.adopt([row(1, "A", 2)]);
    store.set(1, { choice: "B", marked: false });
    calls[0]?.reject(new ApiError(0, "network_error"));
    await flushed();
    const submitted: { items: ResponseItem[]; auto: boolean }[] = [];
    let fire: () => void = () => {};
    let delay = 0;
    runDeadline(
      store,
      (items, auto) => submitted.push({ items, auto }),
      (run, ms) => {
        fire = run;
        delay = ms;
      },
    );
    store.set(2, { choice: "C", marked: false }); // too late: the options are locked
    store.flush();
    expect(calls).toHaveLength(1);
    expect(delay).toBe(AUTO_SUBMIT_DELAY_MS);
    fire();
    expect(submitted).toEqual([{ items: [{ round: 1, op: "set", choice: "B", marked: false, baseRev: 2 }], auto: true }]);
    expect(calls).toHaveLength(1);
  });

  test("settled waits for the request in flight; finish says how many changes were lost", async () => {
    const { store, calls } = harness();
    store.set(1, { choice: "A", marked: false });
    let settled = false;
    void store.settled().then(() => {
      settled = true;
    });
    await flushed();
    expect(settled).toBe(false);
    calls[0]?.reject(new ApiError(503, "service_unavailable"));
    await flushed();
    expect(settled).toBe(true);
    expect(store.finish()).toBe(1);
    expect(store.getSnapshot().intents.size).toBe(0);
  });
});
