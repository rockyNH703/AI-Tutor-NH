/**
 * Every way a request can fail, turned into something a person can act on.
 *
 * The UI shows its OWN copy per error code. Backend messages are safe by contract,
 * but they are not ours to word, and internal details (stack traces, upstream
 * bodies) must never reach the screen — so nothing here echoes `message` from a
 * response. The `requestId` is kept for "report this" cases.
 */

import type { ApiErrorEnvelope, FieldError } from "./types";

/** Codes from the API contract plus the ones only the client can observe. */
export type ErrorCode =
  | "unauthorized"
  | "invalid_credentials"
  | "email_not_verified"
  | "invalid_code"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "request_timeout"
  | "payload_too_large"
  | "validation_error"
  | "rate_limited"
  | "service_unavailable"
  | "internal_error"
  | "llm_unavailable"
  | "llm_timeout"
  | "llm_bad_request"
  | "version_conflict"
  | "current_session"
  | "email_unavailable"
  | "reauth_required"
  | "unsupported_media_type"
  | "invalid_token"
  | "too_many_open_requests"
  // the File Library
  | "storage_quota_exceeded"
  | "unsupported_file_type"
  | "file_too_large"
  | "type_mismatch"
  | "files_unavailable"
  | "files_capacity_reached"
  | "too_many_pending_files"
  | "too_many_files"
  | "retry_not_allowed"
  | "busy"
  // Pin, archive and share chats
  | "archived"
  | "not_movable"
  | "not_shareable"
  | "nothing_to_share"
  // NH Friend Zone
  | "already_member"
  | "no_match"
  | "already_friends"
  | "request_pending"
  | "request_limit"
  | "friend_limit"
  | "not_enough_questions"
  | "battle_exists"
  | "invite_limit"
  | "busy_in_battle"
  | "battle_not_open"
  | "round_closed"
  | "already_answered"
  | "invalid_choice"
  | "challenge_closed"
  // voice mode
  | "voice_unreadable"
  | "voice_unavailable"
  | "voice_busy"
  // client-side only
  | "attachment_unavailable"
  | "network_error"
  | "stream_interrupted"
  | "interrupted"
  | "unknown";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode;
  readonly requestId?: string;
  readonly fields?: FieldError[];
  /** Streaming failures say whether retrying (regenerating) can help. */
  readonly retryable?: boolean;
  /** `version_conflict` only: the resource as it is now, to re-apply an edit onto. */
  readonly current?: unknown;

  constructor(
    status: number,
    code: string,
    options: {
      requestId?: string;
      fields?: FieldError[];
      retryable?: boolean;
      current?: unknown;
    } = {},
  ) {
    const known = normalizeCode(code, status);
    super(known);
    this.name = "ApiError";
    this.status = status;
    this.code = known;
    this.requestId = options.requestId;
    this.fields = options.fields;
    this.retryable = options.retryable;
    this.current = options.current;
  }

  /** The machine-readable reason given for `field` (e.g. "set.timezone"), if any. */
  reasonFor(field: string): string | undefined {
    return this.fields?.find((entry) => entry.field === field)?.reason;
  }
}

const KNOWN = new Set<ErrorCode>([
  "unauthorized",
  "request_timeout",
  "invalid_credentials",
  "email_not_verified",
  "invalid_code",
  "forbidden",
  "not_found",
  "conflict",
  "payload_too_large",
  "validation_error",
  "rate_limited",
  "service_unavailable",
  "internal_error",
  "llm_unavailable",
  "llm_timeout",
  "llm_bad_request",
  "version_conflict",
  "current_session",
  "email_unavailable",
  "reauth_required",
  "unsupported_media_type",
  "invalid_token",
  "too_many_open_requests",
  "storage_quota_exceeded",
  "unsupported_file_type",
  "file_too_large",
  "type_mismatch",
  "files_unavailable",
  "files_capacity_reached",
  "too_many_pending_files",
  "too_many_files",
  "retry_not_allowed",
  "busy",
  "archived",
  "not_movable",
  "not_shareable",
  "nothing_to_share",
  "already_member",
  "no_match",
  "already_friends",
  "request_pending",
  "request_limit",
  "friend_limit",
  "not_enough_questions",
  "battle_exists",
  "invite_limit",
  "busy_in_battle",
  "battle_not_open",
  "round_closed",
  "already_answered",
  "invalid_choice",
  "challenge_closed",
  "voice_unreadable",
  "voice_unavailable",
  "voice_busy",
  "attachment_unavailable",
  "network_error",
  "stream_interrupted",
  "interrupted",
]);

