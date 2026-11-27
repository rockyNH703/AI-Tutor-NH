"use client";

import { Button } from "@v1/ui/button";
import { EmptyState, ErrorState } from "@v1/ui/empty-state";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  Gamepad2,
  GraduationCap,
  ListChecks,
  Lock,
  type LucideIcon,
  MessageCircle,
  Mic,
  PlayCircle,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";

import { QuizzesSwitch } from "@/features/friendzone/quizzes-switch";
import { NhprepSlot } from "@/features/nhprep/nhprep-slot";
import { ApiError, describeError } from "@/lib/api/errors";
import { getNhprepLessons } from "@/lib/api/nhprep";
import {
  type BankTopic,
  type Program,
  type ProgramClaim,
  type ProgramList,
  type ProgramMaterial,
  type ProgramMonth,
  type ProgramMonthDetail,
  getProgramMonth,
} from "@/lib/api/programs";
import type { NhprepLessonSummary, Uuid } from "@/lib/api/types";
import { ARROW, CARD, CARD_GRID, FOOT, NUMBER, twoDigits } from "./card-styles";
import { QuizTopicsView } from "./quiz-topics-view";
import { InvoiceForm } from "./invoice-flow";
import { PROGRAM_AREAS, type ProgramArea, endedClaims, monthFromParam, programChatPath } from "./program-nav";
import { ProgramNav } from "./program-nav-bar";
import { type ProgramsState, refreshPrograms, usePrograms } from "./use-programs";

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
      <ProgramNav slug={slug} className="mb-6" />
      <h2 className="font-display text-2xl font-bold tracking-tight text-foreground">{heading}</h2>
      {intro && <p className="mt-1 max-w-prose text-sm text-text-secondary">{intro}</p>}
      <div className="mt-6">
        {state.status === "loading" && <Loading />}
        {state.status === "missing" && (
          <EmptyState
            icon={Lock}
            title="This program isn't on your account"
            description="Add the invoice number of your NetworkersHome program in My Programs to open it here."
            action={
              <Button asChild size="sm" className="min-h-11">
                <Link href="/programs">Add an invoice</Link>
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

/** What a month holds, each count with its own word ("1 lesson", "154 lessons"); a count of none is left out. */
function materialParts(material: ProgramMaterial): { icon: LucideIcon; count: number; label: string }[] {
  return [
    { icon: BookOpen, count: material.documents, label: material.documents === 1 ? "book or guide" : "books and guides" },
    { icon: PlayCircle, count: material.lessons, label: material.lessons === 1 ? "lesson" : "lessons" },
    { icon: ListChecks, count: material.questions, label: material.questions === 1 ? "practice question" : "practice questions" },
  ].filter((part) => part.count > 0);
}

const AREA_INFO: Record<ProgramArea, { text: string; icon: typeof GraduationCap }> = {
  modules: { text: "Your months, each with its own AI tutor", icon: GraduationCap },
  qna: { text: "Ask about anything in your program", icon: MessageCircle },
  quizzes: { text: "Practice questions by month", icon: Gamepad2 },
  interviews: { text: "An interviewer for each month's topics", icon: Mic },
  videos: { text: "NH lesson videos for each month", icon: PlayCircle },
};

/** One list of places, each a full-width row: scanned top to bottom, like the sidebar it mirrors. */
const LIST = "flex flex-col divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card elev-1";
const ROW = "focus-ring-inset flex min-h-14 items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2";

export function ProgramLanding({ slug }: { slug: string }) {
  return (
    <ProgramScreen slug={slug} title="Overview" intro="Your NetworkersHome program. Pick where to start.">
      {() => (
        <ul className={`${LIST} max-w-2xl`}>
          {PROGRAM_AREAS.map((area) => {
            const { text, icon: Icon } = AREA_INFO[area.path];
            return (
              <li key={area.path}>
                <Link href={`/programs/${slug}/${area.path}`} className={ROW}>
                  <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-soft text-brand">
                    <Icon className="h-5 w-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-foreground">{area.label}</span>
                    <span className="block text-sm text-text-secondary">{text}</span>
                  </span>
                  <ChevronRight aria-hidden className="h-4 w-4 shrink-0 text-text-secondary" />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </ProgramScreen>
  );
}

/**
 * My Programs (/programs, the sidebar's place for every account): a card for each program the account's invoices
 * open, a card for each claim whose access ended, and the invoice form, the one place an invoice is entered. With no
 * program the form is the page's empty state. Statuses come from GET /v1/programs (`claims`), so they outlast a reload
 * and a new sign-in; a refused attempt is shown for this visit only.
 */
export function MyPrograms() {
  return <MyProgramsView state={usePrograms()} />;
}

const ENDED_REASON: Record<Exclude<ProgramClaim["state"], "active">, string> = {
  expired: "Its invoice has expired, so this program no longer opens here.",
  revoked: "Its invoice is no longer active, so this program no longer opens here.",
};

/** The page from the program list's state (unit-tested with plain values). */
export function MyProgramsView({ state }: { state: ProgramsState }) {
  const [focusSlug, setFocusSlug] = useState<string | null>(null);
  const cards = useRef(new Map<string, HTMLAnchorElement>());
  const { data } = state;
  useEffect(() => {
    // After a claim the list is read again: focus moves to the program's card once it is there.
    const node = focusSlug && data ? cards.current.get(focusSlug) : undefined;
    if (!node) return;
    node.focus();
    setFocusSlug(null);
  }, [focusSlug, data]);

  return (
    <ProgramPage label="My Programs">
      <h2 className="font-display text-2xl font-bold tracking-tight text-foreground">Your programs</h2>
      <p className="mt-1 max-w-prose text-sm text-text-secondary">
        Your NetworkersHome programs, each opened by its invoice number.
      </p>
      <div className="mt-6">
        {!data && state.status !== "error" && (
          <SkeletonGroup className={CARD_GRID}>
            <Skeleton className="h-44 w-full rounded-2xl" />
            <Skeleton className="h-44 w-full rounded-2xl" />
          </SkeletonGroup>
        )}
        {!data && state.status === "error" && (
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
        {data && !data.enabled && (
          <EmptyState
            icon={GraduationCap}
            title="Programs aren't available right now"
            description="NetworkersHome programs open here when they're available to your account."
            action={null}
          />
        )}
        {data?.enabled && <ProgramsAndInvoice data={data} cards={cards.current} onClaimed={setFocusSlug} />}
      </div>
    </ProgramPage>
  );
}

function ProgramsAndInvoice({
  data,
  cards,
  onClaimed,
}: {
  data: ProgramList;
  cards: Map<string, HTMLAnchorElement>;
  onClaimed: (slug: string) => void;
}) {
  const ended = endedClaims(
    data.claims,
    data.programs.map((program) => program.slug),
  );
  const empty = data.programs.length === 0;
  const formId = useId();
  return (
    <div className="flex flex-col gap-8">
      {(data.programs.length > 0 || ended.length > 0) && (
        <ul aria-label="Your programs" className={CARD_GRID}>
          {data.programs.map((program) => (
            <li key={program.slug} className="flex">
              <Link
                href={`/programs/${program.slug}`}
                ref={(node) => {
                  if (node) cards.set(program.slug, node);
                  else cards.delete(program.slug);
                }}
                className={`${CARD} flex min-h-44 w-full flex-col p-5`}
              >
                <span className="flex items-center justify-between gap-3">
                  <span aria-hidden className={NUMBER}>
                    <GraduationCap className="h-4 w-4" />
                  </span>
                  <ChevronRight aria-hidden className={ARROW} />
                </span>
                <h3 className="mt-4 text-pretty font-display text-base font-bold leading-snug tracking-tight text-foreground sm:text-[1.0625rem]">
                  {program.title}
                </h3>
                <span className="mt-auto block pt-5">
                  <span className={`${FOOT} justify-between`}>
                    <span className="tabular-nums">
                      {program.months.length} {program.months.length === 1 ? "month" : "months"}
                    </span>
                    <span className="font-semibold text-foreground">Open program</span>
                  </span>
                </span>
              </Link>
            </li>
          ))}
          {ended.map((claim) => (
            <li key={claim.program.slug} className="flex">
              <div className="flex min-h-44 w-full flex-col rounded-2xl border border-border bg-surface-2/40 p-5">
                <span aria-hidden className={NUMBER}>
                  <Lock className="h-4 w-4" />
                </span>
                <h3 className="mt-4 text-pretty font-display text-base font-bold leading-snug tracking-tight text-foreground sm:text-[1.0625rem]">
                  {claim.program.title}
                </h3>
                <p className="mt-1 text-sm text-text-secondary">
                  {ENDED_REASON[claim.state as keyof typeof ENDED_REASON]} Contact NetworkersHome if this isn't right.
                </p>
                <span className="mt-auto block pt-5">
                  <span className={FOOT}>
                    <span className="font-semibold text-foreground">Access ended</span>
                  </span>
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
      {/* One form in one place in the tree, so its status stays put when the first program's card appears above it. */}
      <section
        aria-labelledby={`${formId}-heading`}
        className={empty ? "max-w-2xl rounded-2xl border border-border bg-card p-6 elev-1 sm:p-8" : "max-w-2xl"}
      >
        {empty && (
          <span aria-hidden className="mb-4 grid h-11 w-11 place-items-center rounded-xl bg-brand-soft text-brand">
            <GraduationCap className="h-5 w-5" />
          </span>
        )}
        <h3 id={`${formId}-heading`} className="font-display text-lg font-bold tracking-tight text-foreground">
          {empty ? "Add an invoice to open your program" : "Add another invoice"}
        </h3>
        <p className="mt-1 max-w-prose text-sm text-text-secondary">
          {empty
            ? "Enter the invoice number of your NetworkersHome program. It is checked against NetworkersHome's records at once, and your program opens here: a tutor for every month, quizzes, mock interviews and videos."
            : "Another NetworkersHome program? Its invoice number opens it here, beside the ones above."}
        </p>
        <div className="mt-5">
          <InvoiceForm onClaimed={(result) => onClaimed(result.program.slug)} />
        </div>
      </section>
    </div>
  );
}

/**
 * The program pages' cards (owner, 2026-11-26: Apple-style course cards). One grid for all of them: it fills the width
 * it has, two cards side by side from about 41rem and one below that, never three, so it follows the pane beside a
 * resizable sidebar rather than the window. One surface: white, a hairline edge, a soft tint and a darker edge on hover,
 * a slight give on press.
 */

/** A card per month, in the course's order, its whole surface the way into that month's chat. */
function MonthCards({ program, href, foot }: { program: Program; href: (month: number) => string; foot: (month: ProgramMonth) => ReactNode }) {
  return (
    <ol className={CARD_GRID}>
      {program.months.map((month) => (
        <li key={month.month} className="flex">
          <Link href={href(month.month)} className={`${CARD} flex min-h-44 w-full flex-col p-5`}>
            <span className="flex items-center justify-between gap-3">
              <span aria-hidden className={NUMBER}>
                {twoDigits(month.month)}
              </span>
              <ChevronRight aria-hidden className={ARROW} />
            </span>
            <h3 className="mt-4 text-pretty font-display text-base font-bold leading-snug tracking-tight text-foreground sm:text-[1.0625rem]">
              <span className="sr-only">Month {month.month}: </span>
              {month.title}
            </h3>
            <span className="mt-auto block pt-5">{foot(month)}</span>
          </Link>
        </li>
      ))}
    </ol>
  );
}

/** Course Module's foot: what the month holds, each count with its word. */
function MaterialFoot({ material }: { material: ProgramMaterial }) {
  const parts = materialParts(material);
  if (parts.length === 0) return <span className={FOOT}>Material coming soon</span>;
  return (
    <ul className={FOOT}>
      {parts.map(({ icon: Icon, count, label }) => (
        <li key={label} className="inline-flex items-center gap-1.5">
          <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />
          <span>
            <span className="font-semibold tabular-nums text-foreground">{count.toLocaleString()}</span> {label}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ProgramModules({ slug }: { slug: string }) {
  return (
    <ProgramScreen slug={slug} title="Course Module" intro="Open a month to learn with its own AI tutor.">
      {(program) => (
        <MonthCards
          program={program}
          href={(month) => programChatPath(slug, month, "tutor")}
          foot={(month) => <MaterialFoot material={month.material} />}
        />
      )}
    </ProgramScreen>
  );
}

export function ProgramInterviews({ slug }: { slug: string }) {
  return (
    <ProgramScreen slug={slug} title="Mock Interviews" intro="Pick a month: its interviewer asks one question at a time and gives feedback.">
      {(program) => (
        <MonthCards
          program={program}
          href={(month) => programChatPath(slug, month, "interview")}
          foot={({ material }) =>
            material.documents || material.lessons ? (
              <span className={FOOT}>
                <span className="inline-flex items-center gap-1.5">
                  <Mic aria-hidden className="h-3.5 w-3.5 shrink-0" />
                  Start a mock interview
                </span>
              </span>
            ) : (
              <span className={FOOT}>Material coming soon</span>
            )
          }
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
    <ProgramScreen slug={slug} title="Quizzes & Games" intro="Pick a topic, choose your quizzes, and take a timed test.">
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

function MonthTopics({ slug, month }: { slug: string; month: number }) {
  const [state, retry] = useMonth(slug, month);
  return (
    <States state={state} retry={retry}>
      {(detail) =>
        !detail.quizAvailable || !detail.topics.length ? (
          <p className="text-sm text-text-secondary">No practice questions are mapped to this month yet.</p>
        ) : (
          // Topics first, a topic's quizzes on their own view (owner, 2026-11-27): no endless list of cards.
          <QuizTopicsView slug={slug} month={month} quizzes={detail.topics} />
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
      {/* A card per lesson, in the month's order; an open one takes the whole row for its video and notes. */}
      <ol className={CARD_GRID}>
        {lessons.map((lesson, index) => (
          <li
            key={lesson.id}
            className={
              open === lesson.id ? "col-span-full flex flex-col rounded-2xl border border-border bg-card elev-1" : "flex"
            }
          >
            <button
              type="button"
              aria-expanded={open === lesson.id}
              onClick={() => setOpen(open === lesson.id ? null : lesson.id)}
              className={
                open === lesson.id
                  ? "group focus-ring flex w-full items-center gap-3 rounded-2xl p-4 text-left transition-colors duration-fast hover:bg-surface-2/40 sm:p-5"
                  : `${CARD} flex min-h-24 w-full items-center gap-3 p-4 text-left sm:p-5`
              }
            >
              <span aria-hidden className={NUMBER}>
                {twoDigits(index + 1)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="line-clamp-2 text-pretty text-sm font-semibold leading-snug text-foreground">{lesson.title}</span>
                <span className="mt-0.5 flex items-center gap-1.5 text-xs text-text-secondary">
                  <PlayCircle aria-hidden className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{lesson.course.title}</span>
                </span>
              </span>
              <ChevronDown
                aria-hidden
                className={`h-4 w-4 shrink-0 text-text-secondary transition-transform duration-fast ease-out group-hover:text-foreground ${open === lesson.id ? "rotate-180" : ""}`}
              />
            </button>
            {open === lesson.id && (
              <div className="px-4 pb-4 sm:px-5 sm:pb-5">
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
      </ol>
      {left > 0 && (
        <Button type="button" variant="outline" className="min-h-11 self-start" onClick={() => setShown(shown + LESSONS_PAGE)}>
          Show more lessons ({left} left)
        </Button>
      )}
    </div>
  );
}
