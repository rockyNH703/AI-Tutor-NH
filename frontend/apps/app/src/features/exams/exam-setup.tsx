"use client";

import { Button } from "@v1/ui/button";
import { EmptyState, ErrorState } from "@v1/ui/empty-state";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { cn } from "@v1/ui/utils";
import { ArrowLeft, ListChecks, Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { ApiError, describeError } from "@/lib/api/errors";
import { type ExamDifficulty, type ExamOptions, createExam, getExamOptions } from "@/lib/api/practice-exams";
import {
  DIFFICULTIES,
  DIFFICULTY_LABEL,
  SCORING,
  availableByDifficulty,
  clampCount,
  defaultCount,
  defaultSelection,
  durationOptions,
  minutesText,
  quizzesHref,
  topicHref,
  trackFor,
  trackTopics,
} from "./setup-model";

/**
 * The test setup (Quizzes & Games › month › topic › setup): which Quizzes of the Track, the difficulty, how many
 * questions and how long, each with what the bank really has (GET …/exam-options). Start creates the exam once per
 * request id (a retry gets the same exam) and replaces this page with it, so Back returns to the topic.
 */

type Load = { kind: "loading" } | { kind: "error"; error: unknown } | { kind: "ready"; options: ExamOptions };

const PILL =
  "inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-xl border border-input bg-card px-3 py-2 text-sm text-foreground transition-colors duration-fast ease-out hover:border-brand/50 has-[:checked]:border-brand has-[:checked]:bg-brand-soft has-[:disabled]:cursor-not-allowed has-[:disabled]:border-dashed has-[:disabled]:border-border has-[:disabled]:bg-surface has-[:disabled]:text-text-secondary has-[:disabled]:hover:border-border";
const INPUT = "focus-ring h-4 w-4 shrink-0 accent-brand";
const SECTION = "flex flex-col gap-3 rounded-2xl border border-border bg-card p-4 sm:p-6";
const LEGEND = "text-base font-semibold text-foreground";

export function ExamSetup({ slug, month, topic, quizId }: { slug: string; month: number; topic: string; quizId: string | null }) {
  const router = useRouter();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const refresh = useCallback(() => setAttempt((n) => n + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    // A re-read after a 409 keeps the form (and its choices) on screen; only its counts change.
    setLoad((now) => (now.kind === "ready" ? now : { kind: "loading" }));
    getExamOptions(slug, month, controller.signal).then(
      (options) => setLoad({ kind: "ready", options }),
      (error) => !controller.signal.aborted && setLoad((now) => (now.kind === "ready" ? now : { kind: "error", error })),
    );
    return () => controller.abort();
  }, [slug, month, attempt]);

  const track = load.kind === "ready" ? trackFor(load.options.quizzes, topic, quizId) : null;
  const topicTitle =
    load.kind === "ready" ? trackTopics(load.options.quizzes, track ?? "").find((item) => item.key === topic)?.title ?? null : null;

  return (
    <SetupPage>
      <Breadcrumb slug={slug} month={month} topic={topic} topicTitle={topicTitle} />
      <h1 className="font-display text-2xl font-semibold text-foreground">Set up your test</h1>
      {load.kind === "loading" && <SetupSkeleton />}
      {load.kind === "error" &&
        (load.error instanceof ApiError && load.error.code === "not_found" ? (
          <EmptyState
            icon={ListChecks}
            title={`Month ${month} isn't open to your account`}
            description="Tests are made from the quizzes of a program month your invoice opens."
            action={<BackButton href={quizzesHref(slug, month)} label="Back to Quizzes & Games" />}
          />
        ) : (
          <ErrorState
            title="The test options couldn't be loaded"
            description={describeError(load.error).message}
            action={
              <Button type="button" variant="outline" className="min-h-11" onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </Button>
            }
          />
        ))}
      {load.kind === "ready" &&
        (track ? (
          <SetupForm key={`${track}/${quizId ?? ""}`} slug={slug} month={month} topic={topic} quizId={quizId} track={track} options={load.options}
            onStale={refresh}
            onStarted={(examId) => router.replace(`/exam/${examId}?topic=${encodeURIComponent(topic)}`)}
          />
        ) : (
          <EmptyState
            icon={ListChecks}
            title={`No quizzes for this topic in Month ${month}`}
            description="Pick a topic from Quizzes & Games to set up a test."
            action={<BackButton href={quizzesHref(slug, month)} label="Back to Quizzes & Games" />}
          />
        ))}
    </SetupPage>
  );
}

/** The page's own scroller (the app frame clips; a place owns its scroll region, as the program pages do). */
function SetupPage({ children }: { children: ReactNode }) {
  return (
    <div
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
      tabIndex={0}
      role="region"
      aria-label="Test setup"
      className="scrollbar-subtle focus-ring-inset h-full overflow-y-auto"
    >
      <div className="page-container flex max-w-4xl flex-col gap-5">{children}</div>
    </div>
  );
}

function BackButton({ href, label }: { href: string; label: string }) {
  return (
    <Button asChild variant="outline" className="min-h-11">
      <Link href={href}>
        <ArrowLeft aria-hidden className="h-4 w-4" />
        {label}
      </Link>
    </Button>
  );
}

function Breadcrumb({ slug, month, topic, topicTitle }: { slug: string; month: number; topic: string; topicTitle: string | null }) {
  const link = "focus-ring inline-flex min-h-11 items-center rounded-lg px-2 font-medium text-foreground hover:bg-surface-2";
  return (
    <nav aria-label="Breadcrumb">
      <ol className="-ml-2 flex flex-wrap items-center gap-x-1 text-sm text-text-secondary">
        <li>
          <Link href={quizzesHref(slug, month)} className={link}>
            Quizzes &amp; Games
          </Link>
        </li>
        <li aria-hidden>/</li>
        <li>
          <Link href={quizzesHref(slug, month)} className={link}>
            Month {month}
          </Link>
        </li>
        {topicTitle && (
          <>
            <li aria-hidden>/</li>
            <li>
              <Link href={topicHref(slug, month, topic)} className={link}>
                <bdi dir="auto">{topicTitle}</bdi>
              </Link>
            </li>
          </>
        )}
        <li aria-hidden>/</li>
        <li aria-current="page" className="px-2 text-foreground">
          Test setup
        </li>
      </ol>
    </nav>
  );
}

function SetupSkeleton() {
  return (
    <SkeletonGroup label="Loading the test options" className="flex flex-col gap-4">
      {[0, 1, 2].map((item) => (
        <div key={item} className={SECTION}>
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-2/3" />
        </div>
      ))}
    </SkeletonGroup>
  );
}

type FormProps = {
  slug: string;
  month: number;
  topic: string;
  quizId: string | null;
  track: string;
  options: ExamOptions;
  onStale: () => void;
  /** The exam exists: open it in place of this page. */
  onStarted: (examId: string) => void;
};

export function SetupForm({ slug, month, topic, quizId, track, options, onStale, onStarted }: FormProps) {
  const ids = useId();
  const topics = useMemo(() => trackTopics(options.quizzes, track), [options.quizzes, track]);
  const [selected, setSelected] = useState(() => defaultSelection(options.quizzes, topic, quizId));
  const [difficulty, setDifficulty] = useState<ExamDifficulty>("mixed");
  const counts = availableByDifficulty(options.quizzes, selected);
  const available = counts[difficulty];
  const most = Math.min(options.maxQuestions, available);
  const [pick, setPick] = useState(() => defaultCount(options.lengths, counts.mixed, options.maxQuestions));
  const [custom, setCustom] = useState("");
  const clamped = custom ? clampCount(custom, available, options.maxQuestions) : null;
  const count = clamped ? clamped.value : Math.min(pick, most);
  const durations = durationOptions(Math.max(1, count), options.durations);
  const [multiplier, setMultiplier] = useState(1);
  const duration = durations.find((option) => option.multiplier === multiplier) ?? durations[0];
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const request = useRef<{ key: string; id: string } | null>(null);
  const chosen = topics.flatMap((item) => item.quizzes).filter((quiz) => selected.has(quiz.topicId));
  const blocked = chosen.length === 0 ? "Choose at least one quiz." : available === 0 ? "No questions are available for this choice." : null;

  const toggle = (id: string) =>
    setSelected((now) => {
      const next = new Set(now);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const start = async () => {
    if (pending || blocked || !duration || count < 1) return;
    const body = {
      program: slug,
      month,
      topicIds: chosen.map((quiz) => quiz.topicId),
      difficulty,
      length: count,
      minutes: duration.minutes,
    };
    // One id per choice of settings: a retry of the same test gets the same exam, a changed test a new one.
    const key = JSON.stringify(body);
    if (request.current?.key !== key) request.current = { key, id: crypto.randomUUID() };
    setPending(true);
    setFailure(null);
    try {
      const { examId } = await createExam({ requestId: request.current.id, ...body });
      onStarted(examId);
    } catch (error) {
      setPending(false);
      if (error instanceof ApiError && error.code === "not_enough_questions") {
        const now = (error.current as { available?: unknown } | undefined)?.available;
        setFailure(
          typeof now === "number"
            ? `Only ${now} ${now === 1 ? "question is" : "questions are"} available for this choice now. Choose fewer questions or another difficulty.`
            : "Fewer questions are available for this choice now. Choose fewer questions or another difficulty.",
        );
        onStale(); // the counts are read again
      } else setFailure(describeError(error).message);
    }
  };

  return (
    <form
      className="flex flex-col gap-4"
      aria-describedby={`${ids}-summary`}
      onSubmit={(event) => {
        event.preventDefault();
        void start();
      }}
    >
      <fieldset className={SECTION}>
        <legend className={cn(LEGEND, "float-left w-full")}>Quizzes in this test</legend>
        <p className="clear-left text-sm text-text-secondary">
          Track: <bdi dir="auto">{track}</bdi>. A test draws from one track; choose any of its quizzes.
        </p>
        {topics.map((group) => (
          <fieldset key={group.key} className="flex flex-col gap-2">
            <legend className="mb-2 text-sm font-semibold text-foreground">
              Topic: <bdi dir="auto">{group.title}</bdi>
            </legend>
            {group.quizzes.map((quiz) => (
              <label key={quiz.topicId} className={cn(PILL, "w-full justify-start")}>
                <input
                  type="checkbox"
                  className={INPUT}
                  checked={selected.has(quiz.topicId)}
                  disabled={quiz.available.total === 0}
                  onChange={() => toggle(quiz.topicId)}
                />
                <span dir="auto" className="min-w-0 flex-1 break-words">
                  {quiz.title}
                </span>
                <span className="shrink-0 text-xs tabular-nums text-text-secondary">
                  {quiz.available.total === 0 ? "No questions yet" : `${quiz.available.total} ${quiz.available.total === 1 ? "question" : "questions"}`}
                </span>
              </label>
            ))}
          </fieldset>
        ))}
      </fieldset>

      <fieldset className={SECTION}>
        <legend className={cn(LEGEND, "float-left w-full")}>Difficulty</legend>
        <div className="clear-left flex flex-wrap gap-2">
          {DIFFICULTIES.map((level) => (
            <label key={level} className={PILL}>
              <input
                type="radio"
                name={`${ids}-difficulty`}
                className={INPUT}
                checked={difficulty === level}
                disabled={counts[level] === 0}
                onChange={() => setDifficulty(level)}
              />
              {DIFFICULTY_LABEL[level]}
              <span className="text-xs tabular-nums text-text-secondary">{counts[level] === 0 ? "· none available" : `· ${counts[level]}`}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className={SECTION}>
        <legend className={cn(LEGEND, "float-left w-full")}>Number of questions</legend>
        <div className="clear-left flex flex-wrap gap-2">
          {options.lengths.map((length) => (
            <label key={length} className={PILL}>
              <input
                type="radio"
                name={`${ids}-count`}
                className={INPUT}
                checked={!custom && count === length}
                disabled={length > most}
                onChange={() => {
                  setPick(length);
                  setCustom("");
                }}
              />
              <span className="tabular-nums">{length}</span>
              {length > most && <span className="sr-only">(not enough questions)</span>}
            </label>
          ))}
        </div>
        <p className="text-xs text-text-secondary">
          {most > 0 ? `${most} ${most === 1 ? "question" : "questions"} can be drawn for this choice.` : "No questions can be drawn for this choice."}
        </p>
        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-custom`} className="text-sm font-medium text-foreground">
            Or type a number (1 to {Math.max(1, most)})
          </label>
          <input
            id={`${ids}-custom`}
            type="number"
            inputMode="numeric"
            min={1}
            max={Math.max(1, most)}
            value={custom}
            disabled={most < 1}
            aria-describedby={`${ids}-custom-note`}
            onChange={(event) => setCustom(event.target.value)}
            className="focus-field h-11 w-32 rounded-xl border border-input bg-card px-3 text-base tabular-nums text-foreground"
          />
          <p id={`${ids}-custom-note`} role="status" className="min-h-5 text-xs text-text-secondary">
            {clamped?.message ? `${clamped.message} The test will have ${clamped.value}.` : ""}
          </p>
        </div>
      </fieldset>

      <fieldset className={SECTION}>
        <legend className={cn(LEGEND, "float-left w-full")}>Time</legend>
        <div className="clear-left flex flex-wrap gap-2">
          {durations.map((option) => (
            <label key={option.multiplier} className={PILL}>
              <input
                type="radio"
                name={`${ids}-time`}
                className={INPUT}
                checked={duration?.multiplier === option.multiplier}
                onChange={() => setMultiplier(option.multiplier)}
              />
              <span className="tabular-nums">{minutesText(option.minutes)}</span>
            </label>
          ))}
        </div>
        <p className="text-xs text-text-secondary">
          {Math.round(options.durations.secondsPerQuestion)} seconds a question by default, at most {options.durations.maxMinutes} minutes.
        </p>
      </fieldset>

      <section aria-labelledby={`${ids}-summary-title`} className={SECTION}>
        <h2 id={`${ids}-summary-title`} className={LEGEND}>
          Summary
        </h2>
        <dl id={`${ids}-summary`} className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="text-text-secondary">Quizzes</dt>
          <dd className="text-foreground">{chosen.length ? chosen.map((quiz) => quiz.title).join(", ") : "None chosen"}</dd>
          <dt className="text-text-secondary">Questions</dt>
          <dd className="tabular-nums text-foreground">{count}</dd>
          <dt className="text-text-secondary">Difficulty</dt>
          <dd className="text-foreground">{DIFFICULTY_LABEL[difficulty]}</dd>
          <dt className="text-text-secondary">Time</dt>
          <dd className="text-foreground">{duration ? minutesText(duration.minutes) : "–"}</dd>
          <dt className="text-text-secondary">Scoring</dt>
          <dd className="text-foreground">{SCORING}</dd>
        </dl>
        <p role="status" className="text-sm font-medium text-foreground">
          {failure ? "" : (blocked ?? "")}
        </p>
        {failure && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {failure}
          </p>
        )}
        <div>
          <Button type="submit" className="min-h-11 w-full sm:w-auto" aria-disabled={pending || Boolean(blocked) || undefined}>
            <Play aria-hidden className="h-4 w-4" />
            {pending ? "Starting…" : "Start test"}
          </Button>
        </div>
      </section>
    </form>
  );
}
