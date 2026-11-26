"use client";

import { Badge, type BadgeProps } from "@v1/ui/badge";
import { Button } from "@v1/ui/button";
import { EmptyState, ErrorState } from "@v1/ui/empty-state";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { CheckCircle2, ChevronDown, ClipboardList } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useEffect, useId, useState } from "react";

import { TabPanel, ViewTabs, useTabIds, useViewParam } from "@/components/view-tabs";
import { LazyInlineMarkdown } from "@/features/markdown/lazy-message-content";
import { trackName, useCatalog } from "@/features/settings/learning-data";
import { Panel } from "@/features/study/study-activity-view";
import { describeError } from "@/lib/api/errors";
import { getLearnerProfile } from "@/lib/api/learning";
import { getMastery, getMistakes, getPracticePlan, getPracticeTests, getRevisions } from "@/lib/api/progress";
import type {
  LearnerProfile,
  Mastery,
  MasteryState,
  MasterySubject,
  MistakeConcept,
  Mistakes,
  PlanTask,
  PracticePlan,
  PracticeTests,
  RevisionItem,
  Revisions,
} from "@/lib/api/types";
import { shortDate } from "@/lib/time";
import { useAskInChat } from "./ask-in-chat";
import { PracticePicker } from "./practice-picker";

const link = "focus-ring rounded font-semibold text-brand underline underline-offset-2";
const summary =
  "focus-ring flex cursor-pointer list-none items-center justify-between gap-3 rounded-xl px-3 py-3 text-sm font-semibold text-foreground [&::-webkit-details-marker]:hidden";
const MISTAKES_SHOWN = 50;
const TESTS_SHOWN = 20;
const ASK_HINT = "Buttons here open a new chat with the request filled in. Nothing is sent until you press Send.";
/** A track's level (the bank's tier). */
const TIERS: Record<number, string> = { 1: "Associate", 2: "Professional", 3: "Expert" };
const VIEWS = ["plan", "mastery", "missed", "tests"] as const;
type View = (typeof VIEWS)[number];

type Variant = NonNullable<BadgeProps["variant"]>;

const STATES: Record<MasteryState, { label: string; badge: Variant }> = {
  mastered: { label: "Mastered", badge: "success" },
  learning: { label: "Learning", badge: "info" },
  weak: { label: "Needs work", badge: "warning" },
  not_enough_evidence: { label: "Not enough answers yet", badge: "neutral" },
};
const TASKS: Record<PlanTask["kind"], { label: string; badge: Variant; action: string }> = {
  revise: { label: "Review", badge: "info", action: "Review" },
  strengthen: { label: "Needs work", badge: "warning", action: "Practise" },
  learn: { label: "New", badge: "brand", action: "Practise" },
};
const REVISION_STATES: Record<RevisionItem["state"], { label: string; badge: Variant }> = {
  overdue: { label: "Overdue", badge: "danger" },
  due: { label: "Due today", badge: "info" },
  done: { label: "Reviewed today", badge: "success" },
  upcoming: { label: "Coming up", badge: "neutral" },
};

function days(count: number): string {
  return `${count} ${count === 1 ? "day" : "days"}`;
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/**
 * A subject's line, by its topics' rule: an estimate only once one of its topics has enough answers
 * for one (the API's subject mean also takes in concepts with too few answers).
 */
function subjectEstimate(subject: MasterySubject): string {
  const judged = subject.chapters.some((chapter) => chapter.topics.some((topic) => topic.state !== "not_enough_evidence"));
  return judged && subject.estimate !== null ? `estimate ${percent(subject.estimate)}` : "Not enough answers yet";
}

/** An India learning day ("YYYY-MM-DD"), as the day it names wherever the student is. */
function day(date: string): string {
  return new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function reason(task: PlanTask): string {
  switch (task.reason) {
    case "overdue":
      return `Review is overdue by ${days(task.overdueDays ?? 0)}.`;
    case "due":
      return "Due for review today.";
    case "weak_concept":
      return "Your recent answers show this needs more practice.";
    case "next_topic":
      return "The next topic you haven't practised yet.";
    case "start_here":
      return "A good place to start.";
  }
}

type Resource<T> = { data: T | null; error: unknown; retry: () => void };

/** One resource with its own loading, error and retry: a slow or failed panel never holds up the rest. */
function useResource<T>(load: (signal: AbortSignal) => Promise<T>): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    load(controller.signal).then(
      (loaded) => !controller.signal.aborted && setData(loaded),
      (failure) => !controller.signal.aborted && setError(failure),
    );
    return () => controller.abort();
  }, [attempt]);
  return { data, error, retry: () => setAttempt((count) => count + 1) };
}

