/**
 * L1 recon (plan §4 L1, spec r2-nhprep-public-deep §C): a headed branded browser on nhprep.com, driven one command at
 * a time through `<run>/cmd.jsonl`, observing through the exporter's one gate. It records paths, statuses, query
 * parameter NAMES and JSON SHAPES only: never values, tokens, cookies, screenshots, traces, HAR or storage state.
 *
 *   bun build scripts/nhprep-export/recon.ts --target=node --external @playwright/test --outfile node_modules/.cache/nhprep-export/recon.mjs
 *   NHPREP_CREDENTIALS_FILE=<absolute path> node node_modules/.cache/nhprep-export/recon.mjs
 *
 * (Bun cannot drive Playwright's browsers on Windows: the pipe launch times out; Node runs the bundle.)
 * Commands, one JSON object per line: goto {url} | click {role?,name?,text?,selector?,nth?,exact?} | login | outline |
 * text {selector|role|text, max?} | probe {on} | allow {key,n} | stats | wait {ms} | quit.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { type Locator, type Page, type Response, type Route, chromium } from "@playwright/test";

import { readCredentials } from "./credentials";
import { MIN_GAP_MS, NHPREP_ORIGIN, carriesPassword, createPacer, describeUrl, maxInWindow, shapeOf } from "./gate";
import { assertOutsideAnyRepo } from "./paths";

const ROOT = assertOutsideAnyRepo(process.env.NHPREP_RECON_DIR ?? "C:\\Users\\test\\nhprep-data\\recon");
const dir = join(ROOT, new Date().toISOString().replace(/[:.]/g, "-"));
mkdirSync(dir, { recursive: true });
const outFile = join(dir, "out.jsonl");
const cmdFile = join(dir, "cmd.jsonl");
writeFileSync(cmdFile, "");
writeFileSync(join(ROOT, "latest.txt"), dir);

const LOGIN_KEYS = new Set(["POST /api/auth/callback/credentials", "POST /login", "POST /api/auth/signin/credentials", "POST /api/auth/signin/google"]);
// Google's sign-in pages, allowed only while signing in; recorded by host only.
const GOOGLE_HOST = /(^|\.)(google\.com|google\.co\.in|gstatic\.com|googleusercontent\.com|googleapis\.com|accounts\.youtube\.com)$/;
// Video hosts let through only while a probe runs: Bunny Stream and NHprep's own signed-MP4 host.
const BUNNY_HOST = /(^|\.)(mediadelivery\.net|b-cdn\.net|bunnycdn\.com|bunny\.net)$|^videos\.nhprep\.com$/;
const CHUNK_WORDS = ["mediadelivery", "b-cdn", "iframe", "token", "expires", "libraryId", "videoId", "correct", "explanation", "notes", "chapters", "flashcard", "cheat", "interview", "quiz", "next-action", "drm", "m3u8"];
const SECRETISH = /([?&;](?:token|expires|signature|sig|key|code|state|csrfToken|session)[^=&\s"]*=)[^&\s"']+/gi;
const CHUNK_API = /["'`](\/api\/[A-Za-z0-9/_\-[\]]{2,80})/g;

// Node timers: a page timer is throttled while the browser window is in the background.
const sleep = (ms: number): Promise<void> => new Promise((done) => setTimeout(done, ms));
const sanitize = (text: string): string => text.replace(SECRETISH, "$1<redacted>");
const log = (kind: string, data: Record<string, unknown> = {}): void =>
  appendFileSync(outFile, `${sanitize(JSON.stringify({ t: new Date().toISOString(), kind, ...data }))}\n`);

const tallies = new Map<string, number>();
const tally = (key: string): void => void tallies.set(key, (tallies.get(key) ?? 0) + 1);
const pacedAt: number[] = [];
const pace = createPacer(MIN_GAP_MS);
const writesAllowed = new Map<string, number>();
let loggingIn = false;
let bunnyProbe = false;

function takeWrite(key: string): boolean {
  const left = writesAllowed.get(key) ?? 0;
  if (left <= 0) return false;
  writesAllowed.set(key, left - 1);
  return true;
}

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
    if (loggingIn && GOOGLE_HOST.test(url.hostname)) {
      tally(`login ${url.hostname}`);
      return route.continue();
    }
    if (bunnyProbe && BUNNY_HOST.test(url.hostname)) {
      log("bunny-request", { method, ...where, type });
      return route.continue();
    }
    tally(`abort offsite ${url.hostname}`);
    log("abort", { reason: "offsite", method, ...where, type });
    return route.abort();
  }
  if (type === "image" || type === "media" || type === "font") {
    tally(`abort ${type}`);
    return route.abort();
  }
  if (headers["next-router-prefetch"] || headers.purpose === "prefetch" || headers["sec-purpose"]?.includes("prefetch")) {
    tally("abort prefetch");
    log("abort", { reason: "prefetch", method, ...where, type });
    return route.abort();
  }
  const key = `${method} ${url.pathname}${headers["next-action"] ? " (server action)" : ""}`;
  if (method !== "GET" && method !== "HEAD") {
    if (!((loggingIn && LOGIN_KEYS.has(`${method} ${url.pathname}`)) || takeWrite(key))) {
      tally(`abort write ${key}`);
      log("abort", { reason: "write", key, type });
      return route.abort();
    }
    log("write", { key, type });
  }
  pacedAt.push(await pace());
  await route.continue();
}

async function onResponse(response: Response): Promise<void> {
  const request = response.request();
  const url = new URL(response.url());
  const contentType = (response.headers()["content-type"] ?? "").split(";")[0] ?? "";
  const base = { method: request.method(), ...describeUrl(response.url()), type: request.resourceType(), status: response.status(), contentType };
  if (request.redirectedFrom()) tally("redirect hop");
  if (url.origin !== NHPREP_ORIGIN) {
    if (!bunnyProbe || !BUNNY_HOST.test(url.hostname)) return;
    if (url.pathname.endsWith(".m3u8") && response.ok()) {
      const text = await response.text().catch(() => "");
      const keys = [...text.matchAll(/#EXT-X-(?:SESSION-)?KEY:([^\n]*)/g)].map(([, attrs]) => ({
        method: /METHOD=([^,\s]+)/.exec(attrs ?? "")?.[1] ?? null,
        keyformat: /KEYFORMAT="([^"]+)"/.exec(attrs ?? "")?.[1] ?? null,
      }));
      log("hls", { ...base, variants: (text.match(/#EXT-X-STREAM-INF/g) ?? []).length, keys, mediaSegments: (text.match(/#EXTINF/g) ?? []).length });
    } else log("bunny-response", base);
    return;
  }
  const extra: Record<string, unknown> = {};
  if (contentType.includes("json")) extra.shape = await response.json().then(shapeOf, () => "unparsed");
  else if (contentType.includes("x-component")) extra.rscBytes = (await response.body().catch(() => Buffer.alloc(0))).length;
  log("response", { ...base, ...extra });
  if (request.resourceType() === "script" && url.pathname.startsWith("/_next/static/")) {
    const text = await response.text().catch(() => "");
    const words = CHUNK_WORDS.filter((word) => text.includes(word));
    const apis = [...new Set([...text.matchAll(CHUNK_API)].map(([, path]) => path))].slice(0, 60);
    if (words.length || apis.length) log("chunk", { path: url.pathname, words, apis });
  }
}

type Command = { op: string; [key: string]: unknown };

const str = (value: unknown): string | undefined => (typeof value === "string" ? value : undefined);

function target(page: Page, command: Command): Locator {
  const role = str(command.role);
  const name = str(command.name);
  const text = str(command.text);
  const exact = Boolean(command.exact);
  const base = role
    ? page.getByRole(role as Parameters<Page["getByRole"]>[0], name ? { name, exact } : {})
    : text
      ? page.getByText(text, { exact })
      : page.locator(str(command.selector) ?? "body");
  return typeof command.nth === "number" ? base.nth(command.nth) : base.first();
}

async function outline(page: Page): Promise<Record<string, unknown>> {
  const found = await page.evaluate(() => {
    const visible = (el: Element) => (el as HTMLElement).offsetParent !== null;
    const clean = (el: Element, max: number) => (el.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, max);
    const texts = (selector: string, limit: number) => [...document.querySelectorAll(selector)].filter(visible).slice(0, limit).map((el) => clean(el, 120));
    return {
      title: document.title,
      headings: texts("h1, h2, h3", 60),
      buttons: texts("button, [role=button]", 80),
      tabs: texts("[role=tab]", 40),
      links: [...document.querySelectorAll("a[href]")].filter(visible).slice(0, 150).map((el) => [clean(el, 80), (el as HTMLAnchorElement).href]),
      iframes: [...document.querySelectorAll("iframe")].map((el) => el.src).filter(Boolean),
      videos: document.querySelectorAll("video").length,
      inputs: [...document.querySelectorAll("input, textarea, select")].slice(0, 40).map((el) => [(el as HTMLInputElement).type, (el as HTMLInputElement).name, el.getAttribute("aria-label")]),
    };
  });
  const host = new URL(NHPREP_ORIGIN).host;
  const toPath = (href: string) => {
    const where = describeUrl(href);
    return where.host === host ? where.path : `${where.host}${where.path}`;
  };
  return {
    path: describeUrl(page.url()).path,
    ...found,
    links: found.links.map(([text, href]) => [text, toPath(href ?? NHPREP_ORIGIN)]),
    iframes: found.iframes.map((src) => describeUrl(src)),
  };
}

/** Open /login and wait until React owns its form (its props are on the element): a submit then is the app's POST, not a native GET. */
async function loginPage(page: Page): Promise<void> {
  if (!page.url().startsWith(`${NHPREP_ORIGIN}/login`)) await page.goto(`${NHPREP_ORIGIN}/login`, { waitUntil: "domcontentloaded" });
  const hydrated = () => page.evaluate(() => Object.keys(document.querySelector("form") ?? {}).some((key) => key.startsWith("__reactProps")));
  for (const started = Date.now(); !(await hydrated()); await sleep(500)) {
    if (Date.now() - started > 120_000) throw new Error("the login form never hydrated");
  }
}

