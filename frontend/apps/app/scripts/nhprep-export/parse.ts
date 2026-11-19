/** Export v1 (frozen 2026-11-19, C:\Users\test\netsec-handoffs\nhprep.md): pure helpers, unit-tested. */
import { createHash } from "node:crypto";

export const SCHEMA = 1;
export const EXPORTER_VERSION = "nhprep-export/1";

/** "1h 12m", "34m 22s", "45s" → seconds; anything else → null. */
export function durationSeconds(text: string): number | null {
  const match = /^\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?\s*$/.exec(text);
  if (!match || !(match[1] || match[2] || match[3])) return null;
  return Number(match[1] ?? 0) * 3600 + Number(match[2] ?? 0) * 60 + Number(match[3] ?? 0);
}

export type LessonRow = { slug: string; position: number | null; title: string; type: string | null; durationSeconds: number | null };

const TYPES = new Set(["video", "lab", "text", "article", "quiz", "pdf"]);
const NOISE = /^(free preview|locked|preview|completed|new)$/i;

/** One row of a course page's lesson list, from its leaf texts in order (e.g. ["2", "Title", "video", "1h 12m", "Free Preview"]). */
export function lessonRow(slug: string, leaves: readonly string[]): LessonRow {
  const texts = leaves.map((text) => text.trim()).filter(Boolean);
  const position = texts.find((text) => /^\d{1,4}$/.test(text));
  const type = texts.find((text) => TYPES.has(text.toLowerCase()));
  const duration = texts.find((text) => durationSeconds(text) !== null);
  const rest = texts.filter((text) => text !== position && text !== type && text !== duration && !NOISE.test(text));
  const title = rest.reduce((best, text) => (text.length > best.length ? text : best), "");
  return { slug, position: position ? Number(position) : null, title, type: type?.toLowerCase() ?? null, durationSeconds: duration ? durationSeconds(duration) : null };
}

/** "day-07-…" → 7; the slug's day is not the lesson's position (spec r2-nhprep-public-deep B5). */
export function sourceDay(slug: string): number | null {
  const match = /^day-0*(\d{1,3})-/.exec(slug);
  return match ? Number(match[1]) : null;
}

/** Raw trainer evidence for NetSec's trainers table: never resolved here. */
export function trainerHints(slug: string, title: string, description: string) {
  return {
    titleParens: /\(([^)]+)\)\s*$/.exec(title)?.[1]?.trim() ?? null,
    aboutBy: /\bBY\s+([A-Za-z][A-Za-z.]*)/i.exec(description)?.[1] ?? null,
    slugToken: slug.split("-").at(-1) ?? null,
  };
}

/** NHprep's artifacts JSON as served, minus every `sourceRefs` (transcript pointers NetSec does not use). */
export function stripSourceRefs(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripSourceRefs);
  if (value === null || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).filter(([key]) => key !== "sourceRefs").map(([key, item]) => [key, stripSourceRefs(item)]));
}

export type QuizQuestion = {
  type: "quiz_question";
  schema: number;
  category: string;
  categoryName: string;
  id: string;
  format: "single";
  question: string;
  options: string[];
  correctOption: string;
  correctIndex: number | null;
  explanation: string;
  difficulty: null;
  lessonExternalId: null;
};

type SubmittedQuestion = { id?: unknown; question?: unknown; options?: unknown; correctOption?: unknown; explanation?: unknown };

/** The keyed questions of one submit response; a question without an id, options or key is dropped (never imported). */
export function keyedQuestions(category: string, categoryName: string, body: unknown): QuizQuestion[] {
  const questions = (body as { questions?: SubmittedQuestion[] } | null)?.questions;
  if (!Array.isArray(questions)) return [];
  return questions.flatMap((item) => {
    const { id, question, options, correctOption, explanation } = item ?? {};
    if (typeof id !== "string" || typeof question !== "string" || typeof correctOption !== "string" || !correctOption) return [];
    if (!Array.isArray(options) || !options.every((option): option is string => typeof option === "string")) return [];
    const index = options.indexOf(correctOption);
    return [
      {
        type: "quiz_question" as const,
        schema: SCHEMA,
        category,
        categoryName,
        id,
        format: "single" as const,
        question,
        options,
        correctOption,
        correctIndex: index >= 0 ? index : null,
        explanation: typeof explanation === "string" ? explanation : "",
        difficulty: null,
        lessonExternalId: null,
      },
    ];
  });
}

/** The per-category stop rule (plan §8.2): every reported question seen, or 3 attempts in a row that add nothing. */
export function categoryDone(seen: number, reported: number | null, attemptsWithoutNew: number): boolean {
  return (reported !== null && seen >= reported) || attemptsWithoutNew >= 3;
}

export const sha256 = (data: string | Buffer): string => createHash("sha256").update(data).digest("hex");
