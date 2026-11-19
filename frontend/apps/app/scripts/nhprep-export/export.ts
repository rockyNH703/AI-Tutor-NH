/**
 * The NHprep exporter (plan §8.2, export v1 frozen 2026-11-19 in C:\Users\test\netsec-handoffs\nhprep.md): signs in
 * (Google, owner decision), then writes one JSONL stream of `course`, `lesson`, `quiz_question` and `quiz_coverage`
 * records plus a sha256 manifest into a run folder outside every repository. Every request goes through session.ts's
 * gate. Quiz submits are the only writes (owner decision Q8: no cap; each category stops by the stop rule) and are
 * counted in a ledger that outlives runs. Never stored: video URLs, `exp`/`sig`, tokens, cookies, CSRF values.
 *
 *   bun build scripts/nhprep-export/export.ts --target=node --external @playwright/test --outfile node_modules/.cache/nhprep-export/export.mjs
 *   NHPREP_CREDENTIALS_FILE=<abs path> node node_modules/.cache/nhprep-export/export.mjs [--lessons] [--quiz]
 *     [--courses a,b] [--categories a,b] [--max-lessons N] [--max-attempts N] [--gap-ms N] [--resume <run folder>]
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";

import { NHPREP_ORIGIN } from "./gate";
import { EXPORTER_VERSION, SCHEMA, categoryDone, keyedQuestions, lessonRow, sha256, sourceDay, stripSourceRefs, trainerHints } from "./parse";
import { assertOutsideAnyRepo } from "./paths";
import { type Session, errorText, openSession, sleep } from "./session";

const argv = process.argv.slice(2);
const flag = (name: string): boolean => argv.includes(`--${name}`);
const option = (name: string): string | undefined => {
  const at = argv.indexOf(`--${name}`);
  return at >= 0 ? argv[at + 1] : undefined;
};
const list = (name: string): Set<string> | null => (option(name) ? new Set(option(name)?.split(",").map((item) => item.trim())) : null);
const number = (name: string): number | null => (option(name) ? Number(option(name)) : null);

const ROOT = assertOutsideAnyRepo(process.env.NHPREP_EXPORT_DIR ?? "C:\\Users\\test\\nhprep-data\\export");
const runDir = assertOutsideAnyRepo(option("resume") ?? join(ROOT, new Date().toISOString().replace(/[:.]/g, "-")));
mkdirSync(runDir, { recursive: true });
const exportFile = join(runDir, "export.jsonl");
const ledgerFile = join(ROOT, "ledger.json");

type Row = Record<string, unknown> & { type: string };
const existing: Row[] = existsSync(exportFile) ? readFileSync(exportFile, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line) as Row) : [];
const write = (row: Row): void => appendFileSync(exportFile, `${JSON.stringify(row)}\n`);
const readLedger = (): { quizAttempts: number } => (existsSync(ledgerFile) ? (JSON.parse(readFileSync(ledgerFile, "utf8")) as { quizAttempts: number }) : { quizAttempts: 0 });
const bumpLedger = (): void => writeFileSync(ledgerFile, JSON.stringify({ quizAttempts: readLedger().quizAttempts + 1 }));

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const ARTIFACTS = new RegExp(`^/api/lesson/(${UUID})/artifacts$`);
const VIDEO_URL = new RegExp(`^/api/video-url/(${UUID})$`);
const BUNNY_EMBED = new RegExp(`/embed/(\\d+)/(${UUID})`);

/** The page's own study-aid and video calls, as they arrive (set by the JSON listener, read through `captured()`). */
type Captured = { artifacts: { lessonId: string; body: unknown } | null; videoId: string | null };
const seen: Captured = { artifacts: null, videoId: null };
// A function call, so TypeScript does not keep the `null` it saw assigned before a wait.
const captured = (): Captured => seen;

