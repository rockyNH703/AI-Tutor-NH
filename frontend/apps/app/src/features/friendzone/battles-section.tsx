"use client";

import { Button } from "@v1/ui/button";
import { Skeleton } from "@v1/ui/skeleton";
import { cn } from "@v1/ui/utils";
import { type ReactNode, type Ref, useCallback, useEffect, useId, useState } from "react";

import type { Uuid } from "@/lib/api/types";
import { type BattleMove, type BattleSummary, listBattles, moveBattle } from "./battle-api";
import { battleFailure, nameOf, summaryLine } from "./battle-model";
import { sequence } from "./latest";
import { LIST } from "./player";

/**
 * Battles: invitations either way, ones waiting to start or in progress, and a short history. Accept and Ready open
 * the battle at once (accepting means ready to play). Read again on the hub's pulse and after every change.
 */
export function BattlesSection({
  pulse,
  onOpen,
  headingRef,
}: {
  pulse: number;
  onOpen: (battleId: Uuid) => void;
  headingRef?: Ref<HTMLHeadingElement>;
}) {
  const headingId = useId();
  const [open, setOpen] = useState<BattleSummary[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [history, setHistory] = useState<{ items: BattleSummary[]; next: string | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<ReactNode>(null);

  // The newest read wins: a pulse's answer arriving after a change's reload never puts the older list back.
  const [openTicket] = useState(sequence);
  const [historyTicket] = useState(sequence);
  const readOpen = useCallback(() => {
    const newest = openTicket();
    listBattles("open").then(
      (page) => {
        if (!newest()) return;
        setOpen(page.items);
        setFailed(false);
      },
      () => newest() && setFailed(true),
    );
  }, [openTicket]);
  const readHistory = useCallback(
    (cursor: string | null) => {
      const newest = historyTicket();
      listBattles("history", cursor).then(
        (page) =>
          newest() && setHistory((current) => ({ items: [...(cursor ? (current?.items ?? []) : []), ...page.items], next: page.nextCursor })),
        () => newest() && setNote("Past battles couldn't be loaded. Try again in a moment."),
      );
    },
    [historyTicket],
  );
  useEffect(() => readOpen(), [readOpen, pulse]);
  useEffect(() => readHistory(null), [readHistory]);

  const move = async (summary: BattleSummary, kind: BattleMove) => {
    setBusy(summary.battleId);
    setNote(null);
    try {
      await moveBattle(summary.battleId, kind);
      if (kind === "accept" || kind === "ready") return onOpen(summary.battleId);
      setNote(kind === "decline" ? "Challenge declined." : "Challenge cancelled.");
      readOpen();
      readHistory(null);
    } catch (error) {
      const failure = battleFailure(error, "move");
      setNote(failure.message);
      if (failure.reload) readOpen();
    } finally {
      setBusy(null);
    }
  };

  const action = (label: string, onClick: () => void, primary = false) => (
    <Button type="button" size="sm" variant={primary ? "default" : "outline"} className="min-h-11" disabled={busy !== null} onClick={onClick}>
      {label}
    </Button>
  );
  const actions = (summary: BattleSummary) => {
    const challenger = summary.role === "challenger";
    if (summary.status === "invited")
      return challenger ? (
        action("Cancel", () => move(summary, "cancel"))
      ) : (
        <>
          {action("Accept and play", () => move(summary, "accept"), true)}
          {action("Decline", () => move(summary, "decline"))}
        </>
      );
    if (summary.status === "waiting" && challenger)
      return (
        <>
          {action("Ready", () => move(summary, "ready"), true)}
          {action("Cancel", () => move(summary, "cancel"))}
        </>
      );
    return action("Open", () => onOpen(summary.battleId), summary.status === "active");
  };

  return (
    <section aria-labelledby={headingId}>
      <h3 id={headingId} ref={headingRef} tabIndex={headingRef ? -1 : undefined} className="focus-ring rounded-md text-base font-semibold text-foreground">
        Battles
      </h3>
      <p className="mt-1 text-sm text-text-secondary">Challenge a friend from your Friends list to a live quiz on this program.</p>
      <p role="status" className="text-sm text-foreground [&:not(:empty)]:mt-2">
        {note}
      </p>
      {open === null && !failed && <Skeleton className="mt-3 h-16 w-full rounded-xl" />}
      {failed && (
        <p className="mt-2 text-sm text-text-secondary">
          Your battles couldn't be loaded.{" "}
          <Button type="button" variant="link" className="h-auto min-h-11 p-0" onClick={readOpen}>
            Try again
          </Button>
        </p>
      )}
      {open !== null && open.length === 0 && <p className="mt-1 text-sm text-text-secondary">No battles right now.</p>}
      {open !== null && open.length > 0 && (
        <ul className={cn(LIST, "mt-3")}>
          {open.map((summary) => (
            <li key={summary.battleId}>
              <SummaryRow summary={summary}>{actions(summary)}</SummaryRow>
            </li>
          ))}
        </ul>
      )}
      {history !== null && history.items.length > 0 && (
        <>
          <h4 className="mb-2 mt-4 text-sm font-semibold text-foreground">Past battles</h4>
          <ul className={LIST}>
            {history.items.map((summary) => (
              <li key={summary.battleId}>
                <SummaryRow summary={summary}>{action("View", () => onOpen(summary.battleId))}</SummaryRow>
              </li>
            ))}
          </ul>
          {history.next && (
            <Button type="button" variant="outline" className="mt-3 min-h-11" onClick={() => readHistory(history.next)}>
              Load more
            </Button>
          )}
        </>
      )}
    </section>
  );
}

export function SummaryRow({ summary, children }: { summary: BattleSummary; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-3">
      <span aria-hidden className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-surface-2 text-sm font-semibold text-foreground">
        {summary.opponent.initials ?? "?"}
      </span>
      <span className="min-w-[10rem] flex-1">
        <span className="block break-words text-sm font-semibold text-foreground">
          <bdi dir="auto">{nameOf(summary.opponent)}</bdi>
        </span>
        <span className="block text-xs text-text-secondary">{summaryLine(summary)}</span>
      </span>
      {children && <span className="flex flex-wrap gap-2">{children}</span>}
    </div>
  );
}