function normalizeCode(code: string, status: number): ErrorCode {
  if (KNOWN.has(code as ErrorCode)) return code as ErrorCode;
  // An unfamiliar code still says something through its status.
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 413) return "payload_too_large";
  if (status === 422) return "validation_error";
  if (status === 429) return "rate_limited";
  if (status === 502 || status === 503 || status === 504) return "service_unavailable";
  return "unknown";
}

/** Build an `ApiError` from a non-2xx response, whatever its body looks like. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  let envelope: ApiErrorEnvelope | null = null;
  try {
    envelope = (await response.json()) as ApiErrorEnvelope;
  } catch {
    envelope = null;
  }
  const error = envelope?.error;
  return new ApiError(response.status, typeof error?.code === "string" ? error.code : "", {
    requestId: typeof error?.requestId === "string" ? error.requestId : undefined,
    fields: Array.isArray(error?.fields) ? error.fields : undefined,
    current: error?.current,
  });
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error as { name?: string } | null)?.name === "AbortError"
  );
}

export type RecoveryAction =
  /** Session is gone: send them to sign in. */
  | "sign_in"
  /** Worth trying again now (for a reply: regenerate). */
  | "retry"
  /** Reload the page / refetch. */
  | "reload"
  /** Nothing to do but wait a bit. */
  | "wait"
  /** Change the input (too long, invalid). */
  | "edit"
  | "none";

export type ErrorDescription = {
  code: ErrorCode;
  title: string;
  message: string;
  action: RecoveryAction;
  requestId?: string;
};