async function videoCourses(page: Page): Promise<{ reported: number | null; slugs: string[] }> {
  await page.goto(`${NHPREP_ORIGIN}/courses?type=video`, { waitUntil: "domcontentloaded" });
  const slugs = new Set<string>();
  let reported: number | null = null;
  for (let pageNo = 0; pageNo < 20; pageNo++) {
    await page.locator("a[href^='/course/']").first().waitFor({ timeout: 90_000 });
    const found = await page.evaluate(() => ({
      text: document.body.innerText,
      hrefs: [...document.querySelectorAll("main a[href^='/course/']")].map((a) => a.getAttribute("href") ?? ""),
    }));
    reported ??= Number(/Explore our library of (\d+)/.exec(found.text)?.[1] ?? Number.NaN) || null;
    const before = slugs.size;
    for (const href of found.hrefs) {
      const slug = /^\/course\/([a-z0-9-]+)$/.exec(href)?.[1];
      if (slug) slugs.add(slug);
    }
    const next = page.getByRole("link", { name: /^Next/ }).or(page.getByRole("button", { name: /^Next/ })).first();
    if (slugs.size === before || !(await next.isVisible().catch(() => false)) || (await next.isDisabled().catch(() => true))) break;
    await next.click();
    await page.waitForLoadState("domcontentloaded");
    await sleep(1500);
  }
  return { reported, slugs: [...slugs] };
}

async function readCourse(page: Page, slug: string) {
  await page.goto(`${NHPREP_ORIGIN}/course/${slug}`, { waitUntil: "domcontentloaded" });
  await page.locator("h1").first().waitFor({ timeout: 90_000 });
  const found = await page.evaluate((courseSlug) => {
    const about = [...document.querySelectorAll("h2, h3")].find((el) => /about this course/i.test(el.textContent ?? ""));
    const description = about?.parentElement ? (about.parentElement.innerText ?? "").replace(about.textContent ?? "", "").trim() : "";
    const rows = new Map<string, string[]>();
    for (const a of document.querySelectorAll(`a[href^="/course/${courseSlug}/lesson/"]`)) {
      const href = a.getAttribute("href") ?? "";
      const leaves = [...a.querySelectorAll("*")].filter((el) => el.children.length === 0).map((el) => (el.textContent ?? "").trim());
      if (leaves.some((leaf) => /^\d{1,4}$/.test(leaf)) && !rows.has(href)) rows.set(href, leaves);
    }
    return { title: document.querySelector("h1")?.textContent?.trim() ?? "", description, rows: [...rows.entries()] };
  }, slug);
  const lessons = found.rows.map(([href, leaves]) => lessonRow(href.split("/lesson/")[1] ?? "", leaves)).sort((a, b) => (a.position ?? 9999) - (b.position ?? 9999));
  return { title: found.title, description: found.description, lessons };
}

async function readLesson(session: Session, courseSlug: string, lessonSlug: string) {
  const { page } = session;
  const path = `/course/${courseSlug}/lesson/${lessonSlug}`;
  seen.artifacts = null;
  seen.videoId = null;
  const link = page.locator(`a[href="${path}"]`).first();
  if (new URL(page.url()).pathname.startsWith(`/course/${courseSlug}/lesson/`) && (await link.isVisible().catch(() => false))) await link.click();
  else await page.goto(`${NHPREP_ORIGIN}${path}`, { waitUntil: "domcontentloaded" });
  await page.waitForURL((url) => url.pathname === path, { timeout: 90_000 });
  for (const started = Date.now(); !captured().artifacts && Date.now() - started < 90_000; ) await sleep(250);
  for (const started = Date.now(); !captured().videoId && Date.now() - started < 4_000; ) await sleep(250);
  const { artifacts, videoId } = captured();
  const dom = await page.evaluate(() => ({
    title: document.querySelector("h1")?.textContent?.trim() ?? "",
    of: /Lesson (\d+) of (\d+)/.exec(document.body.innerText)?.slice(1, 3) ?? null,
    iframe: [...document.querySelectorAll("iframe")].map((el) => el.src).find((src) => src.includes("mediadelivery")) ?? null,
  }));
  const bunny = dom.iframe ? BUNNY_EMBED.exec(new URL(dom.iframe).pathname) : null;
  const video = videoId ? { provider: "nhprep-mp4", videoId } : bunny ? { provider: "bunny", libraryId: Number(bunny[1]), videoId: bunny[2] } : null;
  return { dom, video, artifacts };
}