const signedInResult = (page: Page): Record<string, unknown> => ({
  after: describeUrl(page.url()).path,
  host: new URL(page.url()).host,
  signedIn: page.url().startsWith(NHPREP_ORIGIN) && !page.url().startsWith(`${NHPREP_ORIGIN}/login`),
});

/**
 * Owner decision (2026-11-19): the account signs in with Google ("use google signin option ..and use these details").
 * Google's pages are allowed only while this runs; a 2-step check is left to the owner in the headed window (5 min).
 */
async function googleLogin(page: Page): Promise<Record<string, unknown>> {
  const { email, password } = readCredentials("NHPREP_CREDENTIALS_FILE");
  await loginPage(page);
  loggingIn = true;
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
    loggingIn = false;
  }
  return signedInResult(page);
}

async function login(page: Page): Promise<Record<string, unknown>> {
  const { email, password } = readCredentials("NHPREP_CREDENTIALS_FILE");
  await loginPage(page);
  loggingIn = true;
  try {
    await page.getByLabel("Email address").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 60_000 }).catch(() => undefined);
  } finally {
    loggingIn = false;
  }
  const alert = await page.getByRole("alert").first().textContent({ timeout: 1000 }).catch(() => null);
  const signedIn = !page.url().startsWith(`${NHPREP_ORIGIN}/login`);
  return { after: describeUrl(page.url()).path, signedIn, alert: alert?.trim().slice(0, 160) ?? null };
}

