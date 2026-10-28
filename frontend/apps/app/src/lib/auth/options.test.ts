import { describe, expect, test } from "bun:test";

import { signInServedAt } from "./options";

describe("signInServedAt", () => {
  const env = { NEXT_PUBLIC_SITE_URL: "https://abc-def.trycloudflare.com" };

  test("at the configured origin (any path, trailing slash or not)", () => {
    expect(signInServedAt("https://abc-def.trycloudflare.com", env)).toBe(true);
    expect(signInServedAt("https://abc-def.trycloudflare.com/", { NEXT_PUBLIC_SITE_URL: "https://abc-def.trycloudflare.com/" })).toBe(true);
  });

  test("anywhere else: a new tunnel address, plain http, another port, nonsense", () => {
    for (const origin of [
      "https://new-name.trycloudflare.com",
      "http://abc-def.trycloudflare.com",
      "https://abc-def.trycloudflare.com:8443",
      "not a url",
    ]) {
      expect(signInServedAt(origin, env)).toBe(false);
    }
  });

  test("unset (development): every origin", () => {
    expect(signInServedAt("http://127.0.0.1:3000", {})).toBe(true);
  });
});
