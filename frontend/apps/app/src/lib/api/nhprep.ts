/**
 * NHprep video courses (features/nhprep), through the BFF only. Every route answers 404 to an account NHprep
 * isn't for, the same as a missing lesson.
 *
 * Reads go through one per-tab cache: a request already in flight is shared, and a lesson's detail, its items
 * and a list of summaries are kept for the page's life (a chat of 20+ cards, opened twice, asks once). A failed
 * read is dropped from the cache, so Try again asks again. Playback is never cached: each Play asks for a fresh,
 * short-lived link, which lives only in the player's state (never in storage, logs or the URL).
 */
import { callRoute } from "./http";
import type { NhprepItemKind, NhprepItemList, NhprepLesson, NhprepLessonList, NhprepPlayback, Uuid } from "./types";

type Entry = { promise: Promise<unknown>; value?: unknown; done: boolean };
const cache = new Map<string, Entry>();

/** The shared request for `key`, started by `load` only when nothing is cached or in flight. */
export function cachedRead<T>(key: string, load: () => Promise<T>): Promise<T> {
  const hit = cache.get(key);
  if (hit) return hit.promise as Promise<T>;
  const entry: Entry = { promise: Promise.resolve(), done: false };
  entry.promise = load().then(
    (value) => {
      entry.value = value;
      entry.done = true;
      return value;
    },
    (error: unknown) => {
      if (cache.get(key) === entry) cache.delete(key);
      throw error;
    },
  );
  cache.set(key, entry);
  return entry.promise as Promise<T>;
}

/** What `key` already resolved to in this tab, or undefined: lets a view draw cached data without a flash. */
export function peekRead<T>(key: string): T | undefined {
  const hit = cache.get(key);
  return hit?.done ? (hit.value as T) : undefined;
}

/** Tests only. */
export function clearNhprepCache(): void {
  cache.clear();
}

export const lessonKey = (lessonId: Uuid) => `lesson:${lessonId}`;
export const itemsKey = (lessonId: Uuid, kind: NhprepItemKind) => `items:${lessonId}:${kind}`;
export const listKey = (ids: readonly Uuid[]) => `list:${ids.join(",")}`;

/** ≤10 summaries, in the order asked. */
export function getNhprepLessons(ids: readonly Uuid[]): Promise<NhprepLessonList> {
  return cachedRead(listKey(ids), () =>
    callRoute<NhprepLessonList>("GET /v1/nhprep/lessons", { query: { ids: ids.slice(0, 10).join(",") } }),
  );
}

export function getNhprepLesson(lessonId: Uuid): Promise<NhprepLesson> {
  return cachedRead(lessonKey(lessonId), () =>
    callRoute<NhprepLesson>("GET /v1/nhprep/lessons/{lesson_id}", { params: { lesson_id: lessonId } }),
  );
}

export function getNhprepItems(lessonId: Uuid, kind: NhprepItemKind): Promise<NhprepItemList> {
  return cachedRead(itemsKey(lessonId, kind), () =>
    callRoute<NhprepItemList>("GET /v1/nhprep/lessons/{lesson_id}/items", {
      params: { lesson_id: lessonId },
      query: { kind },
    }),
  );
}

/** A fresh link at Play (and after a failed one); never cached. */
export function startNhprepPlayback(lessonId: Uuid): Promise<NhprepPlayback> {
  return callRoute<NhprepPlayback>("POST /v1/nhprep/lessons/{lesson_id}/playback", { params: { lesson_id: lessonId } });
}
