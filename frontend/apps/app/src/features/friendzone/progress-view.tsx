"use client";

import { Button } from "@v1/ui/button";
import { Skeleton } from "@v1/ui/skeleton";
import { Award } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";

import type { Uuid } from "@/lib/api/types";
import { type PlayerStats, type XpPage, getPlayer, getXp } from "./api";
import { sequence } from "./latest";
import { levelProgress, record, statsAccuracy, streakText, xpReason } from "./progress-model";

/**
 * Your progress (Phase 2): level and XP with its history, your record and your badges. A friend's record shows the
 * same way, but only when they chose to show their stats (the API sends `stats: null` otherwise).
 */

const PANEL = "rounded-2xl border border-border bg-card p-4 sm:p-6";

/** The level's XP span as a bar and in words, so it never relies on the bar (or its colour) alone. */
export function LevelBar({ page }: { page: Pick<XpPage, "xp" | "level" | "levelFloor" | "nextLevelAt"> }) {
  const progress = levelProgress(page);
  return (
    <div>
      <p className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm text-foreground">
        <span className="text-base font-semibold">Level {progress.level}</span>
        <span className="tabular-nums text-text-secondary">{progress.text}</span>
      </p>
      <div
        role="progressbar"
        aria-label={`Level ${progress.level} progress`}
        aria-valuemin={0}
        aria-valuemax={progress.span}
        aria-valuenow={progress.into}
        aria-valuetext={progress.text}
        className="mt-2 h-2 overflow-hidden rounded-full border border-border bg-surface-2"
      >
        <div className="h-full bg-brand" style={{ width: `${Math.round(progress.fraction * 100)}%` }} />
      </div>
    </div>
  );
}

/** A member's record: battles, accuracy, dailies, and earned badges with what earned them. */
export function StatsBlock({ stats, own }: { stats: PlayerStats; own: boolean }) {
  const rows: [string, string][] = [
    ["Battles", record(stats)],
    ["Accuracy", statsAccuracy(stats)],
    ["Daily challenges", `${stats.dailiesCompleted} completed`],
    ["Daily streak", streakText(stats.dailyStreak)],
  ];
  return (
    <div className="flex flex-col gap-4">
      {!own && <LevelBar page={stats} />}
      <dl className="divide-y divide-border border-y border-border">
        {rows.map(([label, value]) => (
          <div key={label} className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 py-2.5">
            <dt className="text-sm text-text-secondary">{label}</dt>
            <dd className="text-sm font-semibold text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
      <div>
        <h4 className="text-sm font-semibold text-foreground">Badges</h4>
        {stats.badges.length === 0 ? (
          <p className="mt-1 text-sm text-text-secondary">{own ? "No badges yet. They come with battles and daily challenges." : "No badges yet."}</p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {stats.badges.map((badge) => (
              <li key={badge.key} className="flex items-start gap-3 rounded-xl border border-border px-3 py-2">
                <Award aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-foreground">{badge.title}</span>
                  <span className="block text-xs text-text-secondary">{badge.description}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Your own progress: level and XP, your record and badges, and the XP history (Load more). */
export function ProgressPanel({ playerId, pulse }: { playerId: Uuid; pulse: number }) {
  const headingId = useId();
  const [stats, setStats] = useState<PlayerStats | null>(null);
  const [xp, setXp] = useState<XpPage | null>(null);
  const [failed, setFailed] = useState(false);
  const [more, setMore] = useState<"idle" | "loading" | "failed">("idle");

  const [ticket] = useState(sequence);
  const read = useCallback(() => {
    const newest = ticket();
    Promise.all([getPlayer(playerId), getXp()]).then(
      ([player, page]) => {
        if (!newest()) return;
        setStats(player.stats ?? null);
        // After "Load more", a pulse refreshes the totals but keeps the longer history on screen.
        setXp((shown) => (shown && shown.items.length > page.items.length ? { ...page, items: shown.items, nextCursor: shown.nextCursor } : page));
        setFailed(false);
      },
      () => newest() && setFailed(true),
    );
  }, [playerId, ticket]);
  useEffect(() => {
    if (pulse >= 0) read(); // and again on every pulse: XP arrives after battles and dailies
  }, [read, pulse]);

  const loadMore = () => {
    if (!xp?.nextCursor) return;
    setMore("loading");
    getXp(xp.nextCursor).then(
      (page) => {
        setXp((current) => (current ? { ...page, items: [...current.items, ...page.items] } : page)); // onto what is shown now
        setMore("idle");
      },
      () => setMore("failed"),
    );
  };

  return (
    <section aria-labelledby={headingId} className={PANEL}>
      <h3 id={headingId} className="text-lg font-semibold text-foreground">
        Your progress
      </h3>
      {failed && !xp && (
        <p className="mt-2 text-sm text-text-secondary">
          Your progress couldn't be loaded.{" "}
          <Button type="button" variant="link" className="h-auto min-h-11 p-0" onClick={read}>
            Try again
          </Button>
        </p>
      )}
      {!failed && !xp && <Skeleton aria-busy="true" className="mt-3 h-24 w-full rounded-xl" />}
      {xp && (
        <div className="mt-4 flex flex-col gap-5">
          <LevelBar page={xp} />
          {stats && <StatsBlock stats={stats} own />}
          <div>
            <h4 className="text-sm font-semibold text-foreground">XP history</h4>
            {xp.items.length === 0 ? (
              <p className="mt-1 text-sm text-text-secondary">No XP yet. Finish a battle or a daily challenge to earn some.</p>
            ) : (
              <ul className="mt-2 divide-y divide-border rounded-xl border border-border">
                {xp.items.map((item, index) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: events have no id; the list only grows at its end
                  <li key={index} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                    <span className="min-w-0 text-foreground">
                      {xpReason(item.reason)}
                      <span className="block text-xs text-text-secondary">{formatDay(item.createdAt)}</span>
                    </span>
                    <span className="shrink-0 font-semibold tabular-nums text-foreground">+{item.amount} XP</span>
                  </li>
                ))}
              </ul>
            )}
            {xp.nextCursor && (
              <Button type="button" variant="outline" className="mt-3 min-h-11" disabled={more === "loading"} onClick={loadMore}>
                {more === "loading" ? "Loading…" : "Load more"}
              </Button>
            )}
            {more === "failed" && <p className="mt-2 text-sm text-destructive">More XP history couldn't be loaded. Try again.</p>}
          </div>
        </div>
      )}
    </section>
  );
}

const DAY = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });

function formatDay(iso: string): string {
  const time = Date.parse(iso);
  return Number.isNaN(time) ? "" : DAY.format(time);
}
