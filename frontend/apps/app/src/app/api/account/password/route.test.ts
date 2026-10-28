import { beforeAll, describe, expect, mock, test } from "bun:test";

import { AUTH_BODY_BYTES } from "@/lib/auth/routes";

// Better Auth is a stand-in: these checks all come before it would be asked anything.
let sessionLookups = 0;
mock.module("@/lib/auth/server", () => ({
  getAuth: () => ({
    auth: {
      api: {
        getSession: async () => {
          sessionLookups += 1;
          return null;
        },
      },
    },
  }),
}));

let POST: (request: Request) => Promise<Response>;
beforeAll(async () => {
  ({ POST } = await import("./route"));
});

const ORIGIN = "https://chat.example.test";
const request = (body: string, headers: Record<string, string> = { Origin: ORIGIN }) =>
  new Request(`${ORIGIN}/api/account/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body,
  });

describe("/api/account/password", () => {
  test("a cross-origin request is refused first", async () => {
    const response = await POST(request("{}", { Origin: "https://evil.test" }));
    expect(response.status).toBe(403);
    expect(sessionLookups).toBe(0);
  });

  test("a body over the cap is refused before the session is even looked up", async () => {
    const response = await POST(request(JSON.stringify({ newPassword: "p".repeat(AUTH_BODY_BYTES) })));
    expect(response.status).toBe(413);
    expect(await response.json()).toEqual({ error: { code: "payload_too_large" } });
    expect(sessionLookups).toBe(0);
  });

  test("within the cap it goes on to the session (none here: 401)", async () => {
    const response = await POST(request(JSON.stringify({ newPassword: "a-long-enough-password" })));
    expect(response.status).toBe(401);
    expect(sessionLookups).toBe(1);
  });
});
