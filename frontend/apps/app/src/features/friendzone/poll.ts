/**
 * When to read a battle next (plan section 10; the Quick Tunnel has no server-sent events). Every read moves the
 * battle on by the database clock, so a read right after a deadline shows the next state at once.
 */

import type { Battle, BattleStatus } from "./battle-api";
import { msUntil } from "./clock";

export const POLL = {
  invited: 3000,
  waiting: 2000,
  roundOpen: 1000,
  hidden: 10_000,
  backoffFirst: 1000,
  backoffMax: 10_000,
  /** Read this long after a deadline, so the server's clock has surely passed it. */
  margin: 50,
  /** Never sooner than this. */
  floor: 100,
} as const;

const TERMINAL: ReadonlySet<BattleStatus> = new Set(["completed", "declined", "expired", "cancelled"]);

export function isTerminal(status: BattleStatus): boolean {
  return TERMINAL.has(status);
}

/** After `failures` failed reads in a row: 1 s, 2 s, 4 s, 8 s, then 10 s. */
export function backoff(failures: number): number {
  return Math.min(POLL.backoffMax, POLL.backoffFirst * 2 ** Math.max(0, failures - 1));
}

export type PollContext = { offset: number; localNow: number; hidden: boolean; failures: number };

/** Milliseconds until the next read, or null: the battle is over, stop. */
export function nextPollDelay(battle: Battle, { offset, localNow, hidden, failures }: PollContext): number | null {
  if (isTerminal(battle.status)) return null;
  if (failures > 0) return backoff(failures);
  if (hidden) return POLL.hidden;
  const until = msUntil(battle.nextChangeAt, offset, localNow);
  // The regular interval, or exactly at the next change if that comes first.
  const capped = (interval: number) => (until === null ? interval : Math.max(POLL.floor, Math.min(interval, until + POLL.margin)));
  if (battle.status === "invited") return capped(POLL.invited);
  if (battle.status === "waiting") return capped(POLL.waiting);
  if (battle.question) return capped(POLL.roundOpen);
  // The countdown or a reveal: nothing changes until the next round opens (or the battle ends).
  return until === null ? POLL.roundOpen : Math.max(POLL.floor, until + POLL.margin);
}
