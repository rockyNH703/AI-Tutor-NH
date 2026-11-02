import { afterEach, describe, expect, test } from "bun:test";

import type { Pool } from "pg";

import { createAuth, googleUser, readAuthEnv } from "./config";
import { USED_CODE, claimCode } from "./guard";

const CLIENT_ID = "test-client.apps.googleusercontent.com";
const ENV = {
  databaseUrl: "postgresql://nobody@127.0.0.1:1/none", // never connected: nothing here queries
  secret: "s".repeat(48),
  siteUrl: "https://chat.example.test",
  apiBaseUrl: "http://127.0.0.1:1",
  internalToken: "t".repeat(48),
  google: { clientId: CLIENT_ID, clientSecret: "secret" },
  turnstile: null,
  rateLimit: true,
};

const RAW_ENV = {
  AUTH_DATABASE_URL: ENV.databaseUrl,
  BETTER_AUTH_SECRET: ENV.secret,
  AUTH_INTERNAL_TOKEN: ENV.internalToken,
  NEXT_PUBLIC_SITE_URL: ENV.siteUrl,
  API_BASE_URL: ENV.apiBaseUrl,
};

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("rate limiting configuration", () => {
  test("production refuses to disable Better Auth's rate limiter", () => {
    expect(() => readAuthEnv({ ...RAW_ENV, NODE_ENV: "production", AUTH_RATE_LIMIT: "off" })).toThrow(
      "AUTH_RATE_LIMIT cannot be off in production",
    );
  });

  test("development and tests may disable Better Auth's rate limiter", () => {
    expect(readAuthEnv({ ...RAW_ENV, NODE_ENV: "development", AUTH_RATE_LIMIT: "off" }).rateLimit).toBe(false);
    expect(readAuthEnv({ ...RAW_ENV, NODE_ENV: "test", AUTH_RATE_LIMIT: "off" }).rateLimit).toBe(false);
  });
});

const b64url = (bytes: Uint8Array | string) =>
  Buffer.from(typeof bytes === "string" ? bytes : bytes).toString("base64url");

async function rsaKey(kid: string) {
  const pair = (await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  )) as CryptoKeyPair;
  const jwk = { ...(await crypto.subtle.exportKey("jwk", pair.publicKey)), kid, alg: "RS256", use: "sig" };
  return { pair, jwk };
}

async function idToken(key: CryptoKey, kid: string, claims: Record<string, unknown>) {
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "RS256", kid, typ: "JWT" }));
  const payload = b64url(
    JSON.stringify({ iss: "https://accounts.google.com", aud: CLIENT_ID, iat: now, exp: now + 3600, ...claims }),
  );
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(`${header}.${payload}`));
  return `${header}.${payload}.${b64url(new Uint8Array(signature))}`;
}

/** Google's key endpoint, answered locally with these keys (nothing else is fetched). */
function googleKeys(keys: object[]) {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url === "https://www.googleapis.com/oauth2/v3/certs") return Response.json({ keys });
    throw new Error(`unexpected fetch ${url}`);
  }) as typeof fetch;
}

