/**
 * NH Friend Zone's API (backend app/friendzone, docs/openapi.json tag `friend-zone`). The API decides everything on
 * every request: who may use Friend Zone, who has joined, what a profile holds. A 404 is one generic "not available".
 */

import type { CONTRACT } from "@/lib/api/contract.gen";
import { BFF_PREFIX, callRoute } from "@/lib/api/http";
import type { Uuid } from "@/lib/api/types";

export type FriendZoneProfile = {
  /** The public id other members will see; never the account's own id. */
  playerId: Uuid;
  displayName: string;
  /** Findable by invoice number (off until the member turns it on). */
  discoverable: boolean;
  showProgram: boolean;
  showStats: boolean;
  version: number;
  /** An isolated QA account; shown to the member only. */
  qa: boolean;
};

export type FriendZoneMe = {
  enabled: boolean;
  eligible: boolean;
  member: boolean;
  suspended: boolean;
  profile: FriendZoneProfile | null;
  serverTime: string;
};

export type PrivacyChoices = Pick<FriendZoneProfile, "discoverable" | "showProgram" | "showStats">;
export type ProfileFields = PrivacyChoices & { displayName: string };

export function getFriendZoneMe(): Promise<FriendZoneMe> {
  return callRoute<FriendZoneMe>("GET /v1/friend-zone/me");
}

export function joinFriendZone(fields: ProfileFields): Promise<FriendZoneProfile> {
  return callRoute<FriendZoneProfile>("POST /v1/friend-zone/me", { body: fields });
}

/** A versioned edit of only the changed fields; a stale version is a 409 `version_conflict`. */
export function updateFriendZoneProfile(version: number, set: Partial<ProfileFields>): Promise<FriendZoneProfile> {
  return callRoute<FriendZoneProfile>("PATCH /v1/friend-zone/me", { body: { version, set } });
}

// ------------------------------------------------------------------ friends, requests, blocks, reports (P1.2)

export type Relation = "self" | "friend" | "incoming" | "outgoing" | "blocked" | "none";

/** Another member as the privacy rules let this account see them: never an invoice number, email or account id. */
export type FriendZonePlayer = {
  playerId: Uuid;
  displayName: string;
  initials: string;
  relation: Relation;
  /** The photo's API path, or null when it isn't shown (friends only). */
  avatarUrl: string | null;
  /** null: hidden by the member. */
  programs: { slug: string; title: string }[] | null;
  /** From `GET players/{id}` only: yours, or a friend's who shows them; null otherwise. */
  stats?: PlayerStats | null;
};

export type Badge = { key: string; title: string; description: string };

/** A member's record (Phase 2): battles, accuracy, XP and level, earned badges, daily challenges. */
export type PlayerStats = {
  battles: number;
  wins: number;
  losses: number;
  draws: number;
  correct: number;
  answered: number;
  xp: number;
  level: number;
  levelFloor: number;
  nextLevelAt: number;
  badges: Badge[];
  dailiesCompleted: number;
  dailyStreak: number;
};

export type XpPage = {
  xp: number;
  level: number;
  levelFloor: number;
  nextLevelAt: number;
  items: { reason: string; amount: number; createdAt: string }[];
  nextCursor: string | null;
};

/** A member as you may see them: yourself, a friend, or a pending-request party (with `stats` as allowed). */
export function getPlayer(playerId: Uuid): Promise<FriendZonePlayer> {
  return callRoute<FriendZonePlayer>("GET /v1/friend-zone/players/{player_id}", { params: { player_id: playerId } });
}

/** Your own XP: the total, the level and its span, and the history, newest first. */
export function getXp(cursor?: string | null): Promise<XpPage> {
  return callRoute<XpPage>("GET /v1/friend-zone/me/xp", { query: { cursor } });
}

export type FriendZoneRequest = { requestId: Uuid; player: FriendZonePlayer; createdAt: string; expiresAt: string };
export type FriendZoneRequests = { incoming: FriendZoneRequest[]; outgoing: FriendZoneRequest[] };
export type ReportReason = (typeof CONTRACT.FriendZoneReportRequest.reason.enum)[number];

/** An exact invoice number; every miss is the same 404 `no_match`, and every answer takes at least 0.4 s. */
export function searchFriendZone(invoiceNumber: string): Promise<FriendZonePlayer> {
  return callRoute<FriendZonePlayer>("POST /v1/friend-zone/search", { body: { invoiceNumber } });
}

export async function listFriends(): Promise<FriendZonePlayer[]> {
  return (await callRoute<{ items: FriendZonePlayer[] }>("GET /v1/friend-zone/friends")).items;
}

export function removeFriend(playerId: Uuid): Promise<void> {
  return callRoute<void>("DELETE /v1/friend-zone/friends/{player_id}", { params: { player_id: playerId } });
}

export function listRequests(): Promise<FriendZoneRequests> {
  return callRoute<FriendZoneRequests>("GET /v1/friend-zone/requests");
}

/** Ask a member; if they had already asked you, you are friends at once (`relation` "friend"). */
export function sendRequest(playerId: Uuid): Promise<FriendZonePlayer> {
  return callRoute<FriendZonePlayer>("POST /v1/friend-zone/requests", { body: { playerId } });
}

export function answerRequest(requestId: Uuid, answer: "accept" | "decline" | "cancel"): Promise<FriendZonePlayer | undefined> {
  const params = { request_id: requestId };
  if (answer === "accept") return callRoute<FriendZonePlayer>("POST /v1/friend-zone/requests/{request_id}/accept", { params });
  if (answer === "decline") return callRoute<undefined>("POST /v1/friend-zone/requests/{request_id}/decline", { params });
  return callRoute<undefined>("POST /v1/friend-zone/requests/{request_id}/cancel", { params });
}

export async function listBlocks(): Promise<FriendZonePlayer[]> {
  return (await callRoute<{ items: FriendZonePlayer[] }>("GET /v1/friend-zone/blocks")).items;
}

export function blockPlayer(playerId: Uuid): Promise<void> {
  return callRoute<void>("POST /v1/friend-zone/blocks", { body: { playerId } });
}

export function unblockPlayer(playerId: Uuid): Promise<void> {
  return callRoute<void>("DELETE /v1/friend-zone/blocks/{player_id}", { params: { player_id: playerId } });
}

export function reportPlayer(playerId: Uuid, reason: ReportReason, details: string): Promise<{ reportId: Uuid }> {
  const text = details.trim();
  return callRoute<{ reportId: Uuid }>("POST /v1/friend-zone/reports", { body: { playerId, reason, ...(text ? { details: text } : {}) } });
}

const AVATAR_PATH = /^\/v1\/friend-zone\/players\/[0-9a-f-]{36}\/avatar\?v=[0-9a-f]{32}$/;

/** The photo's address through the BFF, only when `avatarUrl` is exactly the API's avatar route; else null (initials). */
export function avatarSrc(avatarUrl: string | null): string | null {
  return avatarUrl && AVATAR_PATH.test(avatarUrl) ? `${BFF_PREFIX}${avatarUrl}` : null;
}
