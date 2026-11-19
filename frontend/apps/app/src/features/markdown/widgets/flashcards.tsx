"use client";

import { Button } from "@v1/ui/button";
import { Check, ChevronLeft, ChevronRight, Layers, RotateCcw, RotateCw } from "lucide-react";
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";

import { focusLost } from "@/features/navigation/route-focus";
import { InlineMarkdown } from "../message-content";
import type { WidgetProps } from "../rich-block";
import { CardFrame } from "../study-card";
import { parseJson } from "./tokens";

/** ```flashcards, as the tutor writes it (unchanged). */
export const FlashcardDeck = z.object({
  title: z.string().max(200).optional(),
  cards: z
    .array(z.object({ front: z.string().min(1).max(1000), back: z.string().min(1).max(2000) }))
    .min(1)
    .max(100),
});

type Mark = "known" | "learning";

/** Where someone is in a deck: this round's cards (deck indices), the one shown, each card's mark, the summary. */
export type Progress = { queue: number[]; at: number; marks: (Mark | null)[]; done: boolean };

export function freshProgress(total: number): Progress {
  return { queue: Array.from({ length: total }, (_, index) => index), at: 0, marks: Array(total).fill(null), done: false };
}

/** The tab's storage key for a deck: a synchronous hash (FNV-1a, 32 bits) of its source, so the same deck resumes. */
export function deckKey(source: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < source.length; index++) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `netsecai.flashcards.${(hash >>> 0).toString(36)}`;
}

const Stored = z.object({
  queue: z.array(z.number().int()).max(100),
  at: z.number().int(),
  marks: z.array(z.enum(["known", "learning"]).nullable()).max(100),
  done: z.boolean(),
});

/** Saved progress for a deck of `total` cards, checked and clamped; anything else starts afresh. */
export function readProgress(raw: string | null, total: number): Progress {
  const parsed = Stored.safeParse(raw ? parseJson(raw) : null);
  if (!parsed.success || parsed.data.marks.length !== total) return freshProgress(total);
  const queue = [...new Set(parsed.data.queue)].filter((index) => index >= 0 && index < total);
  if (queue.length === 0) return freshProgress(total);
  const at = Math.min(Math.max(parsed.data.at, 0), queue.length - 1);
  return { queue, at, marks: parsed.data.marks, done: parsed.data.done };
}

/** Marks the card shown and moves on; after the round's last card, the summary. */
export function markCard(progress: Progress, mark: Mark): Progress {
  const card = progress.queue[progress.at];
  if (card === undefined) return progress;
  const marks = progress.marks.map((value, index) => (index === card ? mark : value));
  const last = progress.at >= progress.queue.length - 1;
  return { ...progress, marks, at: last ? progress.at : progress.at + 1, done: last };
}

/** A new round of the cards still being learned; unchanged when there are none. */
export function reviewLearning(progress: Progress): Progress {
  const queue = progress.marks.flatMap((mark, index) => (mark === "learning" ? [index] : []));
  return queue.length ? { ...progress, queue, at: 0, done: false } : progress;
}

function loadProgress(key: string, total: number): Progress {
  if (typeof window === "undefined") return freshProgress(total);
  try {
    return readProgress(window.sessionStorage.getItem(key), total);
  } catch {
    return freshProgress(total); // storage blocked (a private window, site data off): start afresh
  }
}

function saveProgress(key: string, progress: Progress): void {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(progress));
  } catch {
    // Storage blocked or full: progress then lasts until the page closes, which is all it is.
  }
}

