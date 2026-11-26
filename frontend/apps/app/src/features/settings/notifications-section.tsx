"use client";

import { RotateCcw } from "lucide-react";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import Link from "next/link";

import { InlineAction } from "@/components/inline-action";
import { describeError } from "@/lib/api/errors";
import { getNotificationPreferences, updateNotificationPreferences } from "@/lib/api/preferences";
import type { NotificationPreferences } from "@/lib/api/types";
import { Notice, SectionHeading, SettingsCard, ToggleRow } from "./primitives";
import { useAutoSaver } from "./use-auto-saver";

/** A divided list of switches: one row each, first and last flush with the card. */
const LIST = "flex flex-col divide-y divide-border [&>*]:py-3 [&>*:first-child]:pt-0 [&>*:last-child]:pb-0";

/**
 * In the app (the header's bell: everything, reminders switchable) and by email (security and
 * the reminders chosen here). Says plainly when this server is not sending notification emails
 * yet, and when email reminders are waiting for a time zone, rather than letting a switch
 * suggest an email that will not come.
 */
export function NotificationsSection() {
  const { value, change, loadError, retry } = useAutoSaver<NotificationPreferences>({
    load: getNotificationPreferences,
    save: (version, set) =>
      updateNotificationPreferences({
        version,
        set: {
          ...(set.dailyReminder !== undefined && { dailyReminder: set.dailyReminder }),
          ...(set.streakWarning !== undefined && { streakWarning: set.streakWarning }),
          ...(set.weeklySummary !== undefined && { weeklySummary: set.weeklySummary }),
          ...(set.securityAlerts !== undefined && { securityAlerts: set.securityAlerts }),
          ...(set.inAppReminders !== undefined && { inAppReminders: set.inAppReminders }),
        },
      }),
  });

  return (
    <>
      <SectionHeading title="Notifications" description="What the bell shows, and what we email you." />
      {loadError ? (
        <SettingsCard title="Couldn't load your notification settings">
          <p className="text-sm text-text-secondary">{describeError(loadError).message}</p>
          <InlineAction icon={RotateCcw} onClick={retry} className="-ml-2 mt-1">
            Try again
          </InlineAction>
        </SettingsCard>
      ) : !value ? (
        <SkeletonGroup label="Loading notification settings" className="flex flex-col gap-4">
          <Skeleton className="h-32 w-full rounded-2xl" />
          <Skeleton className="h-72 w-full rounded-2xl" />
        </SkeletonGroup>
      ) : (
        <div className="flex flex-col gap-4">
          <SettingsCard title="In the app">
            <ToggleRow
              label="Reminders in the app"
              description="Practice reminders and the evening streak reminder. Security alerts and streak milestones always appear."
              checked={value.inAppReminders}
              onCheckedChange={(inAppReminders) => change({ inAppReminders })}
            />
          </SettingsCard>
          <SettingsCard
            title="By email"
            description={
              value.delivery === "paused"
                ? "NetSec AI isn't sending notification emails yet. Your choices are saved for when it does."
                : undefined
            }
          >
            {value.remindersNeedTimezone && (
              <Notice tone="warning">
                Email reminders need your time zone.{" "}
                <Link href="/settings/profile" className="focus-ring rounded font-semibold text-brand underline underline-offset-2">
                  Set your time zone
                </Link>
                .
              </Notice>
            )}
            <div className={LIST}>
              <ToggleRow
                label="Daily reminder"
                description="At your practice time (6 pm by default), on days you haven't used NetSec AI."
                checked={value.dailyReminder}
                onCheckedChange={(dailyReminder) => change({ dailyReminder })}
              />
              <ToggleRow
                label="Practice streak reminder"
                description="On evenings when a streak of two or more days would end."
                checked={value.streakWarning}
                onCheckedChange={(streakWarning) => change({ streakWarning })}
              />
              <ToggleRow
                label="Weekly summary"
                description="Monday mornings: last week's active time and questions."
                checked={value.weeklySummary}
                onCheckedChange={(weeklySummary) => change({ weeklySummary })}
              />
              <ToggleRow
                label="Sign-in alerts"
                description="A sign-in from a new kind of device. Password changes are always emailed."
                checked={value.securityAlerts}
                onCheckedChange={(securityAlerts) => change({ securityAlerts })}
              />
            </div>
          </SettingsCard>
        </div>
      )}
    </>
  );
}
