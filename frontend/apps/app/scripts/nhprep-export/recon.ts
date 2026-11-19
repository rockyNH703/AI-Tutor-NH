/**
 * L1 recon (plan §4 L1, spec r2-nhprep-public-deep §C): the exporter's session (session.ts), driven one command at a
 * time through `<run>/cmd.jsonl`. It records paths, statuses, query parameter NAMES and JSON SHAPES only: never
 * values, tokens, cookies, screenshots, traces, HAR or storage state.
 *
 *   bun build scripts/nhprep-export/recon.ts --target=node --external @playwright/test --outfile node_modules/.cache/nhprep-export/recon.mjs
 *   NHPREP_CREDENTIALS_FILE=<absolute path> node node_modules/.cache/nhprep-export/recon.mjs
 *
 * (Bun cannot drive Playwright's browsers on Windows: the pipe launch times out; Node runs the bundle.)
 * Commands, one JSON object per line: goto {url} | click {role?,name?,text?,selector?,nth?,exact?} | login | google |
 * outline | text {selector|role|text, max?} | probe {on} | videoInfo {id} | playTest {id, unsigned?} | allow {key,n} |
 * stats | wait {ms} | quit.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";

import { MIN_GAP_MS, NHPREP_ORIGIN, describeUrl } from "./gate";
import { assertOutsideAnyRepo } from "./paths";
import { type Session, errorText, openSession, sleep } from "./session";

const ROOT = assertOutsideAnyRepo(process.env.NHPREP_RECON_DIR ?? "C:\\Users\\test\\nhprep-data\\recon");
const dir = join(ROOT, new Date().toISOString().replace(/[:.]/g, "-"));
mkdirSync(dir, { recursive: true });
const cmdFile = join(dir, "cmd.jsonl");
writeFileSync(cmdFile, "");
writeFileSync(join(ROOT, "latest.txt"), dir);

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
async function playTest(session: Session, id: string, unsigned: boolean): Promise<Record<string, unknown>> {
  const { page } = session;
  if (!VIDEO_ID.test(id)) throw new Error("playTest needs a video id (uuid)");
  if (!session.state.probe) throw new Error("turn the probe on first");
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

async function execute(session: Session, command: Command): Promise<Record<string, unknown> | "quit"> {
  const { page } = session;
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
      return session.login();
    case "google":
      return session.googleLogin();
    case "outline":
      return outline(page);
    case "text":
      return { text: (await target(page, command).innerText({ timeout: 10_000 })).slice(0, Number(command.max ?? 4000)) };
    case "probe":
      session.state.probe = command.on === true;
      return { probe: session.state.probe };
    case "videoInfo":
      return videoInfo(page, str(command.id) ?? "");
    case "playTest":
      return playTest(session, str(command.id) ?? "", command.unsigned === true);
    case "allow":
      session.allowWrite(str(command.key) ?? "", Number(command.n ?? 1));
      return { allowed: session.allowedWrites() };
    case "stats":
      return session.stats();
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
  const session = await openSession({ logFile: join(dir, "out.jsonl"), verbose: true });
  session.log("start", { dir, browser: session.browser.version(), gapMs: MIN_GAP_MS });
  for (let done = 0; ; ) {
    const lines = existsSync(cmdFile) ? readFileSync(cmdFile, "utf8").split(/\r?\n/).filter(Boolean) : [];
    for (; done < lines.length; done++) {
      let command: Command;
      try {
        command = JSON.parse(lines[done] ?? "{}") as Command;
      } catch {
        session.log("error", { op: "parse", n: done });
        continue;
      }
      try {
        const result = await execute(session, command);
        if (result === "quit") {
          await session.close();
          session.log("end");
          return;
        }
        session.log("done", { op: command.op, n: done, ...result });
      } catch (error) {
        session.log("error", { op: command.op, n: done, message: errorText(error) });
      }
    }
    await sleep(400);
  }
}

main().catch((error: unknown) => {
  writeFileSync(join(dir, "fatal.txt"), errorText(error));
  process.exitCode = 1;
});
