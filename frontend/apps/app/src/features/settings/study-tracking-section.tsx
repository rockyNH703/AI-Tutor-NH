"use client";

import Link from "next/link";

import { formatStudyTime } from "@/features/study/study-client";
import { useStudy } from "@/features/study/study-store";
import { Notice, SectionHeading, SettingsCard } from "./primitives";

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
        description="How NetSec AI estimates your lab time in the lab sessions you start. You'll find the numbers in Lab Activity."
      />
      <div className="flex flex-col gap-4">
        <Notice>
          Lab time is an estimate of practice activity, not proof of attention or understanding. Only you can see it, and it
          is never compared with anyone else's.
        </Notice>
        <SettingsCard title="What counts as lab time">
          <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-text-secondary">
            <li>A lab session you started is running (not paused).</li>
            <li>You're in the chat or your File Library, and that page is on screen.</li>
            <li>You're active: typing, clicking, scrolling or touching, or NetSec AI is writing you an answer.</li>
            <li>
              Several tabs or devices at once count once. Time offline, or when NetSec AI didn't hear from your device, isn't
              counted and is never added later.
            </li>
          </ul>
        </SettingsCard>
        <SettingsCard title="Idle">
          <p className="text-sm leading-relaxed text-text-secondary">
            While reading or reviewing, you become idle after {reading} without any input; shortly before, NetSec AI asks whether
            you're still practising. While practising or taking a test, after {solving}. Idle time is shown, but isn't lab time.
            These limits are the same for everyone and can't be changed.
          </p>
        </SettingsCard>
        <SettingsCard title="Hidden tabs and other pages">
          <p className="text-sm leading-relaxed text-text-secondary">
            When NetSec AI is in a hidden tab or a minimised window, or you're in Settings, Help or Lab Activity, the session keeps
            running but the time isn't counted. Time in NetSec AI (any time the app is open, practising or not) is shown separately.
          </p>
        </SettingsCard>
        <SettingsCard title="Pause, resume and end">
          <p className="text-sm leading-relaxed text-text-secondary">
            Paused time never counts. A session ends when you end it; after 15 minutes without lab activity it ends by
            itself at your last activity (after an hour, if paused), and every session stops after 8 hours.
          </p>
        </SettingsCard>
        <p className="text-sm text-text-secondary">
          See your lab time in{" "}
          <Link href="/study" className={link}>
            Lab Activity
          </Link>
          .
        </p>
      </div>
    </>
  );
}
