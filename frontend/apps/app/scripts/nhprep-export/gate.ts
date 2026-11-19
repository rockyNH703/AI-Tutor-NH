/**
 * The exporter's one pacing gate and its observation helpers (plan §8.2). Every request that reaches nhprep.com
 * waits for its turn; turns are at least `minGapMs` apart, whatever started them (documents, RSC, JSON, scripts).
 */
export const NHPREP_ORIGIN = "https://nhprep.com";
export const MIN_GAP_MS = 1200;

type Clock = { now: () => number; sleep: (ms: number) => Promise<void> };
const realClock: Clock = { now: () => Date.now(), sleep: (ms) => new Promise((done) => setTimeout(done, ms)) };

/** A gate: each call resolves at least `minGapMs` after the previous call resolved, in call order. */
export function createPacer(minGapMs: number, clock: Clock = realClock): () => Promise<number> {
  let chain: Promise<number> = Promise.resolve(Number.NEGATIVE_INFINITY);
  return () => {
    chain = chain.then(async (last) => {
      const wait = last + minGapMs - clock.now();
      if (wait > 0) await clock.sleep(wait);
      return clock.now();
    });
    return chain;
  };
}

/** The most requests in any window of `windowMs` (the manifest's burst figure). */
export function maxInWindow(times: readonly number[], windowMs = 1000): number {
  const sorted = [...times].sort((a, b) => a - b);
  let best = 0;
  for (let start = 0, end = 0; end < sorted.length; end++) {
    while ((sorted[end] ?? 0) - (sorted[start] ?? 0) >= windowMs) start++;
    best = Math.max(best, end - start + 1);
  }
  return best;
}

export type Shape = string | Shape[] | { [key: string]: Shape };

/** A JSON value's shape: keys and types only, never a value (arrays: their length and first element's shape). */
export function shapeOf(value: unknown, depth = 0): Shape {
  if (value === null) return "null";
  if (Array.isArray(value)) return value.length === 0 ? [] : [`len ${value.length}`, depth > 7 ? "…" : shapeOf(value[0], depth + 1)];
  if (typeof value !== "object") return typeof value;
  if (depth > 7) return "object…";
  return Object.fromEntries(Object.entries(value).slice(0, 80).map(([key, item]) => [key, shapeOf(item, depth + 1)]));
}

/** A password in a query string (a form submitted natively before hydration): such a request must never leave. */
export function carriesPassword(url: URL): boolean {
  return [...url.searchParams.keys()].some((name) => /^(pass|password|passwd|pwd)$/i.test(name));
}

/** A URL as recorded: origin and path, plus the names (never the values) of its query parameters. */
export function describeUrl(raw: string): { host: string; path: string; params: string[] } {
  const url = new URL(raw);
  return { host: url.host, path: url.pathname, params: [...new Set(url.searchParams.keys())] };
}