async function exportLessons(session: Session, summary: Record<string, unknown>): Promise<boolean> {
  const onlyCourses = list("courses");
  const maxLessons = number("max-lessons");
  const doneLessons = new Set(existing.filter((row) => row.type === "lesson").map((row) => `${row.course}/${row.slug}`));
  const doneCourses = new Set(existing.filter((row) => row.type === "course").map((row) => String(row.slug)));
  const inventory = await videoCourses(session.page);
  session.log("inventory", { reported: inventory.reported, found: inventory.slugs.length });
  let complete = inventory.reported !== null && inventory.reported === inventory.slugs.length && !onlyCourses && maxLessons === null;
  let exported = 0;
  let failed = 0;
  for (const slug of inventory.slugs.filter((item) => !onlyCourses || onlyCourses.has(item))) {
    const course = await readCourse(session.page, slug);
    if (!doneCourses.has(slug)) {
      write({ type: "course", schema: SCHEMA, slug, title: course.title, description: course.description, trainerHints: trainerHints(slug, course.title, course.description), lessonCount: course.lessons.length });
    }
    for (const row of course.lessons) {
      if (doneLessons.has(`${slug}/${row.slug}`)) continue;
      if (maxLessons !== null && exported >= maxLessons) return false;
      try {
        const lesson = await readLesson(session, slug, row.slug);
        if (!lesson.artifacts) throw new Error("no study-aid response");
        write({
          type: "lesson",
          schema: SCHEMA,
          external_id: lesson.artifacts.lessonId,
          course: slug,
          slug: row.slug,
          title: lesson.dom.title || row.title,
          position: lesson.dom.of ? Number(lesson.dom.of[0]) : row.position,
          day: sourceDay(row.slug),
          lessonType: row.type,
          durationSeconds: row.durationSeconds,
          video: lesson.video,
          aids: stripSourceRefs(lesson.artifacts.body),
        });
        exported++;
      } catch (error) {
        failed++;
        complete = false;
        session.log("lesson-failed", { course: slug, lesson: row.slug, message: errorText(error) });
      }
    }
    session.log("course-done", { course: slug, lessons: course.lessons.length, exported, failed });
  }
  Object.assign(summary, { inventory: { reported: inventory.reported, found: inventory.slugs.length }, lessonsExported: exported, lessonsFailed: failed });
  return complete && failed === 0;
}

async function quizCategories(page: Page): Promise<string[]> {
  await page.goto(`${NHPREP_ORIGIN}/quiz`, { waitUntil: "domcontentloaded" });
  await page.locator("a[href^='/quiz/']").first().waitFor({ timeout: 90_000 });
  const hrefs = await page.evaluate(() => [...document.querySelectorAll("a[href^='/quiz/']")].map((a) => a.getAttribute("href") ?? ""));
  return [...new Set(hrefs.map((href) => /^\/quiz\/([a-z0-9-]+)$/.exec(href)?.[1]).filter((slug): slug is string => Boolean(slug)))];
}

type Draw = { status: number; questions: { id: string; options: string[] }[]; categoryName: string; totalAvailable: number | null };
type Submitted = { status: number; body: unknown };