const COPY: Record<ErrorCode, { title: string; message: string; action: RecoveryAction }> = {
  unauthorized: {
    title: "You've been signed out",
    message: "Your session has ended. Please sign in again.",
    action: "sign_in",
  },
  invalid_credentials: {
    title: "Couldn't sign in",
    message: "That email or password isn't right.",
    action: "edit",
  },
  email_not_verified: {
    title: "Confirm your email",
    message: "Enter the 6-digit code we emailed you to finish setting up your account.",
    action: "edit",
  },
  invalid_code: {
    title: "That code didn't work",
    message: "The code is wrong or has expired. Request a new one and try again.",
    action: "edit",
  },
  forbidden: {
    title: "Request blocked",
    message: "This request didn't come from this app. Reload the page and try again.",
    action: "reload",
  },
  not_found: {
    title: "Not found",
    message: "It may have been deleted, or it isn't available to your account.",
    action: "none",
  },
  conflict: {
    title: "Still working on a reply",
    message: "A reply is still being written in this chat (perhaps in another tab). Try again in a moment.",
    action: "retry",
  },
  request_timeout: {
    title: "Took too long to send",
    message: "The upload or request did not finish arriving in time. Check your connection and try again.",
    action: "retry",
  },
  payload_too_large: {
    title: "Too long",
    message: "That's more than can be sent at once. Shorten it and try again.",
    action: "edit",
  },
  validation_error: {
    title: "Check the details",
    message: "Something in the form isn't valid. Check it and try again.",
    action: "edit",
  },
  rate_limited: {
    title: "Slow down a little",
    message: "You've reached the limit for now. Please wait a few minutes and try again.",
    action: "wait",
  },
  service_unavailable: {
    title: "Service unavailable",
    message: "The service is temporarily unavailable. Please try again shortly.",
    action: "retry",
  },
  internal_error: {
    title: "Something went wrong",
    message: "Something went wrong on our side. Please try again.",
    action: "retry",
  },
  llm_unavailable: {
    title: "The AI is unavailable",
    message: "The AI service is temporarily unavailable. Please try again.",
    action: "retry",
  },
  llm_timeout: {
    title: "The AI took too long",
    message: "The reply took too long. Please try again.",
    action: "retry",
  },
  llm_bad_request: {
    title: "The AI couldn't handle that",
    message: "Try a shorter message, or start a new chat.",
    action: "edit",
  },
  version_conflict: {
    title: "Changed somewhere else",
    message: "This was changed on another device or tab. The latest version is shown; check it and try again.",
    action: "reload",
  },
  current_session: {
    title: "That's this device",
    message: "To sign this device out, use Log out.",
    action: "none",
  },
  email_unavailable: {
    title: "Email address taken",
    message: "That email address is no longer available. Try a different one.",
    action: "edit",
  },
  reauth_required: {
    title: "Sign in again to confirm",
    message: "For your security, sign out and sign in again, then try within 15 minutes.",
    action: "none",
  },
  unsupported_media_type: {
    title: "Unsupported file",
    message: "Use a JPEG, PNG or WebP image.",
    action: "edit",
  },
  invalid_token: {
    title: "This link doesn't work",
    message: "The link is incomplete or not from us. Open it again from the email.",
    action: "none",
  },
  too_many_open_requests: {
    title: "Many requests are still open",
    message: "You have a lot of open requests. We'll reply to those first.",
    action: "wait",
  },
  storage_quota_exceeded: {
    title: "Storage full",
    message: "Free up storage in your File Library to upload another file.",
    action: "edit",
  },
  unsupported_file_type: {
    title: "File type not supported",
    message: "Upload a PDF, Word, PowerPoint, Excel, CSV, text or image (PNG, JPEG, WebP) file.",
    action: "edit",
  },
  file_too_large: {
    title: "File too large",
    message: "PDF, Word and PowerPoint files can be up to 25 MB; Excel and images 10 MB; text files 2 MB.",
    action: "edit",
  },
  type_mismatch: {
    title: "This file looks damaged",
    message: "Its contents don't match its type. Save it again and upload the new copy.",
    action: "edit",
  },
  files_unavailable: {
    title: "Files are unavailable",
    message: "Files can't be added right now. Please try again later.",
    action: "wait",
  },
  files_capacity_reached: {
    title: "File storage is full",
    message: "Files can't be added right now. Please try again later.",
    action: "wait",
  },
  too_many_pending_files: {
    title: "Files are still being read",
    message: "Wait for your other files to finish before adding more.",
    action: "wait",
  },
  too_many_files: {
    title: "Your library is full",
    message: "Delete some files from your File Library to add more.",
    action: "edit",
  },
  retry_not_allowed: {
    title: "Can't try again",
    message: "This file can't be read. Delete it and upload another copy.",
    action: "none",
  },
  voice_unreadable: {
    title: "Didn't catch that",
    message: "Please say it again.",
    action: "retry",
  },
  voice_unavailable: {
    title: "Voice isn't working",
    message: "Voice isn't available right now. Type your question, or try again shortly.",
    action: "wait",
  },
  voice_busy: {
    title: "Voice is busy",
    message: "Voice mode is busy right now. Please try again in a moment.",
    action: "retry",
  },
  busy: {
    title: "Busy right now",
    message: "Too many files are uploading at once. Please try again in a moment.",
    action: "retry",
  },
  archived: {
    title: "This chat is archived",
    message: "Restore it from Archived chats before pinning it.",
    action: "none",
  },
  not_movable: {
    title: "Not for program chats",
    message: "Program chats stay in their month's history; they can't be pinned or archived.",
    action: "none",
  },
  not_shareable: {
    title: "Not for program chats",
    message: "Program chats can't be shared.",
    action: "none",
  },
  nothing_to_share: {
    title: "Nothing to share yet",
    message: "There's nothing in this chat to share yet.",
    action: "none",
  },
  already_member: {
    title: "Already joined",
    message: "You've already joined Friend Zone. Your profile is shown.",
    action: "reload",
  },
  // The API's own line for every miss, word for word (backend app/friendzone/friends.py NO_MATCH).
  no_match: {
    title: "No match",
    message: "No match. Your friend needs to join Friend Zone and turn on 'Let classmates find me'.",
    action: "edit",
  },
  already_friends: {
    title: "Already friends",
    message: "You're already friends.",
    action: "reload",
  },
  request_pending: {
    title: "Request already sent",
    message: "Your request is already waiting for an answer.",
    action: "reload",
  },
  request_limit: {
    title: "Too many requests waiting",
    message: "You have 50 requests waiting for an answer. Cancel some before sending more.",
    action: "none",
  },
  friend_limit: {
    title: "Friend list full",
    message: "A member can have up to 200 friends. Remove a friend before adding another.",
    action: "none",
  },
  not_enough_questions: {
    title: "Not enough questions",
    message: "No checked questions for this choice yet. Try another month, topic or difficulty.",
    action: "edit",
  },
  battle_exists: {
    title: "Battle already open",
    message: "You already have a battle with this friend. It's in your Battles list.",
    action: "reload",
  },
  invite_limit: {
    title: "Too many invitations waiting",
    message: "You have 3 battle invitations waiting. Wait for an answer or cancel one first.",
    action: "none",
  },
  busy_in_battle: {
    title: "Already in a battle",
    message: "One of you is in another battle right now. Try again when it's over.",
    action: "wait",
  },
  battle_not_open: {
    title: "Battle has moved on",
    message: "This battle can't be changed any more. Its latest state is shown.",
    action: "reload",
  },
  round_closed: {
    title: "Time's up",
    message: "This round closed before your answer arrived.",
    action: "reload",
  },
  already_answered: {
    title: "Answer already locked",
    message: "You've already answered this round. Your first answer counts.",
    action: "reload",
  },
  challenge_closed: {
    title: "Challenge closed",
    message: "This daily challenge is over. A new one comes each day.",
    action: "reload",
  },
  invalid_choice: {
    title: "Not an option",
    message: "That letter isn't one of this question's options.",
    action: "edit",
  },
  attachment_unavailable: {
    title: "A file isn't ready",
    message: "One of the attached files was deleted or isn't ready yet. Remove it or wait, then send again.",
    action: "edit",
  },
  network_error: {
    title: "You're offline",
    message: "We couldn't reach the server. Check your connection and try again.",
    action: "retry",
  },
  stream_interrupted: {
    title: "The reply was interrupted",
    message: "The connection dropped before the reply finished. What arrived is shown above.",
    action: "retry",
  },
  interrupted: {
    title: "The reply was interrupted",
    message: "This reply didn't finish.",
    action: "retry",
  },
  unknown: {
    title: "Something went wrong",
    message: "Please try again.",
    action: "retry",
  },
};

/** User-facing description of any thrown value. */
export function describeError(error: unknown): ErrorDescription {
  if (error instanceof ApiError) {
    const copy = COPY[error.code];
    return {
      code: error.code,
      ...copy,
      // A non-retryable stream failure should not offer a retry button.
      action: error.retryable === false && copy.action === "retry" ? "none" : copy.action,
      requestId: error.requestId,
    };
  }
  if (error instanceof TypeError) {
    return { code: "network_error", ...COPY.network_error };
  }
  return { code: "unknown", ...COPY.unknown };
}

/** Copy for an error code carried on a persisted message (`metadata.errorCode`). */
export function describeCode(code: string | undefined, requestId?: string): ErrorDescription {
  const known: ErrorCode = code && KNOWN.has(code as ErrorCode) ? (code as ErrorCode) : "internal_error";
  return { code: known, ...COPY[known], requestId };
}
