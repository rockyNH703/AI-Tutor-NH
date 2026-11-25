"use client";

import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { Bookmark, BookmarkCheck, Check, Minus, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { type ExamCounts, PALETTE_ATTRIBUTE, PALETTE_LABEL, PALETTE_STATES, type PaletteState, pillLabel } from "./exam-model";

/**
 * The exam's question palette (the house mock-exam navigator's pattern): one numbered pill per question. A state is
 * told by shape and glyph first (square or round, a tick, a dash, a bookmark), colour second; Not saved (a warning
 * glyph) outranks the rest; the current question has a ring and `aria-current="step"`. Picking a pill never moves
 * focus by itself: the caller decides (the sheet closes and the question heading takes it).
 */

const SHAPE: Record<PaletteState, string> = {
  answered: "rounded-md border-brand bg-brand-soft text-foreground",
  "not-answered": "rounded-md border-destructive/60 bg-card text-foreground",
  marked: "rounded-full border-warning bg-card text-foreground",
  "answered-marked": "rounded-full border-brand bg-brand-soft text-foreground",
  "not-visited": "rounded-md border-dashed border-border bg-card text-text-secondary",
};

export function StateGlyph({ state, unsaved = false, className = "h-3.5 w-3.5 shrink-0" }: { state: PaletteState; unsaved?: boolean; className?: string }) {
  if (unsaved) return <TriangleAlert aria-hidden className={cn(className, "text-warning")} />;
  switch (state) {
    case "answered":
      return <Check aria-hidden className={cn(className, "text-brand")} />;
    case "not-answered":
      return <Minus aria-hidden className={cn(className, "text-destructive")} />;
    case "marked":
      return <Bookmark aria-hidden className={cn(className, "text-warning")} />;
    case "answered-marked":
      return <BookmarkCheck aria-hidden className={cn(className, "text-brand")} />;
    default:
      return null;
  }
}

export function ExamPalette({
  total,
  current,
  stateOf,
  unsaved,
  counts,
  locked = false,
  onPick,
  footer,
}: {
  total: number;
  current: number;
  stateOf: (round: number) => PaletteState;
  unsaved: (round: number) => boolean;
  counts: ExamCounts;
  locked?: boolean;
  onPick: (round: number) => void;
  footer?: ReactNode;
}) {
  return (
    <nav aria-label="Questions" {...{ [PALETTE_ATTRIBUTE]: "" }} className="flex flex-col gap-3">
      <ol className="grid grid-cols-5 gap-1.5">
        {Array.from({ length: total }, (_, index) => {
          const round = index + 1;
          const state = stateOf(round);
          const failed = unsaved(round);
          const here = round === current;
          return (
            <li key={round}>
              <Button
                type="button"
                variant="outline"
                aria-label={pillLabel(round, state, failed)}
                aria-current={here ? "step" : undefined}
                aria-disabled={locked || undefined}
                onClick={() => !locked && onPick(round)}
                className={cn(
                  "h-auto min-h-11 w-full min-w-11 gap-0.5 border-2 px-1 tabular-nums hover:bg-surface-2",
                  SHAPE[state],
                  // the current pill must not look like keyboard focus (--ring is the foreground): thicker, brand, underlined
                  here && "font-bold underline decoration-2 underline-offset-2 ring-[3px] ring-brand ring-offset-2 ring-offset-background",
                )}
              >
                {round}
                <StateGlyph state={state} unsaved={failed} />
              </Button>
            </li>
          );
        })}
      </ol>
      <ul aria-label="Legend" className="flex flex-col gap-1 text-xs text-foreground">
        {PALETTE_STATES.map((state) => (
          <li key={state} className="flex items-center gap-2">
            <span aria-hidden className={cn("grid h-5 w-5 shrink-0 place-items-center border-2", SHAPE[state])}>
              <StateGlyph state={state} className="h-3 w-3" />
            </span>
            <span className="min-w-0 flex-1">{PALETTE_LABEL[state]}</span>
            <span className="tabular-nums font-semibold">{counts[state]}</span>
          </li>
        ))}
        {counts.notSaved > 0 && (
          <li className="flex items-center gap-2">
            <span aria-hidden className="grid h-5 w-5 shrink-0 place-items-center">
              <TriangleAlert className="h-3.5 w-3.5 text-warning" />
            </span>
            <span className="min-w-0 flex-1">Not saved</span>
            <span className="tabular-nums font-semibold">{counts.notSaved}</span>
          </li>
        )}
      </ul>
      {footer}
    </nav>
  );
}

