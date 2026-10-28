import { afterEach, describe, expect, test } from "bun:test";

import { createAuth, googleUser } from "./config";

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

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
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