/** One attempt through the page's own fetch (the CSRF value stays in the page): draw 20, answer each with its first option, submit. */
async function attempt(session: Session, slug: string): Promise<{ draw: Draw; submitted: Submitted }> {
  const draw = await session.page.evaluate(async (category) => {
    const response = await fetch(`/api/quiz/${category}/questions`);
    const body = (await response.json().catch(() => ({}))) as { questions?: { id: string; options: string[] }[]; categoryName?: string; totalAvailable?: number };
    return { status: response.status, questions: body.questions ?? [], categoryName: body.categoryName ?? category, totalAvailable: body.totalAvailable ?? null };
  }, slug);
  if (draw.status !== 200 || draw.questions.length === 0) throw new Error(`draw ${draw.status}`);
  session.allowWrite(`POST /api/quiz/${slug}/submit`, 1);
  bumpLedger();
  const submitted = await session.page.evaluate(
    async ({ category, answers, seconds }) => {
      const csrf = ((await (await fetch("/api/csrf")).json()) as { csrfToken?: string }).csrfToken ?? "";
      const response = await fetch(`/api/quiz/${category}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ answers, timeTakenSeconds: seconds }),
      });
      return { status: response.status, body: (await response.json().catch(() => null)) as unknown };
    },
    { category: slug, answers: draw.questions.map((question) => ({ questionId: question.id, selectedOption: question.options[0] ?? "" })), seconds: 60 + Math.floor(Math.random() * 120) },
  );
  return { draw, submitted };
}

async function exportQuiz(session: Session, summary: Record<string, unknown>): Promise<boolean> {
  const only = list("categories");
  const maxAttempts = number("max-attempts");
  const seenIds = new Map<string, Set<string>>();
  for (const row of existing.filter((item) => item.type === "quiz_question")) {
    const set = seenIds.get(String(row.category)) ?? new Set<string>();
    set.add(String(row.id));
    seenIds.set(String(row.category), set);
  }
  const attemptsBefore = new Map(existing.filter((row) => row.type === "quiz_coverage").map((row) => [String(row.category), Number(row.attempts)]));
  const categories = (await quizCategories(session.page)).filter((slug) => !only || only.has(slug));
  let complete = !only;
  let runAttempts = 0;
  for (const slug of categories) {
    const ids = seenIds.get(slug) ?? new Set<string>();
    let attempts = attemptsBefore.get(slug) ?? 0;
    let reported: number | null = null;
    let stale = 0;
    while (!categoryDone(ids.size, reported, stale)) {
      if (maxAttempts !== null && runAttempts >= maxAttempts) {
        complete = false;
        break;
      }
      try {
        const { draw, submitted } = await attempt(session, slug);
        runAttempts++;
        attempts++;
        reported = draw.totalAvailable;
        if (submitted.status !== 200) throw new Error(`submit ${submitted.status}`);
        let fresh = 0;
        for (const question of keyedQuestions(slug, draw.categoryName, submitted.body)) {
          if (ids.has(question.id)) continue;
          ids.add(question.id);
          write(question);
          fresh++;
        }
        stale = fresh === 0 ? stale + 1 : 0;
      } catch (error) {
        session.log("attempt-failed", { category: slug, message: errorText(error) });
        stale++;
        await sleep(5000);
      }
    }
    write({ type: "quiz_coverage", schema: SCHEMA, category: slug, attempts, seen: ids.size, reported });
    session.log("category-done", { category: slug, attempts, seen: ids.size, reported });
    if (reported === null || ids.size < reported) complete = false;
  }
  Object.assign(summary, { quizCategories: categories.length, quizAttemptsThisRun: runAttempts });
  return complete;
}

function writeManifest(startedAt: string, sections: Record<string, boolean>, summary: Record<string, unknown>, session: Session): void {
  const text = existsSync(exportFile) ? readFileSync(exportFile, "utf8") : "";
  const rows = text.split("\n").filter(Boolean).map((line) => JSON.parse(line) as Row);
  const counts: Record<string, number> = {};
  for (const row of rows) counts[row.type] = (counts[row.type] ?? 0) + 1;
  const coverage = new Map(rows.filter((row) => row.type === "quiz_coverage").map((row) => [row.category, row]));
  const stats = session.stats();
  const manifest = {
    schema: SCHEMA,
    exporterVersion: EXPORTER_VERSION,
    startedAt,
    finishedAt: new Date().toISOString(),
    complete: sections,
    sha256: sha256(text),
    counts,
    coverage: [...coverage.values()],
    ledger: readLedger(),
    gapMs: session.gapMs,
    requests: stats.paced,
    maxRequestsPerSecond: stats.maxPerSecond,
    ...summary,
  };
  writeFileSync(join(runDir, "manifest.json"), JSON.stringify(manifest, null, 2));
}

async function main(): Promise<void> {
  if (!flag("lessons") && !flag("quiz")) throw new Error("say what to export: --lessons and/or --quiz");
  const startedAt = new Date().toISOString();
  const session = await openSession({ logFile: join(runDir, "run.log"), gapMs: number("gap-ms") ?? undefined, videoUrlOnce: true });
  session.onJson((path, body) => {
    const artifacts = ARTIFACTS.exec(path);
    if (artifacts?.[1]) seen.artifacts = { lessonId: artifacts[1], body };
    const video = VIDEO_URL.exec(path);
    if (video?.[1]) seen.videoId = video[1];
  });
  const sections: Record<string, boolean> = {};
  const summary: Record<string, unknown> = { runDir };
  try {
    const signIn = await session.googleLogin();
    if (!signIn.signedIn) throw new Error("not signed in");
    session.log("signed-in", {});
    if (flag("lessons")) sections.lessons = await exportLessons(session, summary);
    if (flag("quiz")) sections.quiz = await exportQuiz(session, summary);
  } finally {
    writeManifest(startedAt, sections, summary, session);
    await session.close();
  }
}

main().catch((error: unknown) => {
  writeFileSync(join(runDir, "fatal.txt"), errorText(error));
  process.exitCode = 1;
});
