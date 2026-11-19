"use client";

import { cn } from "@v1/ui/utils";
import { Check, CircleCheck, CircleDashed, CircleX, type LucideIcon } from "lucide-react";
import { type KeyboardEvent, type ReactNode, useId, useRef, useState } from "react";

import type { AssessmentListing } from "@/lib/api/types";
import type { KindPack } from "../kinds";
import type { InputProps, KindModule, ResultProps } from "../question-props";
import { FieldError, OutcomeLine, QuestionLine, Why, asNumbers, ids, toggled } from "./choice";

/**
 * A listing's lines, numbered as the server counts them (app/assessments/kinds.py): CRLF and CR become
 * LF, one trailing newline is dropped. Line N on screen is index N-1 in an answer.
 */
export function listingLines(content: string): string[] {
  const text = content.replace(/\r\n?/g, "\n");
  return (text.endsWith("\n") ? text.slice(0, -1) : text).split("\n");
}

/** Code, a log or a config the question refers to, read-only, with line numbers; scrolls in its own region. */
export function ListingView({ listing }: { listing: AssessmentListing }) {
  const lines = listingLines(listing.content);
  return (
    <figure className="my-2 min-w-0">
      {listing.title ? <figcaption className="mb-1 text-xs font-medium text-text-secondary">{listing.title}</figcaption> : null}
      <div
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
        tabIndex={0}
        role="region"
        aria-label={listing.title ?? "Listing"}
        className="focus-ring max-h-96 overflow-auto rounded-lg border border-border bg-surface-2 py-1 font-mono text-[0.8125rem] leading-6"
      >
        <ol className="min-w-max">
          {lines.map((line, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: lines are positions in the listing
            <li key={index} className="flex gap-3 px-3">
              <span aria-hidden className="w-8 shrink-0 select-none text-right tabular-nums text-text-secondary">
                {index + 1}
              </span>
              <span className="whitespace-pre">{line || " "}</span>
            </li>
          ))}
        </ol>
      </div>
    </figure>
  );
}

// ------------------------------------------------- shared by the kinds that choose (lines, diagram) and cli

/** How one choosable thing (a line, a device, a link) is drawn: chosen while answering; marked once final. */
export type ElementState = "chosen" | "right" | "wrong" | "missed";

export const MARKS: Record<ElementState, { Icon: LucideIcon; tone: string; word: string }> = {
  chosen: { Icon: Check, tone: "text-brand", word: "chosen" },
  right: { Icon: CircleCheck, tone: "text-success", word: "your choice, right" },
  wrong: { Icon: CircleX, tone: "text-destructive", word: "your choice, not in the answer" },
  missed: { Icon: CircleDashed, tone: "text-text-secondary", word: "in the answer, not chosen" },
};

/** Without `right`: chosen or not. With it (a final question): right, wrong, missed, or nothing to mark. */
export function stateOf<T>(item: T, chosen: readonly T[], right?: readonly T[] | null): ElementState | null {
  const picked = chosen.includes(item);
  if (!right) return picked ? "chosen" : null;
  const wanted = right.includes(item);
  return picked ? (wanted ? "right" : "wrong") : wanted ? "missed" : null;
}

export function MarkGlyph({ state }: { state: ElementState }) {
  const { Icon, tone } = MARKS[state];
  return <Icon aria-hidden className={cn("h-4 w-4 shrink-0", tone)} />;
}

/**
 * A final question: "Q3." and the question, what happened (icon + word), what was given (unless skipped)
 * and what was right (unless it was), the kind's own detail, and Why.
 */
export function Outcome({
  question,
  number,
  open,
  given,
  right,
  children,
}: Omit<ResultProps, "compact"> & { given: ReactNode; right: ReactNode; children?: ReactNode }) {
  const response = question.response;
  if (!response) return null;
  const skipped = response.status === "skipped";
  const correct = !skipped && response.earned > 0;
  return (
    <>
      <QuestionLine number={number} text={question.text} />
      <OutcomeLine outcome={skipped ? "skipped" : correct ? "right" : "wrong"}>
        <span className="font-medium">{skipped ? "Skipped." : correct ? "Correct." : "Not quite."}</span>
        {skipped ? null : <> {given}</>}
        {correct ? null : <> {right}</>}
      </OutcomeLine>
      {children}
      <Why text={question.explanation} open={open} />
    </>
  );
}

// ----------------------------------------------------------------------------------- the "lines" kind

