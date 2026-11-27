import { describe, expect, test } from "bun:test";

import { ApiError } from "@/lib/api/errors";
import type { ExamResponseItem, PracticeExam, SaveResult } from "@/lib/api/practice-exams";
import { type SaverDeps, createExamSaver } from "./exam-saver";
import { mirrorKey } from "./practice-model";

const ID = "22222222-2222-4222-8222-222222222222";
const tick = async (times = 4) => {
  for (let i = 0; i < times; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
  };
}

type Put = { baseRev: number; items: ExamResponseItem[] };

function harness(options: { answers?: ((call: Put) => Promise<SaveResult>)[]; reread?: () => Promise<Pick<PracticeExam, "rev" | "status">> } = {}) {
  const calls: Put[] = [];
  const answers = [...(options.answers ?? [])];
  const tasks: (() => void)[] = [];
  let offline = false;
  let online: (() => void) | null = null;
  const storage = memoryStorage();
  const deps: SaverDeps = {
    examId: ID,
    rev: 3,
    put: (baseRev, items) => {
      const call = { baseRev, items };
      calls.push(call);
      const next = answers.shift();
      return next ? next(call) : Promise.resolve({ rev: baseRev + 1, serverTime: "2026-11-27T10:00:00Z" });
    },
    reread: options.reread ?? (() => Promise.resolve({ rev: 9, status: "in_progress" as const })),
    storage,
    schedule: (run) => {
      tasks.push(run);
      return tasks.length;
    },
    cancel: () => {},
    isOffline: () => offline,
    onOnline: (listener) => {
      online = listener;
      return () => {
        online = null;
      };
    },
    timeout: () => new AbortController().signal,
  };
  return {
    calls,
    tasks,
    storage,
    deps,
    setOffline: (value: boolean) => {
      offline = value;
    },
    goOnline: () => {
      offline = false;
      online?.();
    },
  };
}

const PICK = { choice: "B", marked: false };

describe("exam saver (createExamStore around PUT …/responses)", () => {
  test("a pick is sent on the exam's rev, the new rev is kept, and nothing is left on this device", async () => {
    const h = harness();
    const saver = createExamSaver(h.deps);
    saver.store.set(1, PICK);
    expect(h.storage.data.has(mirrorKey(ID))).toBe(true); // mirrored at once, before the answer comes back
    await tick();
    expect(h.calls).toEqual([{ baseRev: 3, items: [{ index: 0, keys: ["B"], marked: false, visited: true }] }]);
    expect(saver.rev()).toBe(4);
    expect(saver.store.getSnapshot().intents.size).toBe(0);
    expect(saver.store.getSnapshot().rows.get(1)?.choice).toBe("B");
    expect(h.storage.data.has(mirrorKey(ID))).toBe(false);
  });

  test("only the latest change per question goes, one request at a time", async () => {
    let release: (value: SaveResult) => void = () => {};
    const h = harness({ answers: [() => new Promise<SaveResult>((resolve) => {
            release = resolve;
          })] });
    const saver = createExamSaver(h.deps);
    saver.store.set(1, PICK);
    saver.store.set(2, { choice: "A", marked: false });
    saver.store.set(2, { choice: "C", marked: true });
    expect(h.calls.length).toBe(1);
    release({ rev: 4, serverTime: "2026-11-27T10:00:00Z" });
    await tick();
    expect(h.calls.length).toBe(2);
    expect(h.calls[1]).toEqual({ baseRev: 4, items: [{ index: 1, keys: ["C"], marked: true, visited: true }] });
  });

  test("a 409 is rebased once on the rev the exam has now", async () => {
    const h = harness({ answers: [() => Promise.reject(new ApiError(409, "stale"))] });
    const saver = createExamSaver(h.deps);
    saver.store.set(1, PICK);
    await tick();
    expect(h.calls.map((call) => call.baseRev)).toEqual([3, 9]);
    expect(saver.rev()).toBe(10);
    expect(saver.store.getSnapshot().intents.size).toBe(0);
  });

  test("a second 409 leaves the answer visibly Not saved and asks for a re-read", async () => {
    const stale = () => Promise.reject(new ApiError(409, "stale"));
    const h = harness({ answers: [stale, stale] });
    const saver = createExamSaver(h.deps);
    saver.store.set(1, PICK);
    await tick();
    const snapshot = saver.store.getSnapshot();
    expect(snapshot.failed.has(1)).toBe(true);
    expect(snapshot.stale).toBe(1);
    expect(h.storage.data.has(mirrorKey(ID))).toBe(true);
  });

  test("a closed exam (submitted or past the cutoff) is not rebased", async () => {
    const h = harness({
      answers: [() => Promise.reject(new ApiError(409, "closed"))],
      reread: () => Promise.resolve({ rev: 9, status: "submitted" as const }),
    });
    const saver = createExamSaver(h.deps);
    saver.store.set(1, PICK);
    await tick();
    expect(h.calls.length).toBe(1);
    expect(saver.store.getSnapshot().stale).toBe(1);
  });

  test("offline, answers wait on this device (mirrored) and go when the browser is back online", async () => {
    const h = harness();
    h.setOffline(true);
    const saver = createExamSaver(h.deps);
    saver.store.set(1, PICK);
    await tick();
    expect(h.calls.length).toBe(0);
    expect(saver.store.getSnapshot().offline).toBe(true);
    expect(JSON.parse(h.storage.data.get(mirrorKey(ID)) ?? "{}").items).toEqual([{ index: 0, keys: ["B"], marked: false }]);
    h.goOnline();
    await tick();
    expect(h.calls.length).toBe(1);
    expect(saver.store.getSnapshot().intents.size).toBe(0);
  });

  test("a network error is retried with backoff", async () => {
    const h = harness({ answers: [() => Promise.reject(new ApiError(0, "network_error"))] });
    const saver = createExamSaver(h.deps);
    saver.store.set(1, PICK);
    await tick();
    expect(saver.store.getSnapshot().failed.has(1)).toBe(true);
    expect(h.tasks.length).toBe(1);
    h.tasks[0]?.();
    await tick();
    expect(h.calls.length).toBe(2);
    expect(saver.store.getSnapshot().intents.size).toBe(0);
  });

  test("after a reload, the answers mirrored on this device are replayed over the server's", async () => {
    const h = harness();
    h.storage.setItem(mirrorKey(ID), JSON.stringify({ rev: 3, items: [{ index: 0, keys: ["B"], marked: false }, { index: 1, keys: ["A"], marked: false }] }));
    const saver = createExamSaver(h.deps);
    saver.adopt({ rev: 5, responses: [{ index: 1, keys: ["A"], marked: false, visited: true }] });
    expect(saver.replay()).toBe(1); // question 2 already has A on the server
    await tick();
    expect(h.calls).toEqual([{ baseRev: 5, items: [{ index: 0, keys: ["B"], marked: false, visited: true }] }]);
  });

  test("time-up: the queue and the mirror stay for the final submit and the not-counted notice", async () => {
    const h = harness();
    h.setOffline(true);
    const saver = createExamSaver(h.deps);
    saver.store.set(1, PICK);
    saver.store.lock();
    expect(saver.wire()).toEqual([{ index: 0, keys: ["B"], marked: false, visited: true }]);
    saver.finish();
    expect(saver.mirrored().map((item) => item.index)).toEqual([0]);
    saver.clearMirror();
    expect(h.storage.data.has(mirrorKey(ID))).toBe(false);
  });
});
