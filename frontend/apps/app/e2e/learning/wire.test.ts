import { describe, expect, test } from "bun:test";
import { randomBytes } from "node:crypto";
import { serializeSignedCookie } from "better-call";

import { PLAIN_COOKIE, verifySignedToken } from "../../src/lib/server/cookies";
import { AUTH_DATABASE_URL, AUTH_SECRET, DATABASE_URL, assertSafeDatabase } from "./env";
import { signedCookieValue, sseEvents } from "./wire";

describe("seeded session cookie", () => {
  const token = randomBytes(24).toString("base64url");

  test("is exactly the cookie Better Auth would set", async () => {
    const real = (await serializeSignedCookie(PLAIN_COOKIE, token, AUTH_SECRET)).split(";")[0];
    expect(`${PLAIN_COOKIE}=${signedCookieValue(token, AUTH_SECRET)}`).toBe(real as string);
  });

  test("passes the BFF's check only with the suite's secret", async () => {
    expect(await verifySignedToken(signedCookieValue(token, AUTH_SECRET), AUTH_SECRET)).toBe(token);
    expect(await verifySignedToken(signedCookieValue(token, "another-secret-of-at-least-32-chars"), AUTH_SECRET)).toBeNull();
  });
});

describe("chat stream frames", () => {
  test("reads meta, tokens and done, skipping keep-alives, with LF or CRLF", () => {
    const body = 'event: meta\ndata: {"conversationId":"c1"}\n\n: keep-alive\n\nevent: token\ndata: {"text":"You "}\n\nevent: done\ndata: {"message":{"id":"m1"}}\n\n';
    const expected = [
      { event: "meta", data: { conversationId: "c1" } },
      { event: "token", data: { text: "You " } },
      { event: "done", data: { message: { id: "m1" } } },
    ];
    expect(sseEvents(body)).toEqual(expected);
    expect(sseEvents(body.replace(/\n/g, "\r\n"))).toEqual(expected);
  });
});

describe("database guard", () => {
  test("accepts the suite's own loopback *_test database", () => {
    expect(() => assertSafeDatabase(DATABASE_URL)).not.toThrow();
    expect(() => assertSafeDatabase(AUTH_DATABASE_URL)).not.toThrow();
  });

  test("refuses another host or a database that is not *_test", () => {
    expect(() => assertSafeDatabase("postgresql://aichat:aichat@10.1.50.94:5432/netsec_learning_e2e_test")).toThrow();
    expect(() => assertSafeDatabase("postgresql+asyncpg://aichat:aichat@127.0.0.1:5432/aichat")).toThrow();
    expect(() => assertSafeDatabase("postgresql://aichat:aichat@127.0.0.1:5432/netsec_test_live")).toThrow();
  });
});