/** Loading (skeletons), a failure with Try again (never zeros), or the panel's content. */
function Loaded<T>({
  resource,
  what,
  children,
}: {
  resource: Resource<T>;
  what: string;
  children: (data: T) => ReactNode;
}) {
  if (resource.error) {
    return (
      <ErrorState
        title={`Couldn't load ${what}`}
        description={describeError(resource.error).message}
        action={
          <Button variant="outline" onClick={resource.retry}>
            Try again
          </Button>
        }
      />
    );
  }
  if (!resource.data) {
    return (
      <SkeletonGroup label={`Loading ${what}`} className="space-y-3">
        <Skeleton className="h-16 w-full rounded-xl" />
        <Skeleton className="h-16 w-full rounded-xl" />
      </SkeletonGroup>
    );
  }
  return <>{children(resource.data)}</>;
}

/** A row's action: fills a new chat with its request (`name` is its accessible name). Full width on phones. */
function AskButton({ label, name, request, hint }: { label: string; name: string; request: string; hint: string }) {
  const ask = useAskInChat();
  return (
    <Button
      variant="outline"
      onClick={() => ask(request)}
      aria-label={name}
      aria-describedby={hint}
      className="w-full shrink-0 sm:w-auto"
    >
      {label}
    </Button>
  );
}

