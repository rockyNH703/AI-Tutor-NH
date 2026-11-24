/**
 * The battle runs on the database clock. Each answer carries `serverTime`; the screen keeps the difference between
 * that clock and this device's, and counts every deadline (`closesAt`, `startsAt`, `nextChangeAt`) on the server's
 * time. A device clock that is minutes off still shows the right seconds left.
 */

/**
 * The server's clock minus this device's, from one answer: its `serverTime` against the middle of the request, which
 * halves the network delay's error. An unreadable time gives 0 (trust the device).
 */
export function clockOffset(serverTime: string, sentAt: number, receivedAt: number): number {
  const server = Date.parse(serverTime);
  return Number.isNaN(server) ? 0 : Math.round(server - (sentAt + receivedAt) / 2);
}

export function serverNow(offset: number, localNow: number): number {
  return localNow + offset;
}

/** Milliseconds until a server time (negative once it has passed); null when there is none. */
export function msUntil(at: string | null | undefined, offset: number, localNow: number): number | null {
  if (!at) return null;
  const when = Date.parse(at);
  return Number.isNaN(when) ? null : when - serverNow(offset, localNow);
}

/** Whole seconds left, as a timer shows them: 4.2 s left is "5", and never below 0. */
export function secondsLeft(at: string | null | undefined, offset: number, localNow: number): number {
  const ms = msUntil(at, offset, localNow);
  return ms === null ? 0 : Math.max(0, Math.ceil(ms / 1000));
}

/** The share of a window still left, 0 to 1, for the timer bar. */
export function fractionLeft(closesAt: string, seconds: number, offset: number, localNow: number): number {
  const ms = msUntil(closesAt, offset, localNow);
  if (ms === null || seconds <= 0) return 0;
  return Math.min(1, Math.max(0, ms / (seconds * 1000)));
}