/** Where an arrow, Home or End moves the active line (APG listbox), or null for any other key. */
export function lineAfterKey(key: string, active: number, count: number): number | null {
  if (key === "ArrowDown") return Math.min(active + 1, count - 1);
  if (key === "ArrowUp") return Math.max(active - 1, 0);
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

/** "line 3", "lines 3, 7" (1-based, in order) or "no lines", from 0-based indices. */
export function linesText(indices: readonly number[]): string {
  const numbers = [...indices].sort((a, b) => a - b).map((index) => index + 1);
  if (numbers.length === 0) return "no lines";
  return numbers.length === 1 ? `line ${numbers[0]}` : `lines ${numbers.join(", ")}`;
}

type LinesBoxProps = {
  listing: AssessmentListing;
  chosen: readonly number[];
  /** Toggles a line; absent: read-only (a result). */
  onToggle?: (index: number) => void;
  /** A final question's right lines, marked against the chosen ones. */
  right?: readonly number[] | null;
  labelledBy?: string;
  describedBy?: string;
  invalid?: boolean;
  disabled?: boolean;
};

/**
 * The listing as an APG multi-select listbox: one tab stop (a roving tabindex), ↑/↓/Home/End move, Space
 * selects or clears the line, a click does both. Lines wrap (no sideways scroll at 320 px) and are at
 * least 24 px tall. Read-only (aria-readonly) when it shows a result.
 */
function LinesBox({ listing, chosen, onToggle, right, labelledBy, describedBy, invalid, disabled }: LinesBoxProps) {
  const lines = listingLines(listing.content);
  const titleId = useId();
  // The tab stop starts on the first chosen line (APG), else the first line.
  const [active, setActive] = useState(() => Math.min(chosen.length > 0 ? Math.min(...chosen) : 0, lines.length - 1));
  const options = useRef<(HTMLDivElement | null)[]>([]);
  const toggle = disabled ? undefined : onToggle;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === " ") {
      event.preventDefault(); // Space chooses a line here; it never pages the transcript
      toggle?.(active);
      return;
    }
    const next = lineAfterKey(event.key, active, lines.length);
    if (next === null) return;
    event.preventDefault();
    setActive(next);
    options.current[next]?.focus();
  };

  return (
    <figure className="my-2 min-w-0">
      {listing.title ? (
        <figcaption id={titleId} className="mb-1 text-xs font-medium text-text-secondary">
          {listing.title}
        </figcaption>
      ) : null}
      <div
        role="listbox"
        aria-multiselectable
        aria-readonly={onToggle ? undefined : true}
        aria-disabled={disabled || undefined}
        aria-labelledby={ids(labelledBy, listing.title && titleId)}
        aria-label={labelledBy || listing.title ? undefined : "Listing"}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        onKeyDown={onKeyDown}
        className="max-h-96 overflow-auto rounded-lg border border-border bg-surface-2 p-1 font-mono text-[0.8125rem] leading-6 aria-[invalid=true]:border-destructive"
      >
        {lines.map((line, index) => {
          const state = stateOf(index, chosen, right);
          const selected = chosen.includes(index);
          return (
            // biome-ignore lint/a11y/useKeyWithClickEvents: the listbox handles the keys (APG roving tabindex)
            <div
              // biome-ignore lint/suspicious/noArrayIndexKey: lines are positions in the listing
              key={index}
              ref={(element) => {
                options.current[index] = element;
              }}
              role="option"
              aria-selected={selected}
              tabIndex={index === active ? 0 : -1}
              onFocus={() => setActive(index)}
              onClick={() => toggle?.(index)}
              className={cn(
                "focus-ring-inset flex min-h-6 gap-2 rounded border px-1.5",
                selected ? "border-brand bg-brand/10" : "border-transparent",
                toggle && "cursor-pointer hover:border-brand/50",
              )}
            >
              <span aria-hidden className="w-7 shrink-0 select-none text-right tabular-nums text-text-secondary">
                {index + 1}
              </span>
              <span aria-hidden className="flex h-6 w-4 shrink-0 items-center">
                {state ? <MarkGlyph state={state} /> : null}
              </span>
              <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
                <span className="sr-only">
                  Line {index + 1}
                  {state && state !== "chosen" ? `, ${MARKS[state].word}` : ""}:{" "}
                </span>
                {line || " "}
              </span>
            </div>
          );
        })}
      </div>
    </figure>
  );
}

function LinesInput({ question, value, onChange, disabled, labelledBy, describedBy, invalid }: InputProps<number[]>) {
  const errorId = useId();
  if (!question.listing) return null;
  return (
    <>
      <LinesBox
        listing={question.listing}
        chosen={value}
        onToggle={(index) => onChange(toggled(value, index))}
        disabled={disabled}
        labelledBy={labelledBy}
        describedBy={ids(describedBy, invalid && errorId)}
        invalid={Boolean(invalid)}
      />
      <FieldError id={errorId} text={invalid} />
    </>
  );
}

/** What was chosen, the right lines, and the listing with both marked (review.tsx draws no second copy). */
function LinesResult({ question, number, open }: ResultProps) {
  const chosen = asNumbers(question.response?.answer) ?? [];
  const right = question.correctList ?? [];
  return (
    <Outcome
      question={question}
      number={number}
      open={open}
      given={`You chose ${linesText(chosen)}.`}
      right={`The answer is ${linesText(right)}.`}
    >
      {question.listing ? <LinesBox listing={question.listing} chosen={chosen} right={right} /> : null}
    </Outcome>
  );
}

export const lines: KindModule<number[]> = {
  Input: LinesInput,
  Result: LinesResult,
  empty: () => [],
  isComplete: (_question, value) => value.length > 0,
  toRequest: (_question, value) => [...value].sort((a, b) => a - b),
  fromRecord: (question) => asNumbers(question.draft) ?? asNumbers(question.response?.answer),
};

const pack: KindPack = { lines };

export default pack;
