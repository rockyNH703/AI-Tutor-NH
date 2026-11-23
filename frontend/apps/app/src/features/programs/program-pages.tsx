"use client";

import { Button } from "@v1/ui/button";
import { BookOpen, Gamepad2, GraduationCap, MessageCircle, Mic, PlayCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useState } from "react";

import { useStores } from "@/features/chat/chat-context";
import { withRequest } from "@/features/my-learning/ask-in-chat";
import { NhprepSlot } from "@/features/nhprep/nhprep-slot";
import { ApiError, describeError } from "@/lib/api/errors";
import { getNhprepLessons } from "@/lib/api/nhprep";
import {
  type Program,
  type ProgramMaterial,
  type ProgramMonthDetail,
  getProgram,
  getProgramMonth,
} from "@/lib/api/programs";
import type { NhprepLessonSummary, Uuid } from "@/lib/api/types";
import { programChatPath } from "./program-chat-header";

type Load<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "missing" } | { status: "error"; error: unknown };

/** One read, with its states: loading, ready, missing (a 404: not this account's) or failed (Try again). */
function useLoad<T>(load: () => Promise<T>, key: string): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    load().then(
      (data) => live && setState({ status: "ready", data }),
      (error) =>
        live &&
        setState(error instanceof ApiError && error.code === "not_found" ? { status: "missing" } : { status: "error", error }),
    );
    return () => {
      live = false;
    };
  }, [key, attempt]);
  return [state, useCallback(() => setAttempt((n) => n + 1), [])];
}

function Page({ title, intro, children }: { title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <div className="page-container py-6">
      <h2 className="text-xl font-semibold text-foreground">{title}</h2>
      {intro && <p className="mt-1 max-w-prose text-sm text-text-secondary">{intro}</p>}
      <div className="mt-6">{children}</div>
    </div>
  );
}

function States<T>({ state, retry, children }: { state: Load<T>; retry: () => void; children: (data: T) => ReactNode }) {
  if (state.status === "loading") return <p className="text-sm text-text-secondary" role="status">Loading…</p>;
  if (state.status === "missing")
    return <p className="text-sm text-text-secondary">This isn't available to your account.</p>;
  if (state.status === "error")
    return (
      <div className="flex items-center gap-3 text-sm" role="alert">
        <span className="text-text-secondary">{describeError(state.error).message}</span>
        <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={retry}>
          Try again
        </Button>
      </div>
    );
  return <>{children(state.data)}</>;
}

function useProgram(slug: string) {
  return useLoad(() => getProgram(slug), slug);
}