describe("Google sign-in", () => {
  test("never signs in by a bare ID token (two-step sign-in would be skipped)", () => {
    const { auth } = createAuth(ENV);
    const google = auth.options.socialProviders?.google as { disableIdTokenSignIn?: boolean } | undefined;
    expect(google?.disableIdTokenSignIn).toBe(true);
  });

  test("believes Google's identity only from a token signed by Google, for this app, still valid", async () => {
    const { pair, jwk } = await rsaKey("k1");
    const other = await rsaKey("k1"); // same kid, different key: a forged signature
    googleKeys([jwk]);
    const good = await idToken(pair.privateKey, "k1", { sub: "g-1", email: "a@b.test", email_verified: true, name: "  Ada  " });
    const user = await googleUser(good, CLIENT_ID);
    expect(user?.user).toEqual({ name: "Ada", email: "a@b.test", emailVerified: true, image: undefined });
    expect((user?.data as { sub?: string } | undefined)?.sub).toBe("g-1");

    const now = Math.floor(Date.now() / 1000);
    for (const bad of [
      await idToken(other.pair.privateKey, "k1", { sub: "g-1", email: "a@b.test", email_verified: true }),
      await idToken(pair.privateKey, "k1", { sub: "g-1", email: "a@b.test", aud: "someone-else" }),
      await idToken(pair.privateKey, "k1", { sub: "g-1", email: "a@b.test", iss: "https://evil.test" }),
      await idToken(pair.privateKey, "k1", { sub: "g-1", email: "a@b.test", iat: now - 7200, exp: now - 3600 }),
      await idToken(pair.privateKey, "unknown-kid", { sub: "g-1", email: "a@b.test" }),
      "not.a.token",
    ]) {
      expect(await googleUser(bad, CLIENT_ID)).toBeNull();
    }
    expect(await googleUser(undefined, CLIENT_ID)).toBeNull();
  });

  test("an address Google has not verified is reported as unverified", async () => {
    const { pair, jwk } = await rsaKey("k2");
    googleKeys([jwk]);
    const missing = await idToken(pair.privateKey, "k2", { sub: "g-2", email: "c@d.test" });
    expect((await googleUser(missing, CLIENT_ID))?.user.emailVerified).toBe(false);
  });

  test("Google's keys unavailable: no identity (fails closed)", async () => {
    const { pair } = await rsaKey("k3");
    globalThis.fetch = (async () => new Response("down", { status: 503 })) as unknown as typeof fetch;
    expect(await googleUser(await idToken(pair.privateKey, "k3", { sub: "g-3", email: "e@f.test" }), CLIENT_ID)).toBeNull();
  });
});

describe("accounts", () => {
  test("only a proven address becomes an account", async () => {
    const { auth } = createAuth(ENV);
    const create = auth.options.databaseHooks?.user?.create?.before;
    expect(create).toBeDefined();
    const base = { id: "u1", email: "a@b.test", name: "  Ada  ", image: "https://x.test/p.png", createdAt: new Date(), updatedAt: new Date() };
    for (const emailVerified of [false, undefined, null]) {
      await expect(create!({ ...base, emailVerified } as never)).rejects.toMatchObject({
        body: { code: "EMAIL_NOT_VERIFIED" },
      });
    }
    const ok = (await create!({ ...base, emailVerified: true } as never)) as { data: Record<string, unknown> };
    expect(ok.data).toMatchObject({ name: "Ada", image: null, emailVerified: true });
  });
});

/**
 * auth_attempts as `reserve` uses it, with PostgreSQL's semantics: every statement yields (so
 * parallel transactions interleave), an insert is seen by others only at COMMIT, and
 * pg_advisory_xact_lock waits for the holder's COMMIT or ROLLBACK. It proves the logic, not
 * PostgreSQL itself. `locks: false` takes the lock away, to show the parallel test can fail.
 */
function fakeAttempts({ locks = true } = {}) {
  let now = Date.now();
  const committed: { scope: string; hash: string; at: number }[] = [];
  const held = new Map<string, Promise<void>>();
  const connect = async () => {
    let pending: typeof committed = [];
    const releases: (() => void)[] = [];
    const end = (commit: boolean) => {
      if (commit) committed.push(...pending);
      pending = [];
      for (const release of releases.splice(0)) release();
    };
    return {
      release: () => undefined,
      async query(sql: string, params: unknown[] = []) {
        await new Promise((resolve) => setTimeout(resolve, 0));
        if (sql === "BEGIN") return { rows: [] };
        if (sql === "COMMIT" || sql === "ROLLBACK") {
          end(sql === "COMMIT");
          return { rows: [] };
        }
        if (sql.includes("pg_advisory_xact_lock")) {
          const key = String(params[0]);
          while (locks && held.has(key)) await held.get(key);
          if (locks) {
            let resolveLock: () => void = () => undefined;
            held.set(
              key,
              new Promise<void>((resolve) => {
                resolveLock = resolve;
              }),
            );
            releases.push(() => {
              held.delete(key);
              resolveLock();
            });
          }
          return { rows: [] };
        }
        if (sql.startsWith("SELECT count(*)")) {
          const [scope, hash, minutes] = params as [string, string, number];
          const n = [...committed, ...pending].filter((r) => r.scope === scope && r.hash === hash && r.at > now - minutes * 60_000);
          return { rows: [{ n: n.length }] };
        }
        if (sql.startsWith("INSERT INTO auth_attempts")) {
          const [scope, hash] = params as [string, string];
          pending.push({ scope, hash, at: now });
          return { rows: [] };
        }
        throw new Error(`unexpected SQL: ${sql}`);
      },
    };
  };
  const advance = (ms: number) => {
    now += ms;
  };
  return { pool: { connect } as unknown as Pool, committed, advance };
}

