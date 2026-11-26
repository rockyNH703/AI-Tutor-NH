"use client";

import Link from "next/link";

import { formatStudyTime } from "@/features/study/study-client";
import { useStudy } from "@/features/study/study-store";
import { SectionHeading, SettingsCard } from "./primitives";

const link = "focus-ring rounded font-semibold text-brand underline underline-offset-2";

/**
 * Settings > Study Tracking: what counts as study time, and the rules it follows. Nothing here is
 * a control: the idle limits are the server's and the same for everyone (they are shown as they
 * are, from the server), and tracking has no other option yet. A switch that changed nothing
 * would only mislead.
 */
export function StudyTrackingSection() {
  const idle = useStudy().server?.idle;
  const reading = idle ? formatStudyTime(idle.readingSeconds) : "a few minutes";
  const solving = idle ? formatStudyTime(idle.solvingSeconds) : "a couple of minutes";

  return (
    <>
      <SectionHeading
        title="Lab Tracking"
        description="How lab time is estimated in the sessions you start. An estimate, not proof of attention; only you see it."
      />
      <SettingsCard
        title="The rules"
        description={
          <>
            The same for everyone. Your numbers are in{" "}
            <Link href="/study" className={link}>
              Lab Activity
            </Link>
            .
          </>
        }
      >
        <dl className="flex flex-col divide-y divide-border">
          {[
            [
              "Counts",
              "A running session, the chat or your File Library on screen, and you active (or an answer being written).",
            ],
            [
              "Idle",
              `After ${reading} without input while reading or reviewing, ${solving} while practising or testing. Shown, not counted.`,
            ],
            ["Not counted", "Paused time, hidden tabs, Settings, Help and Lab Activity, and time offline (never added later)."],
            ["Several tabs", "Tabs and devices at once count once."],
            ["Ends", "When you end it, 15 minutes after your last activity (an hour if paused), or at 8 hours."],
          ].map(([term, rule]) => (
            <div key={term} className="grid gap-1 py-3 first:pt-0 last:pb-0 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-4">
              <dt className="text-sm font-bold text-foreground">{term}</dt>
              <dd className="text-sm leading-relaxed text-text-secondary">{rule}</dd>
            </div>
          ))}
        </dl>
      </SettingsCard>
    </>
  );
}
