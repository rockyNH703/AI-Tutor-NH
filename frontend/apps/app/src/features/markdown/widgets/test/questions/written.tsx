"use client";

import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { type Ref, useContext, useId, useState } from "react";

import { shouldSubmit } from "@/features/chat/keyboard";
import type { AssessmentQuestion } from "@/lib/api/types";
import { ReleaseScrollContext, TestContext } from "../../../context";
import { Markdown } from "../../../message-content";
import type { KindPack } from "../kinds";
import type { InputProps, KindModule, ResultProps } from "../question-props";
import { Disclosure, FieldError, OutcomeIcon, OutcomeLine, QuestionLine, Why, asText, ids, outcomeOf } from "./choice";

/** The server's limits (app/assessments/kinds.py), used when an older record doesn't say. */
const LIMIT = { short: 1000, long: 6000 } as const;
/** The counter shows from this share of the limit on, like the composer's near its own limit. */
const COUNTER_FROM = 0.9;

export function limitOf(question: AssessmentQuestion): number {
  return question.maxLength ?? (question.kind === "long" ? LIMIT.long : LIMIT.short);
}

export function counterShown(length: number, limit: number): boolean {
  return length >= Math.floor(limit * COUNTER_FROM);
}

/** Ctrl+Enter (⌘+Enter on a Mac) checks; Enter alone is a new line. Never while an input method composes. */
export function checksAnswer(event: Parameters<typeof shouldSubmit>[0] & { ctrlKey: boolean; metaKey: boolean }): boolean {
  return (event.ctrlKey || event.metaKey) && shouldSubmit(event);
}

/**
 * A reply's Markdown renderer for the user's own text (Preview, "Your answer"): never raw HTML (the
 * renderer has none), and outside the reply's tests, so a practice block typed here only shows its source.
 */
function OwnText({ text }: { text: string }) {
  return (
    <TestContext.Provider value={null}>
      <Markdown text={text} />
    </TestContext.Provider>
  );
}

function WrittenInput({
  question,
  value,
  onChange,
  onCommit,
  onSubmit,
  disabled,
  mode,
  labelledBy,
  describedBy,
  invalid,
  inputRef,
}: InputProps<string>) {
  const id = useId();
  const release = useContext(ReleaseScrollContext);
  const [preview, setPreview] = useState(false);
  const long = question.kind === "long";
  const limit = limitOf(question);
  const counter = counterShown(value.length, limit) ? `${id}-count` : null;
  const keys = onSubmit ? `${id}-keys` : null;
  return (
    <div>
      {long ? (
        <div className="mb-2 flex flex-wrap gap-2">
          <Button type="button" variant={preview ? "ghost" : "secondary"} aria-pressed={!preview} onClick={() => setPreview(false)}>
            Write
          </Button>
          <Button
            type="button"
            variant={preview ? "secondary" : "ghost"}
            aria-pressed={preview}
            onClick={() => {
              release();
              setPreview(true);
            }}
          >
            Preview
          </Button>
        </div>
      ) : null}
      {preview ? (
        <div
          // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
          tabIndex={0}
          role="region"
          aria-label="Preview of your answer"
          className="focus-ring max-h-96 min-h-24 overflow-auto rounded-xl border border-border bg-card px-3 py-2"
        >
          {value.trim() ? <OwnText text={value} /> : <p className="text-sm text-text-secondary">Nothing to preview yet.</p>}
        </div>
      ) : (
        <textarea
          ref={inputRef as Ref<HTMLTextAreaElement>}
          value={value}
          rows={long ? 8 : 3}
          maxLength={limit}
          onChange={(event) => onChange(event.target.value)}
          onBlur={onCommit}
          onKeyDown={(event) => {
            if (!checksAnswer(event)) return;
            event.preventDefault();
            onCommit?.();
            onSubmit?.();
          }}
          disabled={disabled}
          aria-labelledby={labelledBy}
          aria-describedby={ids(invalid && `${id}-error`, keys, counter, describedBy)}
          aria-invalid={invalid ? true : undefined}
          className="focus-field block w-full resize-y rounded-xl border border-input bg-card px-3 py-2 text-base leading-normal text-foreground disabled:cursor-not-allowed disabled:bg-surface-2 disabled:opacity-60 aria-[invalid=true]:border-destructive sm:text-sm"
        />
      )}
      {keys || counter ? (
        <div className="mt-1 flex justify-end text-2xs text-text-secondary">
          {keys ? (
            <span id={keys} className="sr-only">
              Enter adds a new line; Ctrl+Enter (⌘+Enter on a Mac) {mode === "exam" ? "saves" : "checks"} your answer.
            </span>
          ) : null}
          {counter ? (
            <span id={counter} className={cn("tabular-nums", value.length > limit && "font-semibold text-destructive")}>
              {value.length.toLocaleString()} / {limit.toLocaleString()}
              <span className="sr-only"> characters</span>
            </span>
          ) : null}
        </div>
      ) : null}
      <FieldError id={`${id}-error`} text={invalid} />
    </div>
  );
}

function WrittenResult({ question, number, open }: ResultProps) {
  const response = question.response;
  const points = question.keyPoints;
  if (!response || !points) return null;
  const outcome = outcomeOf(response);
  const wrote = asText(response.answer);
  const found = `${response.earned} of ${points.length} key point${points.length === 1 ? "" : "s"} found.`;
  return (
    <>
      <QuestionLine number={number} text={question.text} />
      <OutcomeLine outcome={outcome}>
        {outcome === "skipped"
          ? "Skipped. The key points are listed below."
          : outcome === "right"
            ? `Correct! ${found}`
            : outcome === "partly"
              ? `Partly right: ${found}`
              : `Not quite: ${found}`}
      </OutcomeLine>
      <ul aria-label="Key points" className="mt-1 space-y-1">
        {points.map((point, index) => {
          const mark = outcome === "skipped" ? null : Boolean(response.marks?.[index]);
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: key points are positions in the question
            <li key={index} className="flex items-start gap-1.5">
              {mark === null ? (
                <span aria-hidden className="h-4 w-4 shrink-0" />
              ) : (
                <OutcomeIcon outcome={mark ? "right" : "wrong"} />
              )}
              <span className="min-w-0 flex-1 break-words">
                {point}
                {mark === null ? null : (
                  <span className="text-xs font-medium text-text-secondary"> ({mark ? "found" : "missed"})</span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {wrote?.trim() ? (
        <Disclosure title="Your answer">
          <OwnText text={wrote} />
        </Disclosure>
      ) : null}
      {question.sample ? (
        <Disclosure title="Sample answer" open={open}>
          <OwnText text={question.sample} />
        </Disclosure>
      ) : null}
      <Why text={question.explanation} open={open} />
    </>
  );
}

const written: KindModule<string> = {
  Input: WrittenInput,
  Result: WrittenResult,
  empty: () => "",
  isComplete: (_question, value) => value.trim() !== "",
  check: (question, value) =>
    value.length > limitOf(question) ? `Keep your answer to ${limitOf(question).toLocaleString()} characters.` : null,
  toRequest: (_question, value) => value,
  fromRecord: (question) => asText(question.draft) ?? asText(question.response?.answer),
};

const pack: KindPack = { short: written, long: written };

export default pack;
