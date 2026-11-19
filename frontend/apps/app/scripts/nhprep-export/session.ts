/**
 * One headed, branded browser session on nhprep.com behind the exporter's one gate (plan §8.2): every nhprep.com
 * request waits for its paced turn; offsite hosts, images, media, fonts, prefetches, service workers and websockets
 * are refused; a write leaves only while signing in or when explicitly allowed for its exact key. The log holds
 * paths, statuses, parameter NAMES and JSON SHAPES only. Shared by recon.ts and export.ts.
 */
import { appendFileSync } from "node:fs";
import { type Browser, type BrowserContext, type Page, type Response, type Route, chromium } from "@playwright/test";

import { readCredentials } from "./credentials";
import { MIN_GAP_MS, NHPREP_ORIGIN, carriesPassword, createPacer, describeUrl, maxInWindow, shapeOf } from "./gate";

const LOGIN_KEYS = new Set(["POST /api/auth/callback/credentials", "POST /login", "POST /api/auth/signin/credentials", "POST /api/auth/signin/google"]);
// Google's sign-in pages, allowed only while signing in; recorded by host only.
const GOOGLE_HOST = /(^|\.)(google\.com|google\.co\.in|gstatic\.com|googleusercontent\.com|googleapis\.com|accounts\.youtube\.com)$/;
// Video hosts let through only while a probe runs: Bunny Stream and NHprep's own signed-MP4 host.
export const VIDEO_HOST = /(^|\.)(mediadelivery\.net|b-cdn\.net|bunnycdn\.com|bunny\.net)$|^videos\.nhprep\.com$/;
const CHUNK_WORDS = ["mediadelivery", "b-cdn", "iframe", "token", "expires", "libraryId", "videoId", "correct", "explanation", "notes", "chapters", "flashcard", "cheat", "interview", "quiz", "next-action", "drm", "m3u8"];
const SECRETISH = /([?&;](?:token|expires|signature|sig|key|code|state|csrfToken|session)[^=&\s"]*=)[^&\s"']+/gi;
const CHUNK_API = /["'`](\/api\/[A-Za-z0-9/_\-[\]]{2,80})/g;

/** Node timers: a page timer is throttled while the browser window is in the background. */
export const sleep = (ms: number): Promise<void> => new Promise((done) => setTimeout(done, ms));
export const errorText = (error: unknown): string => String(error instanceof Error ? error.message : error).slice(0, 600);

export type JsonListener = (path: string, body: unknown, method: string) => void;

export type SessionOptions = {
  logFile: string;
  gapMs?: number;
  /** Refuse a repeat of the player's `GET /api/video-url/<id>` (it retries every ~1.2 s while media is refused). */
  videoUrlOnce?: boolean;
  /** Log every JSON body's shape and every script chunk's hints (recon); the exporter keeps the log small. */
  verbose?: boolean;
};

export type Session = Awaited<ReturnType<typeof openSession>>;

export async function openSession(options: SessionOptions) {
  const gapMs = Math.max(500, options.gapMs ?? MIN_GAP_MS);
  const sanitize = (text: string): string => text.replace(SECRETISH, "$1<redacted>");
  const tallies = new Map<string, number>();
  const tally = (key: string): void => void tallies.set(key, (tallies.get(key) ?? 0) + 1);
  // A log line that cannot be written (a full disk on 2026-11-19) must not leave a request unanswered: count it instead.
  const log = (kind: string, data: Record<string, unknown> = {}): void => {
    try {
      appendFileSync(options.logFile, `${sanitize(JSON.stringify({ t: new Date().toISOString(), kind, ...data }))}\n`);
    } catch {
      tally("log write failed");
    }
  };
  const pacedAt: number[] = [];
  const pace = createPacer(gapMs);
  const writesAllowed = new Map<string, number>();
  const jsonListeners: JsonListener[] = [];
  const videoUrlsSeen = new Set<string>();
  const staticCache = new Map<string, { status: number; headers: Record<string, string>; body: Buffer }>();
  const state = { loggingIn: false, probe: false };

  const takeWrite = (key: string): boolean => {
    const left = writesAllowed.get(key) ?? 0;
    if (left <= 0) return false;
    writesAllowed.set(key, left - 1);
    return true;
  };

  async function onRoute(route: Route): Promise<void> {
    const request = route.request();
    const url = new URL(request.url());
    const type = request.resourceType();
    const method = request.method();
    const where = describeUrl(request.url());
    const headers = request.headers();
    // A form submitted before hydration goes out as GET ?email=…&password=…: never let a password travel in a URL.
    if (carriesPassword(url)) {
      tally("abort password in URL");
      log("abort", { reason: "password-in-url", method, host: url.host, path: url.pathname });
      return route.abort();
    }
    if (url.origin !== NHPREP_ORIGIN) {
      if (state.loggingIn && GOOGLE_HOST.test(url.hostname)) {
        tally(`login ${url.hostname}`);
        return route.continue();
      }
      if (state.probe && VIDEO_HOST.test(url.hostname)) {
        log("probe-request", { method, ...where, type });
        return route.continue();
      }
      tally(`abort offsite ${url.hostname}`);
      if (options.verbose) log("abort", { reason: "offsite", method, ...where, type });
      return route.abort();
    }
    if (type === "image" || type === "media" || type === "font") {
      tally(`abort ${type}`);
      return route.abort();
    }
    if (headers["next-router-prefetch"] || headers.purpose === "prefetch" || headers["sec-purpose"]?.includes("prefetch")) {
      tally("abort prefetch");
      return route.abort();
    }
    if (options.videoUrlOnce && method === "GET" && url.pathname.startsWith("/api/video-url/")) {
      if (videoUrlsSeen.has(url.pathname)) {
        tally("abort repeated video-url");
        return route.abort();
      }
      videoUrlsSeen.add(url.pathname);
    }
    // Routing turns the browser's HTTP cache off, so each page would fetch every script again. Next's static files are
    // content-hashed (immutable): fetch each once, paced like any request, then serve it from memory.
    if (method === "GET" && url.pathname.startsWith("/_next/static/")) {
      const hit = staticCache.get(url.href);
      if (hit) {
        tally("static from memory");
        return route.fulfill(hit);
      }
      pacedAt.push(await pace());
      const response = await route.fetch();
      // The body comes back decoded: its encoding and length headers would no longer be true.
      const headers = Object.fromEntries(Object.entries(response.headers()).filter(([name]) => !/^content-(encoding|length)$/i.test(name)));
      const entry = { status: response.status(), headers, body: await response.body() };
      if (response.ok()) staticCache.set(url.href, entry);
      return route.fulfill(entry);
    }
    const key = `${method} ${url.pathname}${headers["next-action"] ? " (server action)" : ""}`;
    if (method !== "GET" && method !== "HEAD") {
      if (!((state.loggingIn && LOGIN_KEYS.has(`${method} ${url.pathname}`)) || takeWrite(key))) {
        tally(`abort write ${key}`);
        log("abort", { reason: "write", key, type });
        return route.abort();
      }
      tally(`write ${key.replace(/\/api\/quiz\/[^/]+\//, "/api/quiz/*/")}`);
      if (options.verbose) log("write", { key, type });
    }
    pacedAt.push(await pace());
    await route.continue();
  }

  async function onResponse(response: Response): Promise<void> {
    const request = response.request();
    const url = new URL(response.url());
    const contentType = (response.headers()["content-type"] ?? "").split(";")[0] ?? "";
    if (request.redirectedFrom()) tally("redirect hop");
    if (response.status() === 429) {
      tally("status 429");
      log("rate-limited", { path: url.pathname, retryAfter: response.headers()["retry-after"] ?? null });
    }
    const base = { method: request.method(), ...describeUrl(response.url()), type: request.resourceType(), status: response.status(), contentType };
    if (url.origin !== NHPREP_ORIGIN) {
      if (!state.probe || !VIDEO_HOST.test(url.hostname)) return;
      if (url.pathname.endsWith(".m3u8") && response.ok()) {
        const text = await response.text().catch(() => "");
        const keys = [...text.matchAll(/#EXT-X-(?:SESSION-)?KEY:([^\n]*)/g)].map(([, attrs]) => ({
          method: /METHOD=([^,\s]+)/.exec(attrs ?? "")?.[1] ?? null,
          keyformat: /KEYFORMAT="([^"]+)"/.exec(attrs ?? "")?.[1] ?? null,
        }));
        log("hls", { ...base, variants: (text.match(/#EXT-X-STREAM-INF/g) ?? []).length, keys, mediaSegments: (text.match(/#EXTINF/g) ?? []).length });
      } else log("probe-response", base);
      return;
    }
    if (contentType.includes("json")) {
      const body: unknown = await response.json().catch(() => undefined);
      if (body !== undefined) for (const listener of jsonListeners) listener(url.pathname, body, request.method());
      if (options.verbose) log("response", { ...base, shape: body === undefined ? "unparsed" : shapeOf(body) });
    } else if (options.verbose) {
      const extra = contentType.includes("x-component") ? { rscBytes: (await response.body().catch(() => Buffer.alloc(0))).length } : {};
      log("response", { ...base, ...extra });
    }
    if (options.verbose && request.resourceType() === "script" && url.pathname.startsWith("/_next/static/")) {
      const text = await response.text().catch(() => "");
      const words = CHUNK_WORDS.filter((word) => text.includes(word));
      const apis = [...new Set([...text.matchAll(CHUNK_API)].map(([, path]) => path))].slice(0, 60);
      if (words.length || apis.length) log("chunk", { path: url.pathname, words, apis });
    }
  }

  const browser: Browser = await chromium.launch({ channel: process.env.NHPREP_BROWSER ?? "msedge", headless: false });
  const context: BrowserContext = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1400, height: 900 } });
  context.setDefaultTimeout(90_000);
  context.setDefaultNavigationTimeout(120_000);
  // Which DRM systems a player asks for (Widevine, PlayReady, FairPlay), in every frame: names only.
  await context.addInitScript(() => {
    const original = navigator.requestMediaKeySystemAccess?.bind(navigator);
    if (!original) return;
    navigator.requestMediaKeySystemAccess = (keySystem, configs) => {
      console.debug(`nhprep-eme:${keySystem}`);
      return original(keySystem, configs);
    };
  });
  context.on("console", (message) => {
    if (message.text().startsWith("nhprep-eme:")) log("eme", { keySystem: message.text().slice(11) });
  });
  await context.route("**/*", onRoute);
  await context.routeWebSocket(/.*/, (socket) => socket.close());
  context.on("response", (response) => void onResponse(response).catch((error: unknown) => log("error", { op: "response", message: errorText(error) })));
  const page: Page = await context.newPage();

  /** Open /login and wait until React owns its form (its props are on the element): a submit then is the app's POST, not a native GET. */
  async function loginPage(): Promise<void> {
    if (!page.url().startsWith(`${NHPREP_ORIGIN}/login`)) await page.goto(`${NHPREP_ORIGIN}/login`, { waitUntil: "domcontentloaded" });
    const hydrated = () => page.evaluate(() => Object.keys(document.querySelector("form") ?? {}).some((key) => key.startsWith("__reactProps")));
    for (const started = Date.now(); !(await hydrated()); await sleep(500)) {
      if (Date.now() - started > 120_000) throw new Error("the login form never hydrated");
    }
  }

  const signedInResult = (): Record<string, unknown> => ({
    after: describeUrl(page.url()).path,
    host: new URL(page.url()).host,
    signedIn: page.url().startsWith(NHPREP_ORIGIN) && !page.url().startsWith(`${NHPREP_ORIGIN}/login`),
  });

  /**
   * Owner decision (2026-11-19): the account signs in with Google ("use google signin option ..and use these details").
   * Google's pages are allowed only while this runs; a 2-step check is left to the owner in the headed window (5 min).
   */
  async function googleLogin(): Promise<Record<string, unknown>> {
    const { email, password } = readCredentials("NHPREP_CREDENTIALS_FILE");
    await loginPage();
    state.loggingIn = true;
    try {
      await page.getByRole("button", { name: "Continue with Google" }).click();
      await page.waitForURL((url) => url.hostname === "accounts.google.com", { timeout: 90_000 });
      await page.locator("input[name=identifier]").fill(email);
      await page.getByRole("button", { name: "Next" }).click();
      const secret = page.locator("input[name=Passwd]:visible");
      await secret.waitFor({ timeout: 90_000 });
      await secret.fill(password);
      await page.getByRole("button", { name: "Next" }).click();
      await page.waitForURL((url) => url.origin === NHPREP_ORIGIN, { timeout: 300_000 });
      await page.waitForLoadState("domcontentloaded");
    } finally {
      state.loggingIn = false;
    }
    return signedInResult();
  }

  /** The credentials form (kept for accounts with a password; the company account is Google-only). */
  async function login(): Promise<Record<string, unknown>> {
    const { email, password } = readCredentials("NHPREP_CREDENTIALS_FILE");
    await loginPage();
    state.loggingIn = true;
    try {
      await page.getByLabel("Email address").fill(email);
      await page.getByLabel("Password").fill(password);
      await page.getByRole("button", { name: "Sign In", exact: true }).click();
      await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 60_000 }).catch(() => undefined);
    } finally {
      state.loggingIn = false;
    }
    return signedInResult();
  }

  const stats = () => ({ paced: pacedAt.length, maxPerSecond: maxInWindow(pacedAt), tallies: Object.fromEntries(tallies) });

  return {
    browser,
    context,
    page,
    log,
    state,
    stats,
    gapMs,
    googleLogin,
    login,
    allowWrite: (key: string, n: number) => writesAllowed.set(key, n),
    allowedWrites: () => Object.fromEntries(writesAllowed),
    onJson: (listener: JsonListener) => jsonListeners.push(listener),
    /** A new lesson visit: its player may ask for its video once more (a retry after a failed visit must see it). */
    forgetVideoUrls: () => videoUrlsSeen.clear(),
    close: async () => {
      log("stats", stats());
      await context.close();
      await browser.close();
    },
  };
}
