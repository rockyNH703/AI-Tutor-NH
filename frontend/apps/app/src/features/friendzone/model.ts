/**
 * Friend Zone's screen logic as plain data, unit-tested (model.test.ts): which state the hub shows, whether the
 * Practice | Friend Zone switch shows, the name hints, the wording of every choice and failure. Nothing here decides
 * access: the API does, on every request; this only mirrors it so the screen can explain itself.
 */

import { ApiError, describeError } from "@/lib/api/errors";
import { isUuid } from "@/lib/api/routes";
import type {
  FriendZoneMe,
  FriendZonePlayer,
  FriendZoneProfile,
  PrivacyChoices,
  ProfileFields,
  Relation,
  ReportReason,
} from "./api";

export type MeLoad = { status: "loading" } | { status: "ready"; me: FriendZoneMe } | { status: "error"; error: unknown };

export type HubState =
  | { kind: "loading" }
  | { kind: "error" }
  /** Friend Zone isn't switched on for this account: nothing is shown at all. */
  | { kind: "off" }
  | { kind: "ineligible" }
  | { kind: "suspended" }
  | { kind: "join" }
  | { kind: "member"; profile: FriendZoneProfile };

export function hubState(load: MeLoad): HubState {
  if (load.status === "loading") return { kind: "loading" };
  if (load.status === "error") return { kind: "error" };
  const { me } = load;
  if (!me.enabled) return { kind: "off" };
  if (!me.eligible) return { kind: "ineligible" };
  if (me.suspended) return { kind: "suspended" };
  if (!me.member || !me.profile) return { kind: "join" };
  return { kind: "member", profile: me.profile };
}

export type QuizzesView = "practice" | "friend-zone";

/** Quizzes & Games' Friend Zone side is `?zone=friends` (notifications link there too); anything else is Practice. */
export const VIEW_PARAM = "zone";
const FRIEND_ZONE_VALUE = "friends";

export function viewFromParam(value: string | null): QuizzesView {
  return value === FRIEND_ZONE_VALUE ? "friend-zone" : "practice";
}

/**
 * The view shown: the one asked for, except that Friend Zone is never shown to an account it is off for. While
 * `/me` loads or fails, an address asking for Friend Zone keeps it (its loading or Try again state).
 */
export function shownView(load: MeLoad, requested: QuizzesView): QuizzesView {
  if (requested === "practice") return "practice";
  return hubState(load).kind === "off" ? "practice" : "friend-zone";
}

/** The switch shows once Friend Zone is known to be on, or while the Friend Zone view itself is shown. */
export function showsSwitch(load: MeLoad, requested: QuizzesView): boolean {
  return load.status === "ready" ? load.me.enabled : shownView(load, requested) === "friend-zone";
}

