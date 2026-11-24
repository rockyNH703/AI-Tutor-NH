/**
 * The daily challenge (backend app/friendzone/daily_routes.py; docs/api-contract.md "NH Friend Zone", Phase 2): ten
 * questions per program per India day, answered one at a time, each with its own window. Keys, explanations and
 * friends' results come only once your attempt is finished.
 */

import { callRoute } from "@/lib/api/http";
import type { Uuid } from "@/lib/api/types";
import type { BattleOption } from "./battle-api";

export type DailyQuestion = {
  position: number;
  stem: string;
  options: BattleOption[];
  seconds: number;
  servedAt: string;
  closesAt: string;
  myChoice: string | null;
};

export type DailyAttempt = {
  status: "not_started" | "in_progress" | "finished";
  served: number;
  answered: number;
  /** Only once finished. */
  correct: number | null;
  score: number | null;
  startedAt: string | null;
  finishedAt: string | null;
};

export type DailyReview = {
  position: number;
  stem: string;
  options: BattleOption[];
  correctKey: string;
  explanation: string;
  myChoice: string | null;
  correct: boolean | null;
  points: number;
  voided: boolean;
};

export type DailyFriend = {
  playerId: Uuid;
  displayName: string;
  initials: string;
  status: "completed" | "in_progress";
  /** Only for a friend who shows their stats. */
  score: number | null;
  correct: number | null;
};

export type Daily = {
  available: boolean;
  challengeId: Uuid | null;
  date: string;
  program: { slug: string; title: string };
  startsAt: string;
  closesAt: string;
  serverTime: string;
  attempt: DailyAttempt | null;
  question: DailyQuestion | null;
  review: DailyReview[] | null;
  friends: DailyFriend[] | null;
  nextChangeAt: string;
};

export const DAILY_QUESTIONS = 10;

export function getDaily(program: string, signal?: AbortSignal): Promise<Daily> {
  return callRoute<Daily>("GET /v1/friend-zone/daily", { query: { program }, signal });
}

/** Start or resume, then serve the next question unless the current one is still open and unanswered. */
export function nextDaily(challengeId: Uuid): Promise<Daily> {
  return callRoute<Daily>("POST /v1/friend-zone/daily/{challenge_id}/next", { params: { challenge_id: challengeId } });
}

export function answerDaily(challengeId: Uuid, position: number, choice: string): Promise<Daily> {
  return callRoute<Daily>("POST /v1/friend-zone/daily/{challenge_id}/answers", {
    params: { challenge_id: challengeId },
    body: { position, choice },
  });
}
