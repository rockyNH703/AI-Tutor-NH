"use client";

import { DragDropProvider, DragOverlay, PointerSensor } from "@dnd-kit/react";
import { isSortable, useSortable } from "@dnd-kit/react/sortable";
import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { ArrowDown, ArrowUp, GripVertical } from "lucide-react";
import { useId, useLayoutEffect, useRef, useState } from "react";

import { InlineMarkdown } from "../../../message-content";
import { prefersReducedMotion } from "../../tokens";
import type { KindPack } from "../kinds";
import type { InputProps, KindModule, ResultProps } from "../question-props";
import { FieldError, OutcomeIcon, QuestionLine, Why, ids } from "./choice";

// Pointer only. Keyboard and screen-reader users reorder with the Move buttons, so the drag handle is
// hidden from both (aria-hidden, tabIndex -1) and there is no keyboard drag to reach or announce.
const SENSORS = [PointerSensor];

/** Whether a value is an order of this question's shown items (each shown index exactly once). */
export function isOrder(raw: unknown, count: number): raw is number[] {
  return (
    Array.isArray(raw) && raw.length === count && [...raw].sort((a, b) => a - b).every((item, at) => item === at)
  );
}

/** The order with the item at `from` moved to `to` (a new list; out-of-range moves change nothing). */
export function moved(order: readonly number[], from: number, to: number): number[] {
  const next = [...order];
  if (from < 0 || from >= next.length || to < 0 || to >= next.length) return next;
  next.splice(to, 0, ...next.splice(from, 1));
  return next;
}

/** After a Move: the moved item's same button, or its other one once it reached an end (that one is disabled). */
export function nextFocus(order: readonly number[], item: number, up: boolean): "up" | "down" {
  const at = order.indexOf(item);
  const atEnd = up ? at === 0 : at === order.length - 1;
  if (atEnd) return up ? "down" : "up";
  return up ? "up" : "down";
}