/** The address for a view, keeping every other parameter (`?month=`); Practice also drops an open battle. */
export function searchForView(search: string, view: QuizzesView): string {
  const params = new URLSearchParams(search);
  if (view === "friend-zone") params.set(VIEW_PARAM, FRIEND_ZONE_VALUE);
  else {
    params.delete(VIEW_PARAM);
    params.delete(BATTLE_PARAM);
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** The battle on screen is `?battle=<id>` (notifications link there too); anything but an id is none. */
export const BATTLE_PARAM = "battle";

export function battleFromParam(value: string | null): string | null {
  return value && isUuid(value) ? value : null;
}

/** The address with a battle opened (or closed, null), on the Friend Zone side, keeping everything else. */
export function searchForBattle(search: string, battleId: string | null): string {
  const params = new URLSearchParams(search);
  if (battleId) {
    params.set(VIEW_PARAM, FRIEND_ZONE_VALUE);
    params.set(BATTLE_PARAM, battleId);
  } else params.delete(BATTLE_PARAM);
  const query = params.toString();
  return query ? `?${query}` : "";
}

// The name rule mirrors the API's (backend app/friendzone/names.py) for hints only; the API decides, and also refuses
// names reserved for NetworkersHome and its staff, which the screen learns from a 422 on `displayName`.
export const NAME_MIN = 2;
export const NAME_MAX = 30;
const NAME_CHARACTERS = /^[\p{L}\p{M}\p{N} ._'-]+$/u;

/** The name as the API stores it: NFKC, with runs of spaces made one. */
export function cleanName(raw: string): string {
  return raw.normalize("NFKC").split(/\s+/u).filter(Boolean).join(" ");
}

export const NAME_HINT = `${NAME_MIN} to ${NAME_MAX} characters: letters, numbers, spaces and . _ ' -. Don't use your email or invoice number.`;

/** Why a name would be refused, as far as the screen can tell; null when it looks fine. */
export function nameProblem(raw: string): string | null {
  const name = cleanName(raw);
  if (!name) return "Enter a name.";
  if ([...name].length < NAME_MIN || [...name].length > NAME_MAX) return `Use ${NAME_MIN} to ${NAME_MAX} characters.`;
  if (!NAME_CHARACTERS.test(name) || name.toLowerCase().includes("www.")) return "Use letters, numbers, spaces and . _ ' - only.";
  return null;
}

/** Up to two initials, standing in for a photo. */
export function initials(name: string): string {
  return cleanName(name)
    .split(" ")
    .slice(0, 2)
    .map((word) => [...word][0] ?? "")
    .join("")
    .toUpperCase();
}

export const PRIVACY: readonly { key: keyof PrivacyChoices; label: string; description: string }[] = [
  {
    key: "discoverable",
    label: "Let classmates find me by invoice number",
    description: "A classmate who types your invoice number sees your Friend Zone name. Your invoice number is never shown.",
  },
  {
    key: "showProgram",
    label: "Show my program to friends",
    description: "Friends see which NetworkersHome program you're in.",
  },
  {
    key: "showStats",
    label: "Show my stats to friends",
    description: "Friends see your battle results and accuracy.",
  },
];

/** The join consent, word for word as the owner approved it (U9, 2026-11-24). Change only with the owner. */
export const CONSENT =
  "Friend Zone shows your Friend Zone name to classmates you add as friends. If you turn it on, classmates can find you by your invoice number; the number itself is never shown. Your email, invoice number and learning records are never shared. Battle and daily-challenge results are shared only with friends, and stats only if you choose. You can change these settings at any time, and deleting your account removes your Friend Zone data.";

export const PHOTO_NOTE = "Your account photo is shown to friends only.";

export const NEW_PROFILE: ProfileFields = { displayName: "", discoverable: false, showProgram: false, showStats: false };

export function fieldsOf(profile: FriendZoneProfile): ProfileFields {
  const { displayName, discoverable, showProgram, showStats } = profile;
  return { displayName, discoverable, showProgram, showStats };
}

/** Only what changed, so an edit never re-sends a value it didn't touch. */
export function changedFields(before: ProfileFields, after: ProfileFields): Partial<ProfileFields> {
  const name = cleanName(after.displayName);
  return {
    ...(name !== before.displayName ? { displayName: name } : {}),
    ...Object.fromEntries(PRIVACY.filter(({ key }) => after[key] !== before[key]).map(({ key }) => [key, after[key]])),
  };
}

export type Failure = {
  message: string;
  /** The message belongs to the name field. */
  onName: boolean;
  /** What is shown is out of date (the profile, a request, a friend, or whether Friend Zone is open): read it again. */
  reload: boolean;
};

export type Action = "join" | "save" | "search" | "request" | "answer" | "remove" | "block" | "unblock" | "report";

const LIMITED: Record<Action, string> = {
  join: "You've tried to join many times in the last hour. Try again later.",
  save: "You've changed your profile many times in the last hour. Try again later.",
  search: "You've reached the search limit for now. Try again later, or tomorrow if it's been a busy day.",
  request: "You've sent the most friend requests allowed today. Try again tomorrow.",
  answer: "Too many changes at once. Try again in a moment.",
  remove: "Too many changes at once. Try again in a moment.",
  block: "Too many changes at once. Try again in a moment.",
  unblock: "Too many changes at once. Try again in a moment.",
  report: "You've sent the most reports allowed today. Try again tomorrow, and block the member if you need to now.",
};

const GONE: Record<"profile" | "other", string> = {
  profile: "Friend Zone isn't available to your account right now.",
  other: "That's no longer available. The lists are up to date now.",
};

/** What a failed Friend Zone action says, in the app's own words (the API's text is never shown). */
export function failureCopy(error: unknown, action: Action): Failure {
  const failure = (message: string, reload = false, onName = false): Failure => ({ message, onName, reload });
  if (!(error instanceof ApiError)) return failure(describeError(error).message);
  switch (error.code) {
    case "validation_error":
      return error.reasonFor("displayName")
        ? failure("That name can't be used. Choose a different one.", false, true)
        : failure(describeError(error).message);
    case "already_member":
      return failure("You've already joined Friend Zone, so your profile is shown.", true);
    case "version_conflict":
      return failure(
        "Your profile was changed in another tab or on another device. The latest version is shown; make your change again.",
        true,
      );
    case "not_found":
      return failure(GONE[action === "join" || action === "save" ? "profile" : "other"], true);
    case "rate_limited":
      return failure(LIMITED[action]);
    case "already_friends":
    case "request_pending":
      return failure(describeError(error).message, true);
    default:
      return failure(describeError(error).message); // no_match, request_limit, friend_limit and the rest
  }
}

/** The one thing a found member's card offers, by how they relate to you. */
export type FoundAction = { kind: "send"; label: string } | { kind: "accept"; label: string } | { kind: "note"; text: string };

export function foundAction(relation: Relation): FoundAction {
  switch (relation) {
    case "none":
      return { kind: "send", label: "Send friend request" };
    case "incoming":
      return { kind: "accept", label: "Accept their request" };
    case "outgoing":
      return { kind: "note", text: "Request sent. It's waiting for an answer." };
    case "friend":
      return { kind: "note", text: "You're already friends." };
    case "self":
      return { kind: "note", text: "That's your own invoice number." };
    case "blocked":
      return { kind: "note", text: "You've blocked this member. Unblock them below to send a request." };
  }
}

export const REPORT_REASONS: Record<ReportReason, string> = {
  harassment: "Harassment or bullying",
  cheating: "Cheating in battles",
  inappropriate_name: "Inappropriate name",
  spam: "Spam",
  other: "Something else",
};

export const REPORT_NOTE =
  "Blocking protects you straight away: you won't see or find each other in Friend Zone. Reports are reviewed privately by the NetSec AI owner, and the member isn't told about them.";

/** A friend's programs as the member lets friends see them. */
export function programsLine(programs: FriendZonePlayer["programs"]): string {
  if (programs === null) return "Program hidden";
  if (programs.length === 0) return "No program shown";
  return programs.map((program) => program.title).join(", ");
}
