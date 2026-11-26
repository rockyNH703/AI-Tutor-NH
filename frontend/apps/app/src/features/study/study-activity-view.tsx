"use client";

import { Button } from "@v1/ui/button";
import { EmptyState, ErrorState } from "@v1/ui/empty-state";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { Timer } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";

import { TabPanel, ViewTabs, useTabIds } from "@/components/view-tabs";
import { ActivityChart } from "@/features/progress/activity-chart";
import { trackName, useCatalog } from "@/features/settings/learning-data";
import { ChoiceGroup } from "@/features/settings/primitives";
import { GoalBar } from "@/features/settings/progress-section";
import { describeError } from "@/lib/api/errors";
import { getProgress } from "@/lib/api/progress";
import { getStudySummary } from "@/lib/api/study";
import type { Progress, StudySummary } from "@/lib/api/types";
import { ACTIVITY_LABELS, formatStudyTime } from "./study-client";
import { StudySessionPanel } from "./study-session-panel";
import { useStudy } from "./study-store";

export const RANGES = [
  { value: "1", label: "Today" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
] as const;
type Range = (typeof RANGES)[number]["value"];

const link = "focus-ring rounded font-semibold text-brand underline underline-offset-2";

/** A titled card of the page (an `h2`; the app header owns the `h1`). Also used by My Learning. */
export function Panel({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="rounded-2xl border border-border bg-card p-4 elev-1 sm:p-5">
      <h2 id={id} className="mb-3 text-sm font-bold text-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** A number and what it is, inside a `<dl>`. Also used by My Learning. */
export function Stat({ value, label, hint }: { value: string; label: string; hint?: string }) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="order-2 text-xs text-text-secondary">{label}</dt>
      <dd className="order-1 font-display text-2xl font-bold tabular-nums text-foreground">{value}</dd>
      {hint && <dd className="order-3 mt-1 text-2xs leading-relaxed text-text-secondary">{hint}</dd>}
    </div>
  );
}

/** Rows of name, bar and time; the bar only shows what the text says. */
function Breakdown({ rows, empty }: { rows: { key: string; label: string; seconds: number }[]; empty: string }) {
  if (!rows.length) return <p className="text-sm text-text-secondary">{empty}</p>;
  const top = Math.max(...rows.map((row) => row.seconds), 1);
  return (
    <ul className="flex flex-col gap-2.5">
      {rows.map((row) => (
        <li key={row.key} className="flex flex-col gap-1">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate text-foreground">{row.label}</span>
            <span className="shrink-0 tabular-nums text-text-secondary">{formatStudyTime(row.seconds)}</span>
          </div>
          <div aria-hidden className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-brand" style={{ width: `${Math.max((row.seconds / top) * 100, 2)}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

const STATUS_TEXT = {
  active: "In progress",
  paused: "Paused",
  ended: "Ended",
} as const;
const END_TEXT = { ended: "you ended it", stale: "no lab activity", limit: "8-hour limit" } as const;

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

/** The day's goal is the existing active-time goal (Settings > Practice & Lab Time), shown as it is measured. */
function DailyGoal() {
  const [progress, setProgress] = useState<Progress | null | "failed">(null);
  useEffect(() => {
    let cancelled = false;
    getProgress(7).then(
      (loaded) => !cancelled && setProgress(loaded),
      () => !cancelled && setProgress("failed"),
    );
    return () => {
      cancelled = true;
    };
  }, []);
  if (progress === null) return <Skeleton className="h-10 w-full rounded-xl" />;
  if (progress === "failed") return <p className="text-sm text-text-secondary">Your daily goal can't be loaded right now.</p>;
  const goal = progress.goals.dailyActiveMinutes;
  if (!goal) {
    return (
      <p className="text-sm text-text-secondary">
        No daily goal yet.{" "}
        <Link href="/settings/subjects" className={link}>
          Set one
        </Link>
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm text-text-secondary">Active time in NetSec AI today: {progress.today.activeMinutes} min.</p>
      <GoalBar done={progress.today.activeMinutes} goal={goal} label="Today's goal" />
    </div>
  );
}

/** The summary's states: loading, error (with retry), nothing recorded yet, and the numbers. */
export function StudySummaryContent({
  summary: shown,
  error,
  days,
  subjectName,
  onRetry,
}: {
  summary: StudySummary | null;
  error: unknown;
  days: number;
  subjectName: (code: string | null) => string;
  onRetry: () => void;
}) {
  if (error) {
    return (
      <ErrorState
        title="Couldn't load your lab activity"
        description={describeError(error).message}
        action={
          <Button variant="outline" onClick={onRetry}>
            Try again
          </Button>
        }
      />
    );
  }
  if (!shown) {
    return (
      // The shape of what it stands for (the totals, then the views), so the page does not jump when it arrives.
      <SkeletonGroup label="Loading your lab activity" className="flex flex-col gap-5">
        <div className="rounded-2xl border border-border bg-card p-4 elev-1 sm:p-5">
          <Skeleton className="mb-3 h-5 w-24 rounded-md" />
          <div className={TOTALS_GRID}>
            {["lab", "app", "active", "idle", "paused"].map((key) => (
              <div key={key} className="flex flex-col gap-1">
                <Skeleton className="h-8 w-20 rounded-md" />
                <Skeleton className="h-4 w-24 rounded-md" />
                <Skeleton className="h-3 w-full rounded-md" />
              </div>
            ))}
          </div>
        </div>
        <Skeleton className="h-11 w-full rounded-lg" />
        <Skeleton className="h-56 w-full rounded-2xl" />
      </SkeletonGroup>
    );
  }
  if (!shown.firstRecordedDate) {
    return (
      <EmptyState
        icon={Timer}
        title="Nothing recorded yet"
        description="Start a lab session from the chat or your files: your estimated lab time will show here."
        action={null}
      />
    );
  }
  return (
    <>
      <Panel id="study-totals" title={days === 1 ? "Today" : `Last ${days} days`}>
        <dl className={TOTALS_GRID}>
          <Stat value={formatStudyTime(shown.totals.activeSeconds)} label="estimated lab time" hint="Active practice in sessions." />
          <Stat value={formatStudyTime(shown.totals.appSeconds)} label="time in NetSec AI" hint="Open, practising or not." />
          <Stat value={formatStudyTime(shown.totals.activeSeconds)} label="active" hint="In a session, on screen, active." />
          <Stat value={formatStudyTime(shown.totals.idleSeconds)} label="idle" hint="In a session, but no activity was seen." />
          <Stat value={formatStudyTime(shown.totals.pausedSeconds)} label="paused" hint="Time a session was paused." />
        </dl>
        {shown.firstRecordedDate > shown.from && (
          <p className="mt-4 text-2xs text-text-secondary">
            Recorded since {new Date(`${shown.firstRecordedDate}T00:00:00`).toLocaleDateString()}: earlier days have no data.
          </p>
        )}
      </Panel>

      <Breakdowns summary={shown} subjectName={subjectName} />
    </>
  );
}

/** A grid of five totals: two to a row on phones, all five on wide screens. */
const TOTALS_GRID = "grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3 xl:grid-cols-5";

/**
 * The range's detail as views (trend, track, activity, domain, sessions): one at a time instead of a long column.
 * Every panel stays in the page, hidden but for the open one.
 */
function Breakdowns({ summary: shown, subjectName }: { summary: StudySummary; subjectName: (code: string | null) => string }) {
  const ids = useTabIds();
  const trend = shown.days.length > 1;
  const [open, setOpen] = useState<string>(trend ? "trend" : "tracks");
  const tabs = [
    ...(trend ? [{ value: "trend", label: "Trend" }] : []),
    { value: "tracks", label: "Exam tracks" },
    { value: "activities", label: "Activities" },
    { value: "domains", label: "Domains" },
    { value: "sessions", label: "Sessions", count: shown.sessions.length || null },
  ];
  const shownTab = tabs.some((tab) => tab.value === open) ? open : (tabs[0]?.value ?? "tracks");
  const panel = "rounded-2xl border border-border bg-card p-4 elev-1 sm:p-5";
  return (
    <div className="flex flex-col gap-4">
      <ViewTabs label="Lab activity detail" ids={ids} value={shownTab} onChange={setOpen} tabs={tabs} />
      {trend && (
        <TabPanel ids={ids} value="trend" hidden={shownTab !== "trend"} className={panel}>
          <ActivityChart
            days={shown.days.map((day) => ({ date: day.date, activeMinutes: Math.floor(day.activeSeconds / 60) }))}
            goal={null}
            title={`Estimated lab minutes, last ${shown.days.length} days`}
            valueLabel="of estimated lab time"
            columnLabel="Lab time"
          />
        </TabPanel>
      )}
      <TabPanel ids={ids} value="tracks" hidden={shownTab !== "tracks"} className={panel}>
          <Breakdown
            rows={shown.subjects.map((row) => ({
              key: row.subject ?? "none",
              label: subjectName(row.subject),
              seconds: row.activeSeconds,
            }))}
            empty="No lab time in this range."
          />
      </TabPanel>
      <TabPanel ids={ids} value="activities" hidden={shownTab !== "activities"} className={panel}>
          <Breakdown
            rows={shown.activities.map((row) => ({
              key: row.activity,
              label: ACTIVITY_LABELS[row.activity],
              seconds: row.activeSeconds,
            }))}
            empty="No lab time in this range."
          />
      </TabPanel>
      <TabPanel ids={ids} value="domains" hidden={shownTab !== "domains"} className={panel}>
        <Breakdown
          rows={shown.chapters.map((row) => ({
            key: row.chapterId,
            label: `${row.chapterTitle}${row.subject ? ` (${subjectName(row.subject)})` : ""}`,
            seconds: row.activeSeconds,
          }))}
          empty="No domain was chosen in this range."
        />
      </TabPanel>
      <TabPanel ids={ids} value="sessions" hidden={shownTab !== "sessions"} className={panel}>
        {shown.sessions.length ? (
          <ul className="divide-y divide-border">
            {shown.sessions.map((session) => (
              <li key={session.id} className="flex flex-col gap-0.5 py-2.5 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
                  <span className="font-semibold text-foreground">{when(session.startedAt)}</span>
                  <span className="tabular-nums text-text-secondary">
                    {formatStudyTime(session.activeSeconds)} lab time · {formatStudyTime(session.durationSeconds)} long
                  </span>
                </div>
                <p className="text-xs text-text-secondary">
                  {STATUS_TEXT[session.status]}
                  {session.status === "ended" && session.endReason ? ` (${END_TEXT[session.endReason]})` : ""}
                  {" · "}
                  {[...session.subjects.map(subjectName), ...session.chapters].join(", ")}
                  {" · "}
                  {session.activities.map((activity) => ACTIVITY_LABELS[activity]).join(", ")}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-text-secondary">No lab sessions in this range.</p>
        )}
      </TabPanel>
    </div>
  );
}

/**
 * Study Activity: the student's own estimated study time, from the server only (GET
 * /v1/study/summary): today or recent days, active / idle / paused, by subject, chapter and
 * activity, recent sessions and the daily trend. Days with nothing recorded before tracking began
 * are left out, not shown as zero.
 */
export function StudyActivityView() {
  const [range, setRange] = useState<Range>("7");
  const [summary, setSummary] = useState<StudySummary | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const study = useStudy();
  const { catalog } = useCatalog();
  // Reload when the session starts, pauses, resumes or ends (here or on another tab or device).
  const sessionKey = `${study.server?.session?.id ?? "none"}:${study.server?.session?.status ?? ""}`;

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getStudySummary(Number(range), controller.signal).then(
      (loaded) => setSummary(loaded),
      (failure) => !controller.signal.aborted && setError(failure),
    );
    return () => controller.abort();
  }, [range, sessionKey, attempt]);

  const subjectName = (code: string | null) =>
    code ? trackName(catalog, code) : "No exam track chosen";

  return (
    <div
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
      tabIndex={0}
      role="region"
      aria-label="Lab Activity"
      className="scrollbar-subtle focus-ring-inset h-full overflow-y-auto"
    >
      {/* Controls on the left (the session, the goal), the record beside them: in reading order at every width. */}
      <div className="page-container page-wide grid grid-cols-1 items-start gap-5 lg:grid-cols-[19rem_minmax(0,1fr)]">
        <div className="flex flex-col gap-5 lg:sticky lg:top-0">
          <Panel id="study-session" title="Current session">
            <StudySessionPanel />
          </Panel>
          <Panel id="study-goal" title="Daily goal">
            <DailyGoal />
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-5">
          <ChoiceGroup
            legend="Show"
            value={range}
            options={RANGES}
            onChange={(value) => {
              setSummary(null);
              setRange(value);
            }}
          />

          <StudySummaryContent
            summary={summary}
            error={error}
            days={Number(range)}
            subjectName={subjectName}
            onRetry={() => setAttempt((count) => count + 1)}
          />

          <p className="text-xs leading-relaxed text-text-secondary">
            An estimate of practice, not proof of attention, and only you can see it.{" "}
            <Link href="/settings/study-tracking" className={link}>
              How it's measured
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
