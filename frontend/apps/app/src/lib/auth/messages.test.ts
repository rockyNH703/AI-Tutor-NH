import { describe, expect, test } from "bun:test";

import { CODE_REJECTED, authErrorMessage, oauthErrorMessage } from "./messages";

describe("sign-in messages", () => {
  test("Google's answers, as /login?error= carries them", () => {
    expect(oauthErrorMessage(null)).toBeNull();
    expect(oauthErrorMessage("access_denied")).toBe("Google sign-in was cancelled.");
    // An unverified Google address gets no account (lib/auth/config.ts): the callback says so.
    expect(oauthErrorMessage("EMAIL_NOT_VERIFIED")).toContain("hasn't confirmed this email");
    expect(oauthErrorMessage("account_not_linked")).toContain("Settings › Privacy");
    expect(oauthErrorMessage("state_mismatch")).toBe("Google sign-in timed out. Please try again.");
    expect(oauthErrorMessage("anything_else")).toContain("didn't work");
  });

  test("Better Auth's codes in this app's words, never its own text", () => {
    expect(authErrorMessage({ code: "INVALID_EMAIL_OR_PASSWORD" })).toBe("That email and password don't match.");
    expect(authErrorMessage({ code: "SOMETHING_NEW", status: 429 })).toContain("Too many attempts");
    expect(authErrorMessage({ code: "SOMETHING_NEW", status: 500 })).toBe("Something went wrong. Please try again.");
  });

  test("a reset's rejected code sends the student back to the code", () => {
    expect([...CODE_REJECTED].sort()).toEqual(["INVALID_OTP", "OTP_EXPIRED", "TOO_MANY_ATTEMPTS"]);
  });
});
