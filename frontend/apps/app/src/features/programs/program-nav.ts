/**
 * NH programs in the web app, as plain data: the five areas, which program and area an address is in, a program
 * group's state in the sidebar, the month in `?month=`, and what a program chat says about its place. One module, so
 * the sidebar, the header, the chat header, the welcome and the composer never word or link things differently.
 * Nothing here decides access: the API does, on every request.
 */

import type { ChatPlace } from "@/lib/api/types";
import { monthNumber } from "./route-params";

export const PROGRAM_AREAS = [
  { path: "modules", label: "Course Module" },
  { path: "qna", label: "QnA" },
  { path: "quizzes", label: "Quizzes & Games" },
  { path: "interviews", label: "Mock Interviews" },
  { path: "videos", label: "Videos & Links" },
] as const;

export type ProgramArea = (typeof PROGRAM_AREAS)[number]["path"];

/** Where a program chat lives: /programs/<slug>/month/<n> (or /qna), /interview for a mock interview. */
export function programChatPath(slug: string, month: number | null, mode: ChatPlace["mode"]): string {
  const base = `/programs/${slug}/${month === null ? "qna" : `month/${month}`}`;
  return mode === "interview" ? `${base}/interview` : base;
}

/** The program and area of an address (area null: the program's own page); null outside /programs/<slug>. */
export function programRoute(pathname: string | null): { slug: string; area: ProgramArea | null } | null {
  const match = /^\/programs\/([^/]+)(?:\/(.*))?$/.exec(pathname ?? "");
  const slug = match?.[1];
  if (!slug) return null;
  const rest = match[2] ?? "";
  // A month's tutor chat belongs to Course Module, its mock interview to Mock Interviews.
  if (rest.startsWith("month/")) return { slug, area: /^month\/[^/]+\/interview(\/|$)/.test(rest) ? "interviews" : "modules" };
  const first = rest.split("/")[0];
  return { slug, area: PROGRAM_AREAS.find((item) => item.path === first)?.path ?? null };
}

/** The sidebar's saved program groups ({slug: expanded}); anything unreadable is no choice. */
export function parseSaved(raw: string | null): Record<string, boolean> {
  if (!raw) return {};
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, boolean] => typeof entry[1] === "boolean"));
  } catch {
    return {};
  }
}

/** A saved choice wins; without one, a program's group is open while you are inside it. */
export function isExpanded(saved: Record<string, boolean>, slug: string, insideSlug: string | null): boolean {
  return saved[slug] ?? slug === insideSlug;
}

/** The month in `?month=`, if the program has it; otherwise its first month. */
export function monthFromParam(value: string | string[] | null | undefined, months: readonly number[]): number {
  const number = typeof value === "string" ? monthNumber(value) : null;
  return number !== null && months.includes(number) ? number : (months[0] ?? 1);
}

export type PlaceText = {
  /** A short chip leading the title ("Month 2"); null when the title says it all. */
  label: string | null;
  title: string;
  back: { href: string; label: string } | null;
  greeting: string;
  line: string;
  /** Sent as they are: each names a real title, so the tutor's search finds that material. */
  suggestions: string[];
  placeholder: string;
  tabTitle: string;
};

type ProgramTitles = { title: string; months: { month: number; title: string }[] };

const SOURCES = "NetworkersHome material and shows its sources.";

/** What a program chat says about its place. Before the program's titles arrive (null), nothing is invented. */
export function placeText(place: ChatPlace, program: ProgramTitles | null): PlaceText {
  if (place.month === null) {
    return {
      label: null,
      title: "Program Q&A",
      back: null,
      greeting: "Ask anything about your program.",
      line: `Your tutor answers from every month's ${SOURCES}`,
      suggestions: (program?.months ?? []).slice(0, 2).map((item) => `Give me an overview of Month ${item.month}: ${item.title}`),
      placeholder: "Ask about any month of your program…",
      tabTitle: "Program Q&A",
    };
  }
  const n = place.month;
  const topic = program?.months.find((item) => item.month === n)?.title ?? null;
  if (place.mode === "interview") {
    return {
      label: `Month ${n}`,
      title: topic ? `Mock interview: ${topic}` : "Mock interview",
      back: { href: `/programs/${place.program}/interviews`, label: "Mock Interviews" },
      greeting: `Ready for your Month ${n} mock interview?`,
      line: "One question at a time, with feedback on each answer.",
      suggestions: ["Start my mock interview"],
      placeholder: "Answer the interviewer, or ask for the next question…",
      tabTitle: `Month ${n} mock interview`,
    };
  }
  return {
    label: topic ? `Month ${n}` : null,
    title: topic ?? `Month ${n}`,
    back: { href: `/programs/${place.program}/modules`, label: "Course Module" },
    greeting: `What shall we learn in Month ${n}?`,
    line: `Your tutor answers from this month's ${SOURCES}`,
    suggestions: topic ? [`Give me an overview of ${topic}`, `What are the key concepts in ${topic}?`] : [],
    placeholder: `Ask your Month ${n} tutor…`,
    tabTitle: `Month ${n} tutor`,
  };
}
