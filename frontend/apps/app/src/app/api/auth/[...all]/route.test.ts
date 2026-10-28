import { beforeAll, beforeEach, describe, expect, mock, test } from "bun:test";

import { AUTH_BODY_BYTES } from "@/lib/auth/routes";

// Better Auth itself is not under test here: a stand-in that records what reaches it.
const reached: { method: string; url: string; body: string; headers: Headers }[] = [];
mock.module("@/lib/auth/server", () => ({
  getAuth: () => ({
    auth: {
      handler: async (request: Request) => {
        const body = request.method === "GET" ? "" : await request.text();
        reached.push({ method: request.method, url: request.url, body, headers: request.headers });
        return Response.json({ echoed: body, token: "session-token", user: { id: "u1" } });
      },
    },
  }),
}));

let handle: (request: Request) => Promise<Response>;
beforeAll(async () => {
  ({ POST: handle } = await import("./route"));
});
beforeEach(() => {
  reached.length = 0;
});

const URL_BASE = "https://chat.example.test/api/auth";
const post = (path: string, body: BodyInit, headers: Record<string, string> = {}) =>
  new Request(`${URL_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body,
    // A stream body needs this; harmless otherwise.
    ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
  } as RequestInit);

function stream(totalBytes: number, chunk = 4096): ReadableStream<Uint8Array> {
  let sent = 0;
  return new ReadableStream({
    pull(controller) {
      if (sent >= totalBytes) return controller.close();
      const size = Math.min(chunk, totalBytes - sent);
      sent += size;
      controller.enqueue(new Uint8Array(size).fill(0x61));
    },
  });
}

describe("/api/auth body cap", () => {
  test("a normal body reaches Better Auth byte for byte, and its token never leaves", async () => {
    const body = JSON.stringify({ email: "élève@exemple.test", password: "pässwörd 🔐 with spaces" });
    const response = await handle(post("/sign-in/email", body, { "X-Captcha-Response": "tok" }));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(reached).toHaveLength(1);
    expect(reached[0]!.body).toBe(body);
    expect(reached[0]!.headers.get("x-captcha-response")).toBe("tok");
    const answer = (await response.json()) as Record<string, unknown>;
    expect(answer).toEqual({ echoed: body, user: { id: "u1" } });
  });

  test("exactly the cap passes; one byte more is refused before Better Auth sees it", async () => {
    expect((await handle(post("/sign-in/email", "a".repeat(AUTH_BODY_BYTES)))).status).toBe(200);
    expect(reached).toHaveLength(1);
    const refused = await handle(post("/sign-in/email", "a".repeat(AUTH_BODY_BYTES + 1)));
    expect(refused.status).toBe(413);
    expect(await refused.json()).toEqual({ error: { code: "payload_too_large" } });
    expect(reached).toHaveLength(1);
  });

  test("a streamed body with no length is cut off at the cap", async () => {
    const refused = await handle(post("/sign-in/email-otp", stream(5 * 1024 * 1024)));
    expect(refused.status).toBe(413);
    expect(reached).toHaveLength(0);
    expect((await handle(post("/sign-in/email-otp", stream(1024)))).status).toBe(200);
    expect(reached[0]!.body).toBe("a".repeat(1024));
  });

  test("every allowlisted POST is capped; GET and unknown routes are untouched", async () => {
    for (const path of ["/sign-in/social", "/email-otp/reset-password", "/two-factor/verify-totp", "/change-password"]) {
      expect((await handle(post(path, "x".repeat(AUTH_BODY_BYTES + 10)))).status).toBe(413);
    }
    expect(reached).toHaveLength(0);
    expect((await handle(new Request(`${URL_BASE}/get-session`))).status).toBe(200);
    expect((await handle(post("/sign-up/email", "{}"))).status).toBe(404);
    expect(reached.map((entry) => entry.method)).toEqual(["GET"]);
  });
});
