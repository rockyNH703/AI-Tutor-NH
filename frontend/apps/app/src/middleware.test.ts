import { describe, expect, test } from "bun:test";

import { config } from "./middleware";

// Next applies the matcher as a regular expression on the path (the leading slash included).
const matches = (path: string) => config.matcher.some((pattern) => new RegExp(`^${pattern}$`).test(path));

describe("middleware matcher", () => {
  test("pages go through the middleware (HTTPS, page gating, x-pathname)", () => {
    for (const path of ["/", "/login", "/settings/privacy", "/c/11111111-1111-4111-8111-111111111111", "/help"]) {
      expect(matches(path), path).toBe(true);
    }
  });

  test("API routes never do: Next would buffer (and cut at 10 MB) every request body first", () => {
    for (const path of ["/api/v1/files", "/api/v1/chat/stream", "/api/auth/sign-in/email", "/api/account/password", "/api/health"]) {
      expect(matches(path), path).toBe(false);
    }
  });

  test("static assets never do", () => {
    for (const path of ["/_next/static/chunk.js", "/favicon.ico", "/brand/netsec-logo.svg", "/monitoring"]) {
      expect(matches(path), path).toBe(false);
    }
  });
});