/** A label without Markdown's code ticks (the item itself is drawn with its Markdown). */
const spoken = (text: string) => text.replace(/`/g, "");

/** What a Move says once done: "'Ping the gateway' is now 2 of 5." (`at` is 0-based). */
export function positionNote(text: string, at: number, count: number): string {
  return `'${spoken(text)}' is now ${at + 1} of ${count}.`;
}

type RowProps = {
  base: string;
  item: number;
  text: string;
  at: number;
  last: boolean;
  disabled: boolean;
  still: boolean;
  onStep: (at: number, up: boolean) => void;
};

function OrderRow({ base, item, text, at, last, disabled, still, onStep }: RowProps) {
  const { ref, handleRef, isDragSource } = useSortable({
    id: text, // unique within a question (the server refuses duplicate items)
    index: at,
    disabled,
    transition: still ? null : undefined,
  });
  const name = spoken(text);
  return (
    <li
      ref={ref}
      className={cn(
        "flex items-center gap-1 rounded-lg border border-border bg-card p-1 text-sm leading-6",
        isDragSource && "opacity-50",
      )}
    >
      {/* The drag handle: pointer only, so hidden from the keyboard and screen readers (see SENSORS). */}
      <span
        ref={handleRef}
        data-drag-handle
        aria-hidden
        tabIndex={-1}
        className={cn(
          "flex min-h-11 min-w-11 shrink-0 touch-none items-center justify-center rounded-md text-text-secondary",
          disabled ? "cursor-not-allowed opacity-50" : "cursor-grab active:cursor-grabbing",
        )}
      >
        <GripVertical className="h-4 w-4" />
      </span>
      <span className="shrink-0 tabular-nums text-text-secondary">{at + 1}.</span>
      <span className="min-w-0 flex-1 break-words px-1 py-2">
        <InlineMarkdown text={text} />
      </span>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        id={`${base}-${item}-up`}
        aria-label={`Move '${name}' up`}
        disabled={disabled || at === 0}
        onClick={() => onStep(at, true)}
        className="min-h-11 min-w-11 shrink-0"
      >
        <ArrowUp aria-hidden className="h-4 w-4" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        id={`${base}-${item}-down`}
        aria-label={`Move '${name}' down`}
        disabled={disabled || last}
        onClick={() => onStep(at, false)}
        className="min-h-11 min-w-11 shrink-0"
      >
        <ArrowDown aria-hidden className="h-4 w-4" />
      </Button>
    </li>
  );
}

/** Ordering: Move up/down buttons on every item (the keyboard path), plus a pointer drag on a handle. */
function OrderInput({ question, value, onChange, disabled, describedBy, invalid }: InputProps<number[]>) {
  const base = useId();
  const items = question.items ?? [];
  // Read at each drag start: OS or app reduced motion means no drop animation and no sliding rows.
  const [still, setStill] = useState(false);
  const moving = useRef<{ item: number; up: boolean } | null>(null);
  // Said after a Move: focus stays on the button, so nothing else tells where the item went.
  const [note, setNote] = useState("");

  useLayoutEffect(() => {
    const last = moving.current;
    if (!last) return;
    moving.current = null;
    const button = document.getElementById(`${base}-${last.item}-${nextFocus(value, last.item, last.up)}`);
    button?.focus({ preventScroll: true });
    button?.scrollIntoView({ block: "nearest" });
  }, [value, base]);

  const step = (at: number, up: boolean) => {
    const item = value[at];
    const to = up ? at - 1 : at + 1;
    if (item === undefined || to < 0 || to >= value.length) return;
    moving.current = { item, up };
    setNote(positionNote(items[item] ?? "", to, value.length));
    onChange(moved(value, at, to));
  };

  return (
    <DragDropProvider
      sensors={SENSORS}
      onBeforeDragStart={() => setStill(prefersReducedMotion())}
      onDragEnd={(event) => {
        const { source } = event.operation;
        if (event.canceled || !isSortable(source) || source.initialIndex === source.index) return;
        onChange(moved(value, source.initialIndex, source.index));
      }}
    >
      <ol aria-describedby={ids(describedBy, invalid && `${base}-error`)} className="space-y-2">
        {value.map((item, at) => (
          <OrderRow
            key={item}
            base={base}
            item={item}
            text={items[item] ?? ""}
            at={at}
            last={at === value.length - 1}
            disabled={disabled}
            still={still}
            onStep={step}
          />
        ))}
      </ol>
      <DragOverlay dropAnimation={still ? null : undefined}>
        {(source) => (
          <div className="flex min-h-11 items-center gap-1 rounded-lg border border-brand bg-card p-1 text-sm leading-6 elev-3">
            <span aria-hidden className="flex min-h-11 min-w-11 items-center justify-center text-text-secondary">
              <GripVertical className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1 break-words px-1 py-2">
              <InlineMarkdown text={String(source.id)} />
            </span>
          </div>
        )}
      </DragOverlay>
      <p role="status" className="sr-only">
        {note}
      </p>
      <FieldError id={`${base}-error`} text={invalid} />
    </DragDropProvider>
  );
}

/** The order given, one row per position with its mark; a skipped question lists the right order. */
function OrderResult({ question, number, open }: ResultProps) {
  const response = question.response;
  const key = question.correctList;
  const items = question.items ?? [];
  if (!response || !key) return null;
  const given =
    response.status === "answered" && isOrder(response.answer, items.length) ? response.answer : null;
  const marks = (given ?? []).map((item, at) => response.marks?.[at] ?? item === key[at]);
  const placed = marks.filter(Boolean).length;
  return (
    <>
      <QuestionLine number={number} text={question.text} />
      <p className="mt-1">
        {!given
          ? "Skipped. The right order:"
          : placed === items.length
            ? "Correct! All in order."
            : `Not quite: ${placed} of ${items.length} in place.`}
      </p>
      <ol className="mt-1 space-y-1">
        {(given ?? key).map((item, at) => {
          const ok = given ? (marks[at] ?? false) : null;
          return (
            <li key={item} className="flex items-start gap-1.5">
              {ok === null ? null : <OutcomeIcon outcome={ok ? "right" : "wrong"} />}
              <span className="min-w-0 flex-1 break-words">
                {at + 1}. <InlineMarkdown text={items[item] ?? ""} />
                {ok === null ? null : ok ? (
                  " · correct"
                ) : (
                  <>
                    {" · wrong, should be "}
                    <InlineMarkdown text={items[key[at] ?? -1] ?? ""} />
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ol>
      <Why text={question.explanation} open={open} />
    </>
  );
}

const order: KindModule<number[]> = {
  Input: OrderInput,
  Result: OrderResult,
  // The shown order: the server never shows the right order first (kinds.py rotates it).
  empty: (question) => (question.items ?? []).map((_, at) => at),
  isComplete: (question, value) => isOrder(value, question.items?.length ?? 0),
  toRequest: (_question, value) => value,
  fromRecord: (question) => {
    const count = question.items?.length ?? 0;
    const answer = question.response?.answer;
    if (isOrder(question.draft, count)) return question.draft;
    return isOrder(answer, count) ? answer : null;
  },
};

const pack: KindPack = { order };

export default pack;
