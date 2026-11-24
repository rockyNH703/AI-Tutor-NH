"use client";

import { Button } from "@v1/ui/button";
import { EmptyState, ErrorState } from "@v1/ui/empty-state";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { BookOpen, Gamepad2, GraduationCap, Lock, MessageCircle, Mic, PlayCircle } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useState } from "react";

import { useStores } from "@/features/chat/chat-context";
import { QuizzesSwitch } from "@/features/friendzone/quizzes-switch";
import { withRequest } from "@/features/my-learning/ask-in-chat";
import { NhprepSlot } from "@/features/nhprep/nhprep-slot";
import { ApiError, describeError } from "@/lib/api/errors";
import { getNhprepLessons } from "@/lib/api/nhprep";
import { type BankTopic, type Program, type ProgramMaterial, type ProgramMonthDetail, getProgramMonth } from "@/lib/api/programs";
import type { NhprepLessonSummary, Uuid } from "@/lib/api/types";
import { PROGRAM_AREAS, type ProgramArea, monthFromParam, programChatPath } from "./program-nav";
import { refreshPrograms, usePrograms } from "./use-programs";

/**
 * The program's own pages: its landing page, Course Module, Mock Interviews, Quizzes & Games and Videos & Links.
 * Every page is one scrolling region (the app frame clips: a place owns its scroller, as Skills & Certifications
 * does), with its states inside it. The program comes from the shared program list (GET /v1/programs, already read
 * for the sidebar); a month's topics and lessons are read when shown. The API decides what is open on every request.
 */

/** The page's scroller: keyboard-focusable, so it scrolls with the keys too. */
function ProgramPage({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
      tabIndex={0}
      role="region"
      aria-label={label}
      className="scrollbar-subtle focus-ring-inset h-full overflow-y-auto"
    >
      <div className="page-container">{children}</div>
    </div>
  );
}

function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <SkeletonGroup className="flex flex-col gap-3">
      {Array.from({ length: rows }, (_, row) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: placeholder rows, all alike
        <Skeleton key={row} className="h-16 w-full rounded-xl" />
      ))}
    </SkeletonGroup>
  );
}

type ProgramState = { status: "loading" } | { status: "ready"; program: Program } | { status: "missing" } | { status: "error" };

function useProgram(slug: string): ProgramState {
  const { status, data } = usePrograms();
  if (!data) return status === "error" ? { status: "error" } : { status: "loading" };
  const program = data.programs.find((item) => item.slug === slug);
  return program ? { status: "ready", program } : { status: "missing" };
}

/** A program page: its heading, a line about it, and the program once it is known (or why it is not). */
function ProgramScreen({
  slug,
  title,
  intro,
  children,
}: {
  slug: string;
  /** The page's heading; the program's own title when not given (its landing page). */
  title?: string;
  intro?: string;
  children: (program: Program) => ReactNode;
}) {
  const state = useProgram(slug);
  const heading = title ?? (state.status === "ready" ? state.program.title : "Your program");
  return (
    <ProgramPage label={heading}>
      <h2 className="text-xl font-semibold text-foreground">{heading}</h2>
      {intro && <p className="mt-1 max-w-prose text-sm text-text-secondary">{intro}</p>}
      <div className="mt-6">
        {state.status === "loading" && <Loading />}
        {state.status === "missing" && (
          <EmptyState
            icon={Lock}
            title="This program isn't on your account"
            description="Add the invoice number of your NetworkersHome program in Settings to open it here."
            action={
              <Button asChild size="sm" className="min-h-11">
                <Link href="/settings/profile">Add an invoice</Link>
              </Button>
            }
          />
        )}
        {state.status === "error" && (
          <ErrorState
            title="Your programs couldn't be loaded"
            description="Check your connection, then try again."
            action={
              <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={refreshPrograms}>
                Try again
              </Button>
            }
          />
        )}
        {state.status === "ready" && children(state.program)}
      </div>
    </ProgramPage>
  );
}

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

function States<T>({ state, retry, children }: { state: Load<T>; retry: () => void; children: (data: T) => ReactNode }) {
  if (state.status === "loading") return <Loading />;
  if (state.status === "missing") return <p className="text-sm text-text-secondary">This month isn't available to your account.</p>;
  if (state.status === "error")
    return (
      <ErrorState
        title="This month couldn't be loaded"
        description={describeError(state.error).message}
        action={
          <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={retry}>
            Try again
          </Button>
        }
      />
    );
  return <>{children(state.data)}</>;
}