/** ```flashcards: a deck to revise with, a card at a time; Know it / Still learning, then a round summary. */
export default function Flashcards({ source, onError }: WidgetProps) {
  const deck = useMemo(() => {
    const parsed = FlashcardDeck.safeParse(parseJson(source));
    return parsed.success ? parsed.data : null;
  }, [source]);
  const key = useMemo(() => deckKey(source), [source]);
  const total = deck?.cards.length ?? 0;
  const [progress, setProgress] = useState(() => loadProgress(key, total));
  const [back, setBack] = useState(false);
  // Set by the control that is about to disappear (the last mark, Review, Start over).
  const focusNext = useRef<"face" | "summary" | null>(null);
  const face = useRef<HTMLDivElement>(null);
  const summary = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (!deck) onError();
  }, [deck, onError]);
  useEffect(() => {
    if (deck) saveProgress(key, progress);
  }, [deck, key, progress]);
  useEffect(() => {
    const target = focusNext.current === "summary" ? summary.current : focusNext.current === "face" ? face.current : null;
    focusNext.current = null;
    if (!target || !focusLost()) return;
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "nearest" });
  }, [progress.done]);

  if (!deck) return null;
  const { queue, at, marks, done } = progress;
  const index = queue[at] ?? 0;
  const card = deck.cards[index];
  if (!card) return null;
  const count = queue.length;

  const restart = (next: Progress) => {
    focusNext.current = "face";
    setProgress(next);
    setBack(false);
  };

  if (done) {
    const known = marks.filter((mark) => mark === "known").length;
    const learning = marks.filter((mark) => mark === "learning").length;
    return (
      <CardFrame kind="flashcards" icon={Layers} label="Flashcards" title={deck.title}>
        <div className="rounded-xl border border-border bg-card px-4 py-5 text-center">
          <p ref={summary} tabIndex={-1} className="focus-ring rounded-md text-base font-semibold leading-7">
            Round finished. You know {known} of {total} {total === 1 ? "card" : "cards"}.
            {learning ? ` Still learning: ${learning}.` : ""}
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {learning ? (
              <Button type="button" onClick={() => restart(reviewLearning(progress))}>
                Review still learning ({learning})
              </Button>
            ) : null}
            <Button type="button" variant="outline" onClick={() => restart(freshProgress(total))}>
              <RotateCcw aria-hidden className="h-4 w-4" />
              Start over
            </Button>
          </div>
        </div>
      </CardFrame>
    );
  }

  const go = (step: number) => {
    if (at + step < 0 || at + step >= count) return false;
    setProgress((current) => ({ ...current, at: current.at + step }));
    setBack(false);
    return true;
  };
  const mark = (value: Mark) => {
    if (at >= count - 1) focusNext.current = "summary";
    setProgress((current) => markCard(current, value));
    setBack(false);
  };
  // ←/→ anywhere in the deck; only a press that changes the card is taken from the page.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step && go(step)) event.preventDefault();
  };
  const position = `${at + 1} of ${count}`;
  const ends = "aria-disabled:cursor-not-allowed aria-disabled:opacity-50";

  return (
    <CardFrame kind="flashcards" icon={Layers} label="Flashcards" title={deck.title}>
      <div role="group" aria-label="Deck" onKeyDown={onKeyDown}>
        <div className="mb-2 flex items-center gap-3 text-xs text-text-secondary">
          <span className="shrink-0 tabular-nums">{position}</span>
          <div aria-hidden className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-brand" style={{ width: `${((at + 1) / count) * 100}%` }} />
          </div>
        </div>
        <div
          ref={face}
          // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
          tabIndex={0}
          role="region"
          aria-label={`Card ${position}, ${back ? "answer" : "question"}`}
          aria-live="polite"
          className="focus-ring scrollbar-subtle max-h-80 min-h-40 overflow-y-auto rounded-xl border border-border bg-card px-4 py-6 text-center"
        >
          {/* Only the face shown is mounted; a new one rises in (none under reduced motion: globals.css). */}
          <div key={`${index}:${back}`} className="animate-rise-in flex min-h-28 flex-col items-center justify-center gap-2">
            <span className="text-2xs font-semibold uppercase tracking-wide text-text-secondary">
              {back ? "Answer" : "Question"}
            </span>
            <div className="max-w-full break-words text-base leading-7">
              <InlineMarkdown text={back ? card.back : card.front} />
            </div>
          </div>
        </div>
        <Button type="button" variant="outline" className="mt-3 w-full" onClick={() => setBack((shown) => !shown)}>
          <RotateCw aria-hidden className="h-4 w-4" />
          {back ? "Show question" : "Show answer"}
        </Button>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <Button type="button" variant="outline" onClick={() => mark("learning")}>
            Still learning
          </Button>
          <Button type="button" variant="outline" onClick={() => mark("known")}>
            <Check aria-hidden className="h-4 w-4" />
            Know it
          </Button>
        </div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <Button type="button" variant="ghost" className={ends} aria-disabled={at === 0} onClick={() => go(-1)}>
            <ChevronLeft aria-hidden className="h-4 w-4" />
            Previous
          </Button>
          <Button type="button" variant="ghost" className={ends} aria-disabled={at >= count - 1} onClick={() => go(1)}>
            Next
            <ChevronRight aria-hidden className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </CardFrame>
  );
}
