/**
 * NH programs in the web app, as plain data: the five areas and the program bar's tabs, which program and area an
 * address is in, the month in `?month=`, the claims My Programs shows as ended, and what a program chat says about its
 * place. One module, so My Programs, the program bar, the header, the chat header, the welcome and the composer never
 * word or link things differently.
 * Nothing here decides access: the API does, on every request.
 */

import type { ProgramClaim } from "@/lib/api/programs";
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

export type ProgramTab = { area: ProgramArea | null; label: string; href: string };

/** The program bar's tabs: Overview (the program's own page, area null), then the five areas. */
export function programTabs(slug: string): ProgramTab[] {
  return [
    { area: null, label: "Overview", href: `/programs/${slug}` },
    ...PROGRAM_AREAS.map((item) => ({ area: item.path, label: item.label, href: `/programs/${slug}/${item.path}` })),
  ];
}

/**
 * The claims My Programs shows as "Access ended": expired or revoked, once per program (the newest, as the API lists
 * them newest first), and never a program that another of the account's invoices still opens.
 */
export function endedClaims(claims: readonly ProgramClaim[], openSlugs: readonly string[]): ProgramClaim[] {
  const seen = new Set(openSlugs);
  const ended: ProgramClaim[] = [];
  for (const claim of claims) {
    if (seen.has(claim.program.slug)) continue;
    seen.add(claim.program.slug); // the newest claim speaks for its program
    if (claim.state !== "active") ended.push(claim);
  }
  return ended;
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
  placeholder: string;
  tabTitle: string;
};

type ProgramTitles = { title: string; months: { month: number; title: string }[] };


/** What a program chat says about its place. Before the program's titles arrive (null), nothing is invented. */
export function placeText(place: ChatPlace, program: ProgramTitles | null): PlaceText {
  if (place.month === null) {
    return {
      label: null,
      title: "Program Q&A",
      back: null,
      greeting: "Ask anything about your program.",
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
      placeholder: "Answer the interviewer, or ask for the next question…",
      tabTitle: `Month ${n} mock interview`,
    };
  }
  return {
    label: topic ? `Month ${n}` : null,
    title: topic ?? `Month ${n}`,
    back: { href: `/programs/${place.program}/modules`, label: "Course Module" },
    greeting: `What shall we learn in Month ${n}?`,
    placeholder: `Ask your Month ${n} tutor…`,
    tabTitle: `Month ${n} tutor`,
  };
}
