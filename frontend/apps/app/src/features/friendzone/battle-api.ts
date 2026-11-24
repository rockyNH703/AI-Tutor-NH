/**
 * Friend Zone battles (backend app/friendzone/battle_routes.py; docs/api-contract.md "NH Friend Zone", Battles). The
 * server runs the whole battle by the database clock: a read moves it on. The open round never carries its key, and
 * explanations come only once the battle is completed.
 */

import type { CONTRACT } from "@/lib/api/contract.gen";
import { callRoute } from "@/lib/api/http";
import type { Uuid } from "@/lib/api/types";

export type BattleStatus = "invited" | "waiting" | "active" | "completed" | "declined" | "expired" | "cancelled";
export type BattleResult = "points" | "forfeit" | "draw" | "void";
export type Difficulty = (typeof CONTRACT.FriendZoneCreateBattleRequest.difficulty.enum)[number];
export type BattleLength = (typeof CONTRACT.FriendZoneCreateBattleRequest.length.enum)[number];

export type BattlePlayer = {
  /** null once that member's account is gone. */
  playerId: Uuid | null;
  displayName: string | null;
  initials: string | null;
  role: "challenger" | "opponent";
  ready: boolean;
  score: number;
  correct: number;
  answered: number;
  winner: boolean;
};

export type BattleOption = { key: string; text: string };

/** The open round: never its key. */
export type BattleQuestion = {
  round: number;
  stem: string;
  options: BattleOption[];
  seconds: number;
  opensAt: string;
  closesAt: string;
  myChoice: string | null;
};

/** A closed round. */
export type BattleRound = {
  round: number;
  voided: boolean;
  correctKey: string | null;
  myChoice: string | null;
  theirChoice: string | null;
  myPoints: number;
  theirPoints: number;
};

export type BattleReview = {
  round: number;
  stem: string;
  options: BattleOption[];
  correctKey: string;
  explanation: string;
  voided: boolean;
  myChoice: string | null;
  theirChoice: string | null;
};

export type Battle = {
  battleId: Uuid;
  status: BattleStatus;
  endReason: string | null;
  result: BattleResult | null;
  program: { slug: string; title: string };
  month: number;
  topicId: string | null;
  difficulty: string;
  length: number;
  roundsClosed: number;
  expiresAt: string | null;
  startsAt: string | null;
  endsAt: string | null;
  nextChangeAt: string | null;
  serverTime: string;
  me: BattlePlayer;
  opponent: BattlePlayer;
  question: BattleQuestion | null;
  rounds: BattleRound[];
  review: BattleReview[] | null;
};

export type BattleSummary = {
  battleId: Uuid;
  status: BattleStatus;
  result: BattleResult | null;
  role: "challenger" | "opponent";
  opponent: BattlePlayer;
  program: { slug: string; title: string };
  month: number;
  length: number;
  createdAt: string;
  endedAt: string | null;
  expiresAt: string | null;
};

export type BattlePage = { items: BattleSummary[]; nextCursor: string | null };

export type Challenge = {
  opponentId: Uuid;
  program: string;
  month: number;
  topicId?: string;
  difficulty: Difficulty;
  length: BattleLength;
  /** One per challenge dialog: sending it again answers with the same battle. */
  requestId: Uuid;
};

export function listBattles(view: "open" | "history", cursor?: string | null): Promise<BattlePage> {
  return callRoute<BattlePage>("GET /v1/friend-zone/battles", { query: { view, cursor } });
}

export function createBattle(challenge: Challenge): Promise<Battle> {
  return callRoute<Battle>("POST /v1/friend-zone/battles", { body: challenge });
}

export function getBattle(battleId: Uuid, signal?: AbortSignal): Promise<Battle> {
  return callRoute<Battle>("GET /v1/friend-zone/battles/{battle_id}", { params: { battle_id: battleId }, signal });
}

export type BattleMove = "accept" | "decline" | "cancel" | "ready";

const MOVES = {
  accept: "POST /v1/friend-zone/battles/{battle_id}/accept",
  decline: "POST /v1/friend-zone/battles/{battle_id}/decline",
  cancel: "POST /v1/friend-zone/battles/{battle_id}/cancel",
  ready: "POST /v1/friend-zone/battles/{battle_id}/ready",
} as const;

export function moveBattle(battleId: Uuid, move: BattleMove): Promise<Battle> {
  return callRoute<Battle>(MOVES[move], { params: { battle_id: battleId } });
}

/** A new invitation to the same friend with the same settings (only after a battle decided by play). */
export function rematchBattle(battleId: Uuid, requestId: Uuid): Promise<Battle> {
  return callRoute<Battle>("POST /v1/friend-zone/battles/{battle_id}/rematch", { params: { battle_id: battleId }, body: { requestId } });
}

/** One letter for the open round, final at once. */
export function answerBattle(battleId: Uuid, round: number, choice: string): Promise<Battle> {
  return callRoute<Battle>("POST /v1/friend-zone/battles/{battle_id}/answers", { params: { battle_id: battleId }, body: { round, choice } });
}