function materialLine(material: ProgramMaterial): string {
  const parts = [
    material.documents ? (material.documents === 1 ? "1 book or guide" : `${material.documents} books and guides`) : "",
    material.lessons ? `${material.lessons} lessons` : "",
    material.questions ? `${material.questions} practice questions` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "Material coming soon";
}

const AREA_INFO: Record<ProgramArea, { text: string; icon: typeof GraduationCap }> = {
  modules: { text: "Your months, each with its own AI tutor", icon: GraduationCap },
  qna: { text: "Ask about anything in your program", icon: MessageCircle },
  quizzes: { text: "Practice questions by month", icon: Gamepad2 },
  interviews: { text: "An interviewer for each month's topics", icon: Mic },
  videos: { text: "NH lesson videos for each month", icon: PlayCircle },
};

const CARD = "focus-ring flex h-full min-h-11 items-start gap-3 rounded-xl border border-border bg-card p-4 transition-colors hover:bg-surface-2";

export function ProgramLanding({ slug }: { slug: string }) {
  return (
    <ProgramScreen slug={slug} intro="Your NetworkersHome program. Pick where to start.">
      {() => (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {PROGRAM_AREAS.map((area) => {
            const { text, icon: Icon } = AREA_INFO[area.path];
            return (
              <li key={area.path}>
                <Link href={`/programs/${slug}/${area.path}`} className={CARD}>
                  <Icon aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-brand" />
                  <span>
                    <span className="block text-sm font-semibold text-foreground">{area.label}</span>
                    <span className="block text-sm text-text-secondary">{text}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </ProgramScreen>
  );
}

/** The months as cards: the month's number (the course's order), its title and what it holds. */
function MonthGrid({ program, href, note }: { program: Program; href: (month: number) => string; note?: (material: ProgramMaterial) => string }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {program.months.map((month) => (
        <li key={month.month}>
          <Link href={href(month.month)} className={CARD}>
            <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-sm font-semibold text-foreground">
              {month.month}
            </span>
            <span className="min-w-0">
              <span className="sr-only">Month {month.month}: </span>
              <span className="block text-base font-semibold leading-snug text-foreground">{month.title}</span>
              <span className="mt-1 block text-xs text-text-secondary">{(note ?? materialLine)(month.material)}</span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function ProgramModules({ slug }: { slug: string }) {
  return (
    <ProgramScreen slug={slug} title="Course Module" intro="Open a month to learn with its own AI tutor.">
      {(program) => <MonthGrid program={program} href={(month) => programChatPath(slug, month, "tutor")} />}
    </ProgramScreen>
  );
}

export function ProgramInterviews({ slug }: { slug: string }) {
  return (
    <ProgramScreen slug={slug} title="Mock Interviews" intro="Pick a month: its interviewer asks one question at a time and gives feedback.">
      {(program) => (
        <MonthGrid
          program={program}
          href={(month) => programChatPath(slug, month, "interview")}
          note={(material) => (material.documents || material.lessons ? "Start a mock interview" : "Material coming soon")}
        />
      )}
    </ProgramScreen>
  );
}

/**
 * The month on a page that shows one month at a time: the address's `?month=` is the only copy of it, so a link, a
 * reload and Back all show the month the address names. A pick rewrites it without a server request (the history API,
 * which Next follows: useSearchParams updates). Reading a server prop instead showed the arrival month after Back.
 */
function usePickedMonth(program: Program): [number, (month: number) => void] {
  const params = useSearchParams();
  const month = monthFromParam(params.get("month") ?? undefined, program.months.map((item) => item.month));
  const pick = (next: number) => window.history.replaceState(null, "", `?month=${next}`);
  return [month, pick];
}

function MonthPicker({ program, month, onPick }: { program: Program; month: number; onPick: (month: number) => void }) {
  const title = program.months.find((item) => item.month === month)?.title;
  return (
    <div className="mb-6">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Month">
        {program.months.map((item) => (
          <Button
            key={item.month}
            type="button"
            size="sm"
            variant={item.month === month ? "default" : "outline"}
            aria-pressed={item.month === month}
            title={item.title}
            className="min-h-11"
            onClick={() => onPick(item.month)}
          >
            Month {item.month}
          </Button>
        ))}
      </div>
      {title && <h3 className="mt-4 text-base font-semibold text-foreground">{title}</h3>}
    </div>
  );
}

function useMonth(slug: string, month: number) {
  return useLoad<ProgramMonthDetail>(() => getProgramMonth(slug, month), `${slug}/${month}`);
}

export function ProgramQuizzes({ slug }: { slug: string }) {
  return (
    <ProgramScreen slug={slug} title="Quizzes & Games" intro="Quiz yourself on a month's topics in that month's chat.">
      {(program) => <QuizzesSwitch program={program} practice={<QuizzesFor slug={slug} program={program} />} />}
    </ProgramScreen>
  );
}

function QuizzesFor({ slug, program }: { slug: string; program: Program }) {
  const [month, pick] = usePickedMonth(program);
  return (
    <>
      <MonthPicker program={program} month={month} onPick={pick} />
      <MonthTopics slug={slug} month={month} />
    </>
  );
}

/** The month's topics, grouped by their question track (its name once, not on every row). */
function byTrack(topics: BankTopic[]): [string, BankTopic[]][] {
  const groups = new Map<string, BankTopic[]>();
  for (const topic of topics) groups.set(topic.book, [...(groups.get(topic.book) ?? []), topic]);
  return [...groups];
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
          <div className="flex flex-col gap-6">
            {byTrack(detail.topics).map(([track, topics]) => (
              <section key={track} aria-label={track}>
                <h4 className="mb-2 text-sm font-semibold text-foreground">
                  {track} <span className="font-normal text-text-secondary">({topics.length})</span>
                </h4>
                <ul className="flex flex-col divide-y divide-border rounded-xl border border-border bg-card">
                  {topics.map((topic) => (
                    <li key={topic.topicId} className="flex flex-wrap items-center gap-3 px-4 py-2">
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm text-foreground">{topic.title}</span>
                        <span className="block text-xs text-text-secondary">{topic.chapter}</span>
                      </span>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="min-h-11"
                        aria-label={`Quiz me on ${topic.title}`}
                        onClick={() => quiz(topic.title)}
                      >
                        Quiz me
                      </Button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )
      }
    </States>
  );
}

export function ProgramVideos({ slug }: { slug: string }) {
  return (
    <ProgramScreen slug={slug} title="Videos & Links" intro="NetworkersHome lesson videos, notes and cheat sheets for each month.">
      {(program) => <VideosFor slug={slug} program={program} />}
    </ProgramScreen>
  );
}

function VideosFor({ slug, program }: { slug: string; program: Program }) {
  const [month, pick] = usePickedMonth(program);
  return (
    <>
      <MonthPicker program={program} month={month} onPick={pick} />
      <MonthLessons slug={slug} month={month} />
    </>
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

const LESSONS_PAGE = 20;

function LessonList({ ids }: { ids: Uuid[] }) {
  const [shown, setShown] = useState(LESSONS_PAGE);
  const [lessons, setLessons] = useState<NhprepLessonSummary[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [open, setOpen] = useState<Uuid | null>(null);
  useEffect(() => {
    let live = true;
    const wanted = ids.slice(0, shown);
    const batches = [];
    for (let start = 0; start < wanted.length; start += 10) batches.push(getNhprepLessons(wanted.slice(start, start + 10)));
    Promise.all(batches).then(
      (lists) => {
        if (!live) return;
        setLessons(lists.flatMap((list) => list.items));
        setFailed(false);
      },
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [ids, shown, attempt]);
  if (failed)
    return (
      <ErrorState
        title="These lessons couldn't be loaded"
        description="Check your connection, then try again."
        action={
          <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={() => setAttempt((n) => n + 1)}>
            Try again
          </Button>
        }
      />
    );
  if (lessons === null) return <Loading rows={5} />;
  const left = ids.length - shown;
  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col divide-y divide-border rounded-xl border border-border bg-card">
        {lessons.map((lesson) => (
          <li key={lesson.id}>
            <button
              type="button"
              aria-expanded={open === lesson.id}
              onClick={() => setOpen(open === lesson.id ? null : lesson.id)}
              className="focus-ring-inset flex min-h-11 w-full items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-surface-2"
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
      {left > 0 && (
        <Button type="button" variant="outline" className="min-h-11 self-start" onClick={() => setShown(shown + LESSONS_PAGE)}>
          Show more lessons ({left} left)
        </Button>
      )}
    </div>
  );
}