function Plan({ plan, subjectName, hint }: { plan: PracticePlan; subjectName: (code: string) => string; hint: string }) {
  return (
    <div className="flex flex-col gap-4">
      {plan.tasks.length === 0 ? (
        <EmptyState
          icon={ClipboardList}
          title={plan.summary.answered ? "Nothing planned for today" : "No plan yet"}
          description={
            plan.summary.answered
              ? "Nothing is due and no new topic is waiting. Practise any domain below."
              : "Your plan builds from the practice tests you take. Choose a domain to take your first one."
          }
          action={null}
        />
      ) : (
        <>
          {(plan.insufficientHistory || plan.needsProfile) && (
            <p className="text-xs text-text-secondary">
              {plan.insufficientHistory ? "A starting point: it adapts as you answer more questions. " : ""}
              {plan.needsProfile ? "It draws on every certification track for now." : ""}
            </p>
          )}
          <ol className="flex flex-col divide-y divide-border">
            {plan.tasks.map((task) => (
              <li
                key={`${task.kind}:${task.topicId}:${task.conceptTag ?? ""}`}
                className="flex flex-col gap-3 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex min-w-0 flex-col gap-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={TASKS[task.kind].badge}>{TASKS[task.kind].label}</Badge>
                    <span className="min-w-0 break-words text-sm font-semibold text-foreground">{task.topicTitle}</span>
                  </div>
                  <p className="break-words text-xs text-text-secondary">
                    {subjectName(task.subjectCode)} · Domain {task.chapterNumber}: {task.chapterTitle}
                  </p>
                  <p className="text-xs text-text-secondary">
                    {reason(task)} {task.minutes} min · {task.questions} questions
                  </p>
                  {task.done && (
                    <p className="flex items-center gap-1.5 text-xs font-semibold text-success">
                      <CheckCircle2 aria-hidden className="h-4 w-4" />
                      Done today
                    </p>
                  )}
                </div>
                <AskButton
                  label={TASKS[task.kind].action}
                  name={`${TASKS[task.kind].action} ${task.topicTitle} in a new chat`}
                  request={task.prompt}
                  hint={hint}
                />
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

function RevisionRow({ item, subjectName, hint }: { item: RevisionItem; subjectName: (code: string) => string; hint?: string }) {
  const state = REVISION_STATES[item.state];
  return (
    <li className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={state.badge}>{state.label}</Badge>
          <span className="min-w-0 break-words text-sm font-semibold text-foreground">{item.topicTitle}</span>
        </div>
        <p className="break-words text-xs text-text-secondary">
          {subjectName(item.subjectCode)} · Domain {item.chapterNumber}: {item.chapterTitle}
        </p>
        <p className="text-xs text-text-secondary">
          {item.state === "overdue" ? `Due ${day(item.dueDate)}, ${days(item.overdueDays)} ago.` : `Due ${day(item.dueDate)}.`}{" "}
          Last time {percent(item.lastAccuracy)} right ({day(item.lastReviewed)}).
        </p>
      </div>
      {hint && <AskButton label="Review" name={`Review ${item.topicTitle} in a new chat`} request={item.prompt} hint={hint} />}
    </li>
  );
}

function Revision({ revisions, subjectName, hint }: { revisions: Revisions; subjectName: (code: string) => string; hint: string }) {
  const now = revisions.items.filter((item) => item.state !== "upcoming");
  const later = revisions.items.filter((item) => item.state === "upcoming");
  if (!revisions.items.length) {
    return (
      <p className="text-sm text-text-secondary">
        Nothing to review yet. A topic you practise comes back after {revisions.ladderDays.join(", ")} days.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {now.length ? (
        <ul className="flex flex-col divide-y divide-border">
          {now.map((item) => (
            <RevisionRow
              key={item.topicId}
              item={item}
              subjectName={subjectName}
              hint={item.state === "overdue" || item.state === "due" ? hint : undefined}
            />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-text-secondary">Nothing is due today.</p>
      )}
      {later.length > 0 && (
        <details className="group rounded-xl border border-border">
          <summary className={summary}>
            Coming up ({later.length})
            <ChevronDown aria-hidden className="h-4 w-4 shrink-0 text-text-secondary transition-transform group-open:rotate-180" />
          </summary>
          <ul className="flex flex-col divide-y divide-border px-3 pb-3">
            {later.map((item) => (
              <RevisionRow key={item.topicId} item={item} subjectName={subjectName} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function MasteryList({ mastery, subjectName }: { mastery: Mastery; subjectName: (code: string) => string }) {
  if (!mastery.subjects.length) {
    return (
      <p className="text-sm text-text-secondary">
        No estimates yet. They appear once you've answered questions in a certification practice test.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col divide-y divide-border rounded-xl border border-border">
        {mastery.subjects.map((subject) => (
          <details key={`${subject.subjectCode}:${subject.classLevel}:${subject.language}`} className="group">
            <summary className={summary}>
              <span className="min-w-0 break-words">
                {subjectName(subject.subjectCode)} · {TIERS[subject.classLevel] ?? `Level ${subject.classLevel}`}
              </span>
              <span className="flex shrink-0 items-center gap-2 text-xs font-medium text-text-secondary">
                {subjectEstimate(subject)}
                <ChevronDown aria-hidden className="h-4 w-4 transition-transform group-open:rotate-180" />
              </span>
            </summary>
            <div className="flex flex-col gap-4 px-3 pb-4">
              {subject.chapters.map((chapter) => (
                <section key={chapter.chapterId} aria-labelledby={`mastery-${chapter.chapterId}`} className="flex flex-col gap-2">
                  <h3 id={`mastery-${chapter.chapterId}`} className="break-words text-sm font-semibold text-foreground">
                    Domain {chapter.number}: {chapter.title}
                    <span className="block text-xs font-normal text-text-secondary">{chapter.bookTitle}</span>
                  </h3>
                  <ul className="flex flex-col gap-2.5">
                    {chapter.topics.map((topic) => {
                      const answered = topic.concepts.reduce((sum, concept) => sum + concept.answered, 0);
                      const correct = topic.concepts.reduce((sum, concept) => sum + concept.correct, 0);
                      const shown = topic.state === "not_enough_evidence" ? null : topic.estimate;
                      return (
                        <li key={topic.topicId} className="flex flex-col gap-1">
                          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm">
                            <span className="min-w-0 break-words text-foreground">
                              {topic.number}. {topic.title}
                            </span>
                            <Badge variant={STATES[topic.state].badge}>{STATES[topic.state].label}</Badge>
                          </div>
                          <p className="text-xs text-text-secondary">
                            {correct} of {answered} correct
                            {shown === null ? "" : ` · estimate ${percent(shown)}`}
                          </p>
                          {shown !== null && (
                            <div aria-hidden className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                              <div className="h-full rounded-full bg-brand" style={{ width: `${Math.max(shown * 100, 2)}%` }} />
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          </details>
        ))}
      </div>
      <p className="text-2xs leading-relaxed text-text-secondary">
        An estimate is a guide from your practice answers, not a score. Mastered needs {mastery.model.minAnswers} answers to{" "}
        {mastery.model.minQuestions} different questions.
      </p>
    </div>
  );
}

function MistakeEntry({ concept, subjectName, hint }: { concept: MistakeConcept; subjectName: (code: string) => string; hint: string }) {
  // Every chosen or right option ("A. …; C. …"): a certification question has up to eight, and some take several.
  const option = (letters: string[], options: string[]) =>
    letters.map((letter) => `${letter}. ${options["ABCDEFGH".indexOf(letter)] ?? ""}`).join("; ");
  const what = concept.objective || concept.topicTitle;
  return (
    <details className="group">
      <summary className={summary}>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="break-words">{what}</span>
          <span className="break-words text-xs font-normal text-text-secondary">
            {subjectName(concept.subjectCode)} · {concept.topicTitle} · wrong {concept.wrong}{" "}
            {concept.wrong === 1 ? "time" : "times"}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {concept.recurring && <Badge variant="warning">Recurring</Badge>}
          <ChevronDown aria-hidden className="h-4 w-4 text-text-secondary transition-transform group-open:rotate-180" />
        </span>
      </summary>
      <div className="flex flex-col gap-4 px-3 pb-4">
        <ul className="flex flex-col gap-4">
          {concept.items.map((item) => (
            <li key={`${item.questionId}:${item.answeredAt}`} className="flex min-w-0 flex-col gap-1.5 text-sm">
              {/* Wide maths scrolls here instead of widening the page. */}
              <div
                // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
                tabIndex={0}
                role="region"
                aria-label="Question"
                className="scrollbar-subtle focus-ring-inset flex min-w-0 flex-col gap-1.5 overflow-x-auto break-words"
              >
                <p className="text-foreground">
                  <LazyInlineMarkdown text={item.stem} />
                </p>
                <p className="text-text-secondary">
                  Your answer: <LazyInlineMarkdown text={option(item.chosenKeys, item.options)} />
                </p>
                <p className="text-foreground">
                  Correct answer: <LazyInlineMarkdown text={option(item.correctKeys, item.options)} />
                </p>
                {item.explanation && (
                  <p className="text-text-secondary">
                    <LazyInlineMarkdown text={item.explanation} />
                  </p>
                )}
              </div>
              <p className="text-xs text-text-secondary">
                {shortDate(item.answeredAt)}
                {item.conversationId && (
                  <>
                    {" · "}
                    <Link href={`/c/${item.conversationId}`} className={link}>
                      Open the practice test
                    </Link>
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
        <AskButton label="Practise this" name={`Practise this in a new chat: ${what}`} request={concept.prompt} hint={hint} />
      </div>
    </details>
  );
}

function MistakeList({ mistakes, subjectName, hint }: { mistakes: Mistakes; subjectName: (code: string) => string; hint: string }) {
  if (!mistakes.concepts.length) {
    return (
      <p className="text-sm text-text-secondary">
        No missed questions. A question you get wrong in a practice test shows up here, with the right answer.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col divide-y divide-border rounded-xl border border-border">
        {mistakes.concepts.map((concept) => (
          <MistakeEntry key={`${concept.topicId}:${concept.conceptTag}`} concept={concept} subjectName={subjectName} hint={hint} />
        ))}
      </div>
      <p className="text-2xs leading-relaxed text-text-secondary">
        Recurring: wrong {mistakes.recurringRule.wrong} or more times in {mistakes.recurringRule.days} days.
      </p>
    </div>
  );
}

function TestList({ tests }: { tests: PracticeTests }) {
  if (!tests.items.length) {
    return (
      <p className="text-sm text-text-secondary">
        No practice tests yet. Choose a domain under Today, or ask for one in a chat.
      </p>
    );
  }
  return (
    <ul className="flex flex-col divide-y divide-border">
      {tests.items.map((test) => (
        <li key={test.setId} className="flex flex-col gap-1 py-2.5 first:pt-0 last:pb-0">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm">
            <span className="min-w-0 break-words font-semibold text-foreground">{test.title}</span>
            <span className="shrink-0 tabular-nums text-text-secondary">
              {test.answered ? `${test.correct} of ${test.answered} correct` : "No answers yet"}
            </span>
          </div>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-secondary">
            {shortDate(test.startedAt)} · {test.answered} of {test.total} answered
            {test.open && <Badge variant="info">In progress</Badge>}
            {test.conversationId ? (
              <Link href={`/c/${test.conversationId}`} className={link}>
                Open the chat
              </Link>
            ) : (
              <span>(chat deleted)</span>
            )}
          </p>
        </li>
      ))}
    </ul>
  );
}

/** One figure of the summary row: a value (or its placeholder) and what it is. */
function Figure({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 bg-card px-4 py-3 sm:px-5">
      <dt className="text-xs text-text-secondary">{label}</dt>
      <dd className="font-display text-xl font-bold tabular-nums text-foreground">{value}</dd>
      <dd className="min-h-4 truncate text-2xs text-text-secondary">{detail}</dd>
    </div>
  );
}

/** A value still loading: the height of the figure it stands for. */
function Pending() {
  return (
    <span className="block py-1">
      <span aria-hidden className="block h-5 w-16 animate-soft-pulse rounded-md bg-surface-2" />
    </span>
  );
}

/** Today at a glance: answers toward a practice day, the plan's minutes, what is due, the exam date. */
function Summary({ plan, revisions, profile }: { plan: PracticePlan | null; revisions: Revisions | null; profile: Resource<LearnerProfile> }) {
  const due = revisions?.items.filter((item) => item.state === "overdue" || item.state === "due") ?? [];
  const overdue = due.filter((item) => item.state === "overdue").length;
  const exam = profile.data?.examDate ?? null;
  const left = plan && exam && plan.examDate === exam ? plan.daysToExam : null;
  const passed = plan !== null && exam !== null && exam < plan.date;
  return (
    <dl
      aria-label="Today at a glance"
      className="grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-border bg-border elev-1 lg:grid-cols-4"
    >
      <Figure
        label="Practice answers today"
        value={plan ? `${plan.answeredToday} of ${plan.answersForLearningDay}` : <Pending />}
        detail={plan ? (plan.answeredToday >= plan.answersForLearningDay ? "A practice day" : "for a practice day") : null}
      />
      <Figure
        label="Planned today"
        value={plan ? (plan.tasks.length ? `${plan.budgetMinutes} min` : "Nothing") : <Pending />}
        detail={plan?.tasks.length ? `${plan.tasks.length} ${plan.tasks.length === 1 ? "task" : "tasks"}` : null}
      />
      <Figure
        label="Due for review"
        value={revisions ? due.length : <Pending />}
        detail={overdue ? `${overdue} overdue` : revisions ? "topics" : null}
      />
      <Figure
        label="Exam date"
        value={profile.error ? "—" : profile.data ? (exam ? day(exam) : "Not set") : <Pending />}
        detail={
          <Link href="/settings/goals" className={link}>
            {exam ? (passed ? "Passed · set the next" : left !== null && left > 0 ? `In ${days(left)} · change` : "Change") : "Set a date"}
          </Link>
        }
      />
    </dl>
  );
}

/**
 * My Learning: the student's own practice record, from question-bank tests in the chat only (GET
 * /v1/progress/plan, mastery, revisions, mistakes, practice-tests and practice-topics): today's plan,
 * a chapter picker, revision, the exam date, mastery estimates, mistakes and recent tests. Each panel
 * loads on its own, says when it can't, and never shows a number the server didn't give. Its
 * buttons fill a new chat with a request; nothing is sent until the student presses Send.
 */
export function MyLearningView() {
  const { catalog } = useCatalog();
  const plan = useResource(getPracticePlan);
  const mastery = useResource(getMastery);
  const revisions = useResource(getRevisions);
  const mistakes = useResource((signal) => getMistakes(MISTAKES_SHOWN, signal));
  const tests = useResource((signal) => getPracticeTests(TESTS_SHOWN, signal));
  const profile = useResource(() => getLearnerProfile());

  const subjectName = (code: string) => trackName(catalog, code);
  const [view, setView] = useViewParam(VIEWS);
  const ids = useTabIds();
  const hint = useId();

  return (
    <div
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
      tabIndex={0}
      role="region"
      aria-label="Skills & Certifications"
      className="scrollbar-subtle focus-ring-inset h-full overflow-y-auto"
    >
      <div className="page-container page-wide flex flex-col gap-5">
        <Summary plan={plan.data} revisions={revisions.data} profile={profile} />

        <div className="flex flex-col gap-5">
          <ViewTabs
            label="Skills & Certifications"
            ids={ids}
            value={view}
            onChange={setView}
            tabs={[
              { value: "plan", label: "Today" },
              { value: "mastery", label: "Mastery" },
              { value: "missed", label: "Missed questions", count: mistakes.data?.concepts.length || null },
              { value: "tests", label: "Practice tests", count: tests.data?.items.length || null },
            ]}
          />
          <p id={hint} className="-mt-2 text-2xs text-text-secondary">
            {ASK_HINT}
          </p>
          <TabPanel ids={ids} value={view}>
            {view === "plan" && (
              <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
                <Panel id="learning-plan" title="Today's plan">
                  <Loaded resource={plan} what="your plan">
                    {(data) => <Plan plan={data} subjectName={subjectName} hint={hint} />}
                  </Loaded>
                </Panel>
                <div className="flex flex-col gap-5">
                  <Panel id="learning-revision" title="Topics to review">
                    <Loaded resource={revisions} what="your topics to review">
                      {(data) => <Revision revisions={data} subjectName={subjectName} hint={hint} />}
                    </Loaded>
                  </Panel>
                  <Panel id="learning-practice" title="Practice by domain">
                    <PracticePicker subjectName={subjectName} hint={hint} />
                  </Panel>
                </div>
              </div>
            )}
            {view === "mastery" && (
              <Panel id="learning-mastery" title="Topic mastery">
                <Loaded resource={mastery} what="your topic mastery">
                  {(data) => <MasteryList mastery={data} subjectName={subjectName} />}
                </Loaded>
              </Panel>
            )}
            {view === "missed" && (
              <Panel id="learning-mistakes" title="Missed questions">
                <Loaded resource={mistakes} what="your missed questions">
                  {(data) => <MistakeList mistakes={data} subjectName={subjectName} hint={hint} />}
                </Loaded>
              </Panel>
            )}
            {view === "tests" && (
              <Panel id="learning-tests" title="Recent practice tests">
                <Loaded resource={tests} what="your practice tests">
                  {(data) => <TestList tests={data} />}
                </Loaded>
              </Panel>
            )}
          </TabPanel>
        </div>

        <p className="text-xs leading-relaxed text-text-secondary">
          Only your answers to practice tests in the chat count here, and only you can see it. Lab time is in{" "}
          <Link href="/study" className={link}>
            Lab Activity
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