function materialLine(material: ProgramMaterial): string {
  const parts = [
    material.documents ? `${material.documents} book${material.documents === 1 ? "" : "s"} and guides` : "",
    material.lessons ? `${material.lessons} lessons` : "",
    material.questions ? `${material.questions} practice questions` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Material coming soon";
}

const AREAS = [
  { path: "modules", label: "Course Module", text: "Your months, each with its own AI tutor", icon: GraduationCap },
  { path: "qna", label: "QnA", text: "Ask about anything in your program", icon: MessageCircle },
  { path: "quizzes", label: "Quizzes & Games", text: "Practice questions and flashcards by month", icon: Gamepad2 },
  { path: "interviews", label: "Mock Interviews", text: "An interviewer for each month's topics", icon: Mic },
  { path: "videos", label: "Videos & Links", text: "NH lesson videos for each month", icon: PlayCircle },
] as const;

export function ProgramLanding({ slug }: { slug: string }) {
  const [state, retry] = useProgram(slug);
  return (
    <States state={state} retry={retry}>
      {(program) => (
        <Page title={program.title} intro="Your NetworkersHome program. Pick where to start.">
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {AREAS.map((area) => (
              <li key={area.path}>
                <Link
                  href={`/programs/${slug}/${area.path}`}
                  className="focus-ring flex h-full min-h-11 items-start gap-3 rounded-xl border border-border p-4 hover:bg-surface-2"
                >
                  <area.icon aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-brand" />
                  <span>
                    <span className="block text-sm font-semibold text-foreground">{area.label}</span>
                    <span className="block text-sm text-text-secondary">{area.text}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </Page>
      )}
    </States>
  );
}

function MonthGrid({ program, href, note }: { program: Program; href: (month: number) => string; note?: (material: ProgramMaterial) => string }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {program.months.map((month) => (
        <li key={month.month}>
          <Link href={href(month.month)} className="focus-ring flex h-full min-h-11 flex-col gap-1 rounded-xl border border-border p-4 hover:bg-surface-2">
            <span className="text-xs font-semibold uppercase tracking-[0.06em] text-text-secondary">Month {month.month}</span>
            <span className="text-lg font-semibold leading-snug text-foreground">{month.title}</span>
            <span className="text-xs text-text-secondary">{(note ?? materialLine)(month.material)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function ProgramModules({ slug }: { slug: string }) {
  const [state, retry] = useProgram(slug);
  return (
    <States state={state} retry={retry}>
      {(program) => (
        <Page title="Course Module" intro={`${program.title}: open a month to learn with its own AI tutor.`}>
          <MonthGrid program={program} href={(month) => programChatPath(slug, month, "tutor")} />
        </Page>
      )}
    </States>
  );
}

export function ProgramInterviews({ slug }: { slug: string }) {
  const [state, retry] = useProgram(slug);
  return (
    <States state={state} retry={retry}>
      {(program) => (
        <Page title="Mock Interviews" intro="Pick a month: its interviewer asks one question at a time and gives feedback.">
          <MonthGrid
            program={program}
            href={(month) => programChatPath(slug, month, "interview")}
            note={(material) => (material.documents || material.lessons ? "Start a mock interview" : "Material coming soon")}
          />
        </Page>
      )}
    </States>
  );
}

function MonthPicker({ program, month, onPick }: { program: Program; month: number; onPick: (month: number) => void }) {
  return (
    <div className="mb-5 flex flex-wrap gap-2" role="group" aria-label="Month">
      {program.months.map((item) => (
        <Button
          key={item.month}
          type="button"
          size="sm"
          variant={item.month === month ? "default" : "outline"}
          aria-pressed={item.month === month}
          className="min-h-11"
          onClick={() => onPick(item.month)}
        >
          Month {item.month}
        </Button>
      ))}
    </div>
  );
}

function useMonth(slug: string, month: number) {
  return useLoad<ProgramMonthDetail>(() => getProgramMonth(slug, month), `${slug}/${month}`);
}

export function ProgramQuizzes({ slug }: { slug: string }) {
  const [state, retry] = useProgram(slug);
  const [month, setMonth] = useState(1);
  return (
    <States state={state} retry={retry}>
      {(program) => (
        <Page title="Quizzes & Games" intro="Quiz yourself on a month's topics in that month's chat. Flashcards come with its NH lessons.">
          <MonthPicker program={program} month={month} onPick={setMonth} />
          <MonthTopics slug={slug} month={month} />
        </Page>
      )}
    </States>
  );
}

function MonthTopics({ slug, month }: { slug: string; month: number }) {
  const [state, retry] = useMonth(slug, month);
  const { chat } = useStores();
  const router = useRouter();
  const quiz = (title: string) => {
    // The request goes into the month chat's own draft (as "Ask in chat" does elsewhere); nothing is sent by itself.
    const key = chat.freshDraftKey({ program: slug, month, mode: "tutor" });
    chat.setDraft(key, withRequest(chat.getDraft(key), `Quiz me on ${title}`));
    router.push(programChatPath(slug, month, "tutor"));
  };
  return (
    <States state={state} retry={retry}>
      {(detail) =>
        !detail.quizAvailable ? (
          <p className="text-sm text-text-secondary">No practice questions are mapped to this month yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border rounded-xl border border-border">
            {detail.topics.map((topic) => (
              <li key={topic.topicId} className="flex flex-wrap items-center gap-3 px-4 py-2">
                <span className="min-w-0 flex-1">
                  <span className="block text-sm text-foreground">{topic.title}</span>
                  <span className="block text-xs text-text-secondary">
                    {topic.book} · {topic.chapter}
                  </span>
                </span>
                <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={() => quiz(topic.title)}>
                  Quiz me
                </Button>
              </li>
            ))}
          </ul>
        )
      }
    </States>
  );
}

export function ProgramVideos({ slug }: { slug: string }) {
  const [state, retry] = useProgram(slug);
  const [month, setMonth] = useState(1);
  return (
    <States state={state} retry={retry}>
      {(program) => (
        <Page title="Videos & Links" intro="NetworkersHome lesson videos, notes and cheat sheets for each month.">
          <MonthPicker program={program} month={month} onPick={setMonth} />
          <MonthLessons slug={slug} month={month} />
        </Page>
      )}
    </States>
  );
}

function MonthLessons({ slug, month }: { slug: string; month: number }) {
  const [state, retry] = useMonth(slug, month);
  return (
    <States state={state} retry={retry}>
      {(detail) =>
        detail.lessonIds.length === 0 ? (
          <p className="text-sm text-text-secondary">No NH videos are mapped to this month yet.</p>
        ) : (
          <LessonList ids={detail.lessonIds} />
        )
      }
    </States>
  );
}

function LessonList({ ids }: { ids: Uuid[] }) {
  const [shown, setShown] = useState(20);
  const [lessons, setLessons] = useState<NhprepLessonSummary[] | null>(null);
  const [open, setOpen] = useState<Uuid | null>(null);
  useEffect(() => {
    let live = true;
    const wanted = ids.slice(0, shown);
    const batches = [];
    for (let start = 0; start < wanted.length; start += 10) batches.push(getNhprepLessons(wanted.slice(start, start + 10)));
    Promise.all(batches).then(
      (lists) => live && setLessons(lists.flatMap((list) => list.items)),
      () => live && setLessons([]),
    );
    return () => {
      live = false;
    };
  }, [ids, shown]);
  if (lessons === null) return <p className="text-sm text-text-secondary" role="status">Loading…</p>;
  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col divide-y divide-border rounded-xl border border-border">
        {lessons.map((lesson) => (
          <li key={lesson.id}>
            <button
              type="button"
              aria-expanded={open === lesson.id}
              onClick={() => setOpen(open === lesson.id ? null : lesson.id)}
              className="focus-ring flex min-h-11 w-full items-center gap-3 px-4 py-2 text-left hover:bg-surface-2"
            >
              <BookOpen aria-hidden className="h-4 w-4 shrink-0 text-brand" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-foreground">{lesson.title}</span>
                <span className="block truncate text-xs text-text-secondary">{lesson.course.title}</span>
              </span>
            </button>
            {open === lesson.id && (
              <div className="px-4 pb-4">
                <NhprepSlot
                  eager
                  card={{
                    version: 1,
                    views: ["video", "notes"],
                    status: "confident",
                    primaryLessonId: lesson.id,
                    alternativeLessonIds: [],
                    matchedItemId: null,
                  }}
                />
              </div>
            )}
          </li>
        ))}
      </ul>
      {shown < ids.length && (
        <Button type="button" variant="outline" className="min-h-11 self-start" onClick={() => setShown(shown + 20)}>
          Show more lessons
        </Button>
      )}
    </div>
  );
}