const VIDEO_ID = /^[0-9a-f-]{36}$/;

/** NHprep's signed video link for one video id, described without its signature: host, path, file type, lifetime. */
async function videoInfo(page: Page, id: string): Promise<Record<string, unknown>> {
  if (!VIDEO_ID.test(id)) throw new Error("videoInfo needs a video id (uuid)");
  return page.evaluate(async (videoId) => {
    const response = await fetch(`/api/video-url/${videoId}`);
    const url = new URL(((await response.json()) as { url: string }).url);
    const exp = Number(url.searchParams.get("exp"));
    return {
      status: response.status,
      host: url.host,
      path: url.pathname,
      params: [...url.searchParams.keys()],
      ttlSeconds: Number.isFinite(exp) && exp > 0 ? exp - Math.floor(Date.now() / 1000) : null,
      sigLength: (url.searchParams.get("sig") ?? "").length,
    };
  }, id);
}

/**
 * Play a few seconds of one video in a muted <video> on the page, signed (as NHprep serves it) or with its query
 * stripped (`unsigned`), and report what happened: playing or the media error, and whether EME (DRM) was involved.
 * Needs `probe {on:true}`. The URL never leaves the page.
 */
async function playTest(page: Page, id: string, unsigned: boolean): Promise<Record<string, unknown>> {
  if (!VIDEO_ID.test(id)) throw new Error("playTest needs a video id (uuid)");
  if (!bunnyProbe) throw new Error("turn the probe on first");
  await page.evaluate(
    async ({ videoId, strip }) => {
      document.getElementById("nhprep-probe")?.remove();
      const response = await fetch(`/api/video-url/${videoId}`);
      const url = new URL(((await response.json()) as { url: string }).url);
      if (strip) url.search = "";
      const video = document.createElement("video");
      video.id = "nhprep-probe";
      video.muted = true;
      video.preload = "auto";
      video.style.width = "320px";
      video.src = url.href;
      document.body.prepend(video);
      void video.play().catch(() => undefined);
    },
    { videoId: id, strip: unsigned },
  );
  const read = () =>
    page.evaluate(() => {
      const video = document.getElementById("nhprep-probe") as HTMLVideoElement | null;
      return video
        ? { readyState: video.readyState, currentTime: Math.round(video.currentTime * 10) / 10, duration: Number.isFinite(video.duration) ? Math.round(video.duration) : null, errorCode: video.error?.code ?? null, mediaKeys: video.mediaKeys !== null, width: video.videoWidth, height: video.videoHeight }
        : null;
    });
  let state = await read();
  for (const started = Date.now(); Date.now() - started < 20_000; await sleep(500)) {
    state = await read();
    if (!state || state.errorCode !== null || state.currentTime >= 3) break;
  }
  await page.evaluate(() => {
    const video = document.getElementById("nhprep-probe") as HTMLVideoElement | null;
    video?.pause();
    video?.removeAttribute("src");
    video?.load();
    video?.remove();
  });
  return { unsigned, played: (state?.currentTime ?? 0) >= 3, ...state };
}

