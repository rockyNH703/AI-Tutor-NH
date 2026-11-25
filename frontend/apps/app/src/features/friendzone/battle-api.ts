/**
 * Friend Zone battles (backend app/friendzone/battle_routes.py; docs/api-contract.md "NH Friend Zone", Battles). The
 * server runs the whole battle by the database clock: a read moves it on. A battle is an exam (`format: "exam"`): one
 * paper and one deadline for both, answers saved as they change and graded only at the end. Keys and explanations
 * come only once the battle is completed. `format: "rounds"` battles are history from before the exam.
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
  /** An exam, once completed: whole seconds from the start to the submit. */
  timeTakenSeconds?: number | null;
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

/** A voided exam question has no stem, key or explanation (it changed or was withdrawn after the exam started). */
export type BattleReview = {
  round: number;
  stem: string | null;
  options: BattleOption[];
  correctKey: string | null;
  explanation: string | null;
  voided: boolean;
  myChoice: string | null;
  theirChoice: string | null;
};

/** One of my saved exam responses: a row exists once the question was visited. `rev` is the server's revision. */
export type ExamResponse = { round: number; choice: string | null; marked: boolean; rev: number };

export type ExamQuestion = { round: number; stem: string; options: BattleOption[] };

/** The running exam, once it has started: the same paper for both, my own responses only. */
export type Exam = {
  secondsPerQuestion: number;
  endsAt: string;
  cutoffAt: string;
  questions: ExamQuestion[];
  responses: ExamResponse[];
  submittedAt: string | null;
  opponentSubmitted: boolean;
};

export type BattleXp = { reason: string; amount: number };

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
  /** Missing (an older answer) reads as "rounds". */
  format?: "rounds" | "exam";
  repeatsFromPrevious?: number;
  /** Only while active and started. */
  exam?: Exam | null;
  /** Mine, only once completed. */
  xp?: BattleXp[] | null;
  /** Only on a submit's answer: false when the server took nothing (the battle had moved on, or I had submitted). */
  submitApplied?: boolean | null;
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

/** One question's change: `set` writes its whole state on the revision it was based on; `visit` only marks it seen. */
export type ResponseItem =
  | { round: number; op: "set"; choice: string | null; marked: boolean; baseRev: number }
  | { round: number; op: "visit" };

export type ResponsesResult = {
  serverTime: string;
  status: BattleStatus;
  submittedAt: string | null;
  opponentSubmitted: boolean;
  /** Every response row of mine after the save. */
  responses: ExamResponse[];
  /** One per item sent, in order; `rev` 0: there is no row. */
  items: { round: number; applied: boolean; rev: number }[];
};

/** Save exam answers as they change (compare-and-set per question). */
export function saveResponses(battleId: Uuid, responses: ResponseItem[], signal?: AbortSignal): Promise<ResponsesResult> {
  return callRoute<ResponsesResult>("POST /v1/friend-zone/battles/{battle_id}/responses", {
    params: { battle_id: battleId },
    body: { responses },
    signal,
  });
}

/** Submit my exam with whatever is still unsaved (final values). `auto`: the deadline's own submit. */
export function submitExam(battleId: Uuid, responses: ResponseItem[], auto: boolean): Promise<Battle> {
  return callRoute<Battle>("POST /v1/friend-zone/battles/{battle_id}/submit", { params: { battle_id: battleId }, body: { responses, auto } });
}
