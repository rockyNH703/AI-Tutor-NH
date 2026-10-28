/**
 * What the sign-in screens say for each of Better Auth's codes, in this app's own words (its
 * message text is never shown). Pure, so it is unit-tested directly (messages.test.ts).
 */

import { PASSWORD_MAX, PASSWORD_MIN } from "@/lib/api/types";

export const ERRORS: Record<string, string> = {
  INVALID_EMAIL_OR_PASSWORD: "That email and password don't match.",
  EMAIL_NOT_VERIFIED: "This address isn't confirmed yet. Sign in with an email code instead.",
  INVALID_OTP: "That code isn't right.",
  OTP_EXPIRED: "That code has expired. Send a new one.",
  TOO_MANY_ATTEMPTS: "Too many wrong codes. Send a new one.",
  INVALID_EMAIL: "Enter a valid email address.",
  PASSWORD_TOO_SHORT: `Passwords must be ${PASSWORD_MIN}–${PASSWORD_MAX} characters.`,
  PASSWORD_TOO_LONG: `Passwords must be ${PASSWORD_MIN}–${PASSWORD_MAX} characters.`,
  PASSWORD_COMPROMISED: "This password has appeared in a data breach, so it isn't safe. Choose a different one.",
  PASSWORD_CHECK_UNAVAILABLE: "We couldn't check that password just now. Please try again in a minute.",
  CAPTCHA_REQUIRED: "Please confirm you're not a robot, then try again.",
  CAPTCHA_INVALID: "That check didn't go through. Please try it again.",
  CAPTCHA_UNAVAILABLE: "The robot check isn't available right now. Please try again in a minute.",
  INVALID_CODE: "That code isn't right. Check the time on your phone, and use the newest code.",
  INVALID_BACKUP_CODE: "That recovery code isn't right, or it was already used.",
  CODE_ALREADY_USED: "That code was just used. Wait for the next one your app shows.",
  ACCOUNT_TEMPORARILY_LOCKED: "Too many wrong codes. Two-step sign-in is locked for 30 minutes.",
  TOO_MANY_ATTEMPTS_REQUEST_NEW_CODE: "Too many wrong codes. Please sign in again.",
  INVALID_TWO_FACTOR_COOKIE: "This sign-in timed out. Please sign in again.",
  SESSION_NOT_FRESH: "For your security, sign out and sign in again, then try within 15 minutes.",
};

/** The codes of a reset code that is wrong, expired or used up: the code step asks again. */
export const CODE_REJECTED: ReadonlySet<string> = new Set(["INVALID_OTP", "OTP_EXPIRED", "TOO_MANY_ATTEMPTS"]);

export function authErrorMessage(error: { code?: string; status?: number } | null | undefined): string {
  if (error?.code && ERRORS[error.code]) return ERRORS[error.code]!;
  if (error?.status === 429) return "Too many attempts. Please wait a few minutes and try again.";
  return "Something went wrong. Please try again.";
}

/** Google sign-in comes back to /login?error=<code> when it does not end in a session. */
export function oauthErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  const key = code.toLowerCase().replace(/[\s-]+/g, "_");
  if (key === "access_denied") return "Google sign-in was cancelled.";
  if (key === "account_not_linked") {
    return "This email already has a NetSec AI account. Sign in with your password or an email code, then connect Google in Settings › Privacy.";
  }
  if (key.includes("email_not_verified")) {
    return "Google hasn't confirmed this email address. Sign in with an email code instead.";
  }
  if (/state|restart|invalid_code|no_code|callback/.test(key)) return "Google sign-in timed out. Please try again.";
  return "Google sign-in didn't work. Please try again, or use your email.";
}