/** A cookie value signed the way Better Auth signs its own (HMAC-SHA256, base64). */
async function signedCookie(value: string, secret: string) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = Buffer.from(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value))).toString("base64");
  return encodeURIComponent(`${value}.${signature}`);
}

describe("an authenticator code works once", () => {
  const WINDOW = USED_CODE.minutes * 60_000;

  test("the second claim of one user's code is refused until the claim expires", async () => {
    const db = fakeAttempts();
    expect(await claimCode(db.pool, "u1", "123456")).toBe(true);
    expect(await claimCode(db.pool, "u1", "123456")).toBe(false);
    db.advance(WINDOW - 1000);
    expect(await claimCode(db.pool, "u1", "123456")).toBe(false);
    db.advance(1000);
    expect(await claimCode(db.pool, "u1", "123456")).toBe(true);
    // Only the claims that passed were recorded, as hashes (no code or user id is stored).
    expect(db.committed).toHaveLength(2);
    expect(db.committed.every((row) => row.scope === "totp_used" && /^[0-9a-f]{64}$/.test(row.hash))).toBe(true);
  });

  test("other users and other codes don't collide", async () => {
    const db = fakeAttempts();
    expect(await claimCode(db.pool, "u1", "123456")).toBe(true);
    expect(await claimCode(db.pool, "u2", "123456")).toBe(true);
    expect(await claimCode(db.pool, "u1", "654321")).toBe(true);
    expect(await claimCode(db.pool, "u1", "12345")).toBe(true);
  });

  test("parallel uses of one code: exactly one claim passes", async () => {
    const db = fakeAttempts();
    const claims = await Promise.all(Array.from({ length: 5 }, () => claimCode(db.pool, "u1", "123456")));
    expect(claims.filter(Boolean)).toHaveLength(1);
    // Without the advisory lock the same interleaving lets every one through.
    const unlocked = fakeAttempts({ locks: false });
    const raced = await Promise.all(Array.from({ length: 5 }, () => claimCode(unlocked.pool, "u1", "123456")));
    expect(raced.filter(Boolean).length).toBeGreaterThan(1);
  });

  test("verify-totp claims the code before it is checked, at sign-in and when turning it on", async () => {
    const { auth, pool } = createAuth(ENV);
    const db = fakeAttempts();
    Object.assign(pool, { connect: db.pool.connect }); // the hook's claim goes to the fake
    const beforeHook = auth.options.hooks?.before as unknown as (ctx: object) => Promise<unknown>;
    const challenge = await signedCookie("challenge-1", ENV.secret);
    const context = {
      secret: ENV.secret,
      createAuthCookie: (name: string) => ({ name, attributes: {} }),
      internalAdapter: {
        findVerificationValue: async (id: string) => (id === "challenge-1" ? { value: "u1" } : null),
      },
    };
    const signIn = (code: string) =>
      beforeHook({
        path: "/two-factor/verify-totp",
        body: { code },
        headers: new Headers({ cookie: `two_factor=${challenge}` }),
        context,
      });
    const turnOn = (code: string) =>
      beforeHook({
        path: "/two-factor/verify-totp",
        body: { code },
        headers: new Headers(),
        context: { ...context, session: { user: { id: "u2" }, session: {} } },
      });

    expect(await signIn("123456")).toEqual({ context: { body: { code: "123456", trustDevice: false } } });
    await expect(signIn("123456")).rejects.toMatchObject({ body: { code: "CODE_ALREADY_USED" } });
    const [first, second] = await Promise.allSettled([turnOn("111111"), turnOn("111111")]);
    expect([first.status, second.status].sort()).toEqual(["fulfilled", "rejected"]);
    const refused = first.status === "rejected" ? first.reason : second.status === "rejected" ? second.reason : null;
    expect(refused).toMatchObject({ body: { code: "CODE_ALREADY_USED" } });
    expect(db.committed).toHaveLength(2);
  });
});

