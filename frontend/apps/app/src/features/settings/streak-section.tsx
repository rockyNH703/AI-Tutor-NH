"use client";

import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { CheckCircle2, Circle, Flame } from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";

import { useAppearance } from "@/components/appearance-provider";
import { useStreak } from "@/features/streak/streak-store";
import { LEARNING_EVENT } from "@/lib/activity";
import { describeError } from "@/lib/api/errors";
import { getLearningDays } from "@/lib/api/progress";
import type { LearningDays, LearningStreak } from "@/lib/api/types";
import { SectionHeading, SettingsCard, ToggleRow } from "./primitives";

/**
 * The calendar's drawn height, held while it loads so the card doesn't collapse and regrow: up to a
 * ~1490px window its days are 10px or less (LearningActivityCalendar's layout), a 136-138px drawing. In px,
 * like the drawing, which doesn't scale with the text size. Wider windows (up to 170px) and the phone's
 * scroll hint add a little below it, where nothing follows: it is the page's last block, so that moves nothing.
 */
const CALENDAR_SPACE = "min-h-[138px]";

// d3 and the calendar load on this page only, not with the rest of the app.
const LearningActivityCalendar = dynamic(
  () => import("@/features/streak/LearningActivityCalendar").then((module) => module.LearningActivityCalendar),
  { ssr: false, loading: () => <Skeleton className="h-[138px] w-full rounded-xl" /> },
);

function longDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year as number, (month as number) - 1, day as number).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function daysLabel(n: number): string {
  return `${n} ${n === 1 ? "day" : "days"}`;
}

/**
 * The three numbers. While they load (`data` null) a dash holds each value's place in the value's own type,
 * so the card is already its final height and nothing below it moves when they arrive.
 */
function StreakStats({ data }: { data: LearningStreak | null }) {
  const dash = (
    <span aria-hidden className="text-text-secondary">
      –
    </span>
  );
  return (
    <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
      <div className="flex flex-col-reverse">
        <dt className="text-xs text-text-secondary">Current streak</dt>
        <dd className="flex items-center gap-1.5 text-2xl font-bold tabular-nums text-foreground">
          <Flame aria-hidden className={data?.todayActive ? "h-5 w-5 fill-current text-streak" : "h-5 w-5 text-text-secondary"} />
          {data ? daysLabel(data.currentStreak) : dash}
        </dd>
      </div>
      <div className="flex flex-col-reverse">
        <dt className="text-xs text-text-secondary">Longest streak</dt>
        <dd className="text-2xl font-bold tabular-nums text-foreground">{data ? daysLabel(data.longestStreak) : dash}</dd>
      </div>
      <div className="col-span-2 flex flex-col-reverse sm:col-span-1">
        <dt className="text-xs text-text-secondary">Today's practice</dt>
        <dd className="flex items-center gap-1.5 text-base font-semibold text-foreground">
          {!data ? (
            dash
          ) : data.todayActive ? (
            <>
              <CheckCircle2 aria-hidden className="h-5 w-5 text-success" /> Completed
            </>
          ) : (
            <>
              <Circle aria-hidden className="h-5 w-5 text-text-secondary" /> Not completed
            </>
          )}
        </dd>
      </div>
    </dl>
  );
}

/** Settings > Learning & Streak: the streak, today's learning, the header switch, and the year's calendar. */
export function StreakSection() {
  const appearance = useAppearance();
  const streak = useStreak();
  const [calendar, setCalendar] = useState<LearningDays | null>(null);
  const [calendarError, setCalendarError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const loadCalendar = useCallback(() => {
    setCalendarError(null);
    getLearningDays().then(setCalendar, setCalendarError);
  }, []);

  useEffect(() => {
    loadCalendar();
    window.addEventListener(LEARNING_EVENT, loadCalendar);
    return () => window.removeEventListener(LEARNING_EVENT, loadCalendar);
  }, [loadCalendar]);

  const data = streak.data;
  const failed = !data && streak.status !== "loading";
  const shown = appearance?.appearance;
  const selectedDay = selected ? calendar?.days.find((day) => day.date === selected) : null;

  return (
    <>
      <SectionHeading
        title="Practice Streak"
        description="Days you asked a real question and got a full answer. Days follow India time."
      />
      <div className="flex flex-col gap-4">
        <SettingsCard title={failed ? "Couldn't load your practice streak" : "Your practice streak"}>
          {data ? (
            <StreakStats data={data} />
          ) : failed ? (
            <p className="text-sm text-text-secondary">Your practice streak is safe; it just can't be shown right now.</p>
          ) : (
            <SkeletonGroup label="Loading your practice streak">
              <StreakStats data={null} />
            </SkeletonGroup>
          )}
          {/* Always here (disabled until the account's appearance is known), so the card never changes height. */}
          <div className="border-t border-border pt-4">
            <ToggleRow
              label="Show my practice streak in the header"
              checked={shown?.showStreak ?? true}
              disabled={!shown}
              onCheckedChange={(showStreak) => appearance?.change({ showStreak })}
            />
          </div>
        </SettingsCard>

        <SettingsCard title="Activity" description="Darker days had more practice. Choose a day for its details.">
          {calendarError ? (
            <div className="flex flex-col items-start gap-2">
              <p className="text-sm text-text-secondary">{describeError(calendarError).message}</p>
              <button
                type="button"
                onClick={loadCalendar}
                className="focus-ring rounded text-sm font-medium text-brand underline underline-offset-2"
              >
                Try again
              </button>
            </div>
          ) : (
            <div>
              <div className={`${CALENDAR_SPACE} max-w-full overflow-x-clip`}>
                {calendar ? (
                  <LearningActivityCalendar days={calendar.days} start={calendar.start} onSelect={setSelected} />
                ) : (
                  <SkeletonGroup label="Loading your calendar">
                    <Skeleton className="h-[138px] w-full rounded-xl" />
                  </SkeletonGroup>
                )}
              </div>
              <div aria-live="polite" className="text-sm">
                {selected && (
                  <p className="mt-3 rounded-xl bg-surface px-3.5 py-2.5">
                    <span className="font-semibold text-foreground">{longDate(selected)}</span>{" "}
                    <span className="text-text-secondary">
                      {selectedDay
                        ? `✓ Practice day · ${selectedDay.interactions} practice interaction${selectedDay.interactions === 1 ? "" : "s"}`
                        : "No practice that day"}
                    </span>
                  </p>
                )}
              </div>
            </div>
          )}
        </SettingsCard>
      </div>
    </>
  );
}