async function execute(page: Page, command: Command): Promise<Record<string, unknown> | "quit"> {
  switch (command.op) {
    case "goto": {
      const url = new URL(str(command.url) ?? "/", NHPREP_ORIGIN);
      if (url.origin !== NHPREP_ORIGIN) throw new Error("goto is for nhprep.com only");
      const response = await page.goto(url.href, { waitUntil: "domcontentloaded" });
      return { path: describeUrl(page.url()).path, status: response?.status() ?? null };
    }
    case "click":
      await target(page, command).click({ timeout: 30_000 });
      await page.waitForLoadState("domcontentloaded");
      return { path: describeUrl(page.url()).path };
    case "login":
      return login(page);
    case "google":
      return googleLogin(page);
    case "outline":
      return outline(page);
    case "text":
      return { text: (await target(page, command).innerText({ timeout: 10_000 })).slice(0, Number(command.max ?? 4000)) };
    case "probe":
      bunnyProbe = command.on === true;
      return { bunnyProbe };
    case "videoInfo":
      return videoInfo(page, str(command.id) ?? "");
    case "playTest":
      return playTest(page, str(command.id) ?? "", command.unsigned === true);
    case "allow":
      writesAllowed.set(str(command.key) ?? "", Number(command.n ?? 1));
      return { allowed: Object.fromEntries(writesAllowed) };
    case "stats":
      return { paced: pacedAt.length, maxPerSecond: maxInWindow(pacedAt), tallies: Object.fromEntries(tallies) };
    case "wait":
      await sleep(Math.min(Number(command.ms ?? 1000), 120_000));
      return {};
    case "quit":
      return "quit";
    default:
      throw new Error(`unknown op ${command.op}`);
  }
}

async function main(): Promise<void> {
  const browser = await chromium.launch({ channel: process.env.NHPREP_BROWSER ?? "msedge", headless: false });
  const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1400, height: 900 } });
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
  context.on("response", (response) => void onResponse(response).catch((error: unknown) => log("error", { op: "response", message: String(error) })));
  const page = await context.newPage();
  log("start", { dir, browser: browser.version(), gapMs: MIN_GAP_MS });

  for (let done = 0; ; ) {
    const lines = existsSync(cmdFile) ? readFileSync(cmdFile, "utf8").split(/\r?\n/).filter(Boolean) : [];
    for (; done < lines.length; done++) {
      let command: Command;
      try {
        command = JSON.parse(lines[done] ?? "{}") as Command;
      } catch {
        log("error", { op: "parse", n: done });
        continue;
      }
      try {
        const result = await execute(page, command);
        if (result === "quit") {
          log("stats", { paced: pacedAt.length, maxPerSecond: maxInWindow(pacedAt), tallies: Object.fromEntries(tallies) });
          await context.close();
          await browser.close();
          log("end");
          return;
        }
        log("done", { op: command.op, n: done, ...result });
      } catch (error) {
        log("error", { op: command.op, n: done, message: String(error instanceof Error ? error.message : error).slice(0, 600) });
      }
    }
    await sleep(400);
  }
}

main().catch((error: unknown) => {
  log("fatal", { message: String(error instanceof Error ? error.message : error).slice(0, 600) });
  process.exitCode = 1;
});