describe("two-factor session revocation", () => {
  const afterHook = () => {
    const { auth } = createAuth(ENV);
    const plugin = auth.options.plugins?.find((candidate) => candidate.id === "padhai-two-factor-everywhere");
    const hook = plugin?.hooks?.after?.find((candidate) => candidate.matcher({ path: "/two-factor/disable" } as never));
    expect(hook).toBeDefined();
    return hook!.handler as unknown as (ctx: object) => Promise<unknown>;
  };

  const context = (path: "/two-factor/verify-totp" | "/two-factor/disable", challenge: string | null) => {
    const deleted: string[] = [];
    const sessions = [
      { token: "current", expiresAt: new Date(Date.now() + 60_000) },
      { token: "other", expiresAt: new Date(Date.now() + 60_000) },
      { token: "expired", expiresAt: new Date(Date.now() - 60_000) },
    ];
    return {
      deleted,
      ctx: {
        path,
        headers: new Headers(),
        context: {
          returned: { status: true },
          newSession: { user: { id: "u1" }, session: { token: "current" } },
          internalAdapter: {
            listSessions: async (userId: string) => {
              expect(userId).toBe("u1");
              return sessions;
            },
            deleteSession: async (token: string) => {
              deleted.push(token);
            },
          },
          createAuthCookie: (name: string) => ({ name, attributes: {} }),
          secret: ENV.secret,
        },
        getSignedCookie: async () => challenge,
      },
    };
  };

  test("successful enable revokes every other active session, preserves the renewed current session, and sends its notice", async () => {
    const events: unknown[] = [];
    globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      events.push(JSON.parse(String(init?.body)));
      return new Response(null, { status: 202 });
    }) as unknown as typeof fetch;
    const { ctx, deleted } = context("/two-factor/verify-totp", null);
    await afterHook()(ctx);
    expect(deleted).toEqual(["other"]);
    expect(events).toEqual([{ kind: "two_factor_on", userId: "u1" }]);
  });

  test("successful disable revokes every other active session, preserves the renewed current session, and sends its notice", async () => {
    const events: unknown[] = [];
    globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      events.push(JSON.parse(String(init?.body)));
      return new Response(null, { status: 202 });
    }) as unknown as typeof fetch;
    const { ctx, deleted } = context("/two-factor/disable", null);
    await afterHook()(ctx);
    expect(deleted).toEqual(["other"]);
    expect(events).toEqual([{ kind: "two_factor_off", userId: "u1" }]);
  });

  test("a sign-in TOTP challenge does not revoke sessions as though two-factor was just enabled", async () => {
    globalThis.fetch = (async () => new Response(null, { status: 202 })) as unknown as typeof fetch;
    const { ctx, deleted } = context("/two-factor/verify-totp", null);
    const challenge = await signedCookie("challenge", ENV.secret);
    (ctx as { headers: Headers }).headers = new Headers({ cookie: `two_factor=${challenge}` });
    await afterHook()(ctx);
    expect(deleted).toEqual([]);
  });
});

describe("cookies on a shared parent domain (*.trycloudflare.com)", () => {
  test("every Better Auth cookie is __Host- on HTTPS, and the session cookie keeps its name", async () => {
    const { auth } = createAuth(ENV);
    const context = await auth.$context;
    for (const name of ["state", "two_factor", "session_data", "dont_remember"]) {
      expect(context.createAuthCookie(name).name).toBe(`__Host-netsecai.${name}`);
    }
    expect(context.authCookies.sessionToken.name).toBe("__Host-padhai_session");
  });

  test("plain HTTP (local development) can't use the prefix", async () => {
    const { auth } = createAuth({ ...ENV, siteUrl: "http://localhost:3000" });
    const context = await auth.$context;
    expect(context.createAuthCookie("state").name).toBe("netsecai.state");
    expect(context.authCookies.sessionToken.name).toBe("padhai_session");
  });
});
