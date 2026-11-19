"use client";

import { cn } from "@v1/ui/utils";
import { CircleCheck, CircleDot, CircleX, SkipForward } from "lucide-react";
import { type FocusEvent, type ReactNode, type Ref, useContext, useId } from "react";

import type { AssessmentAnswer, AssessmentQuestion, AssessmentValue } from "@/lib/api/types";
import { LETTERS } from "../../../assessment";
import { ReleaseScrollContext } from "../../../context";
import { InlineMarkdown } from "../../../message-content";
import type { KindPack } from "../kinds";
import type { InputProps, KindModule, ResultProps } from "../question-props";

// ------------------------------------------------------------------ parts the kinds in this folder share

/** Space-separated ids for aria-describedby, or undefined when there are none. */
export function ids(...parts: (string | false | null | undefined)[]): string | undefined {
  return parts.filter(Boolean).join(" ") || undefined;
}

/** A recorded value (a draft, a response's answer) when it has the shape a kind expects, else null. */
export function asNumbers(value: AssessmentValue | null | undefined): number[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "number") ? (value as number[]) : null;
}
export function asTexts(value: AssessmentValue | null | undefined): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? (value as string[]) : null;
}
export function asText(value: AssessmentValue | null | undefined): string | null {
  return typeof value === "string" ? value : null;
}

/** A blur that takes focus out of the element, not to another control inside it: the kind's "done editing". */
export function onLeave(commit?: () => void) {
  return (event: FocusEvent<HTMLElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) commit?.();
  };
}

export type Outcome = "right" | "partly" | "wrong" | "skipped";

export function outcomeOf(response: AssessmentAnswer): Outcome {
  if (response.status === "skipped") return "skipped";
  if (response.earned > 0 && response.earned >= response.possible) return "right";
  return response.earned > 0 ? "partly" : "wrong";
}

/** The mark beside a result; the words next to it always say the same (never colour or icon alone). */
export function OutcomeIcon({ outcome }: { outcome: Outcome }) {
  const className = "mt-1 h-4 w-4 shrink-0";
  if (outcome === "skipped") return <SkipForward aria-hidden className={cn(className, "text-text-secondary")} />;
  if (outcome === "right") return <CircleCheck aria-hidden className={cn(className, "text-success")} />;
  if (outcome === "partly") return <CircleDot aria-hidden className={cn(className, "text-warning")} />;
  return <CircleX aria-hidden className={cn(className, "text-destructive")} />;
}

/** A result's first line: "Q3." (none for a one-question block) and the question. */
export function QuestionLine({ number, text }: { number: number | null; text: string | null }) {
  return (
    <p className="font-medium">
      {number !== null && <span className="mr-1 text-text-secondary">Q{number}.</span>}
      <InlineMarkdown text={text ?? ""} />
    </p>
  );
}

/** A result line: its mark, then what it says. */
export function OutcomeLine({ outcome, children }: { outcome: Outcome; children: ReactNode }) {
  return (
    <p className="mt-1 flex items-start gap-1.5">
      <OutcomeIcon outcome={outcome} />
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </p>
  );
}

/** A closed-by-default part of a result. Opening it lets go of the transcript's bottom first, so it stays in view. */
export function Disclosure({ title, open, children }: { title: string; open?: boolean; children: ReactNode }) {
  const release = useContext(ReleaseScrollContext);
  return (
    <details open={open} className="mt-1 text-text-secondary">
      {/* py-1: at least 24 px tall, so two disclosures in a row stay separate targets (WCAG 2.5.8). */}
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: a summary's Enter and Space fire click (native, keyboard-operable) */}
      <summary className="focus-ring w-fit cursor-pointer rounded-md py-1 text-xs font-medium" onClick={release}>
        {title}
      </summary>
      <div className="mt-1 text-foreground">{children}</div>
    </details>
  );
}

export function Why({ text, open }: { text: string | null; open?: boolean }) {
  return text ? (
    <Disclosure title="Why" open={open}>
      <InlineMarkdown text={text} />
    </Disclosure>
  ) : null;
}

/** A field's error (a client check or the server's 422), bound to the field by aria-describedby; never live. */
export function FieldError({ id, text }: { id: string; text?: string | null }) {
  return text ? (
    <p id={id} className="mt-1.5 text-xs text-destructive">
      {text}
    </p>
  ) : null;
}

// ------------------------------------------------------------------ single and multiple choice

type ChoicesProps = Pick<
  InputProps<unknown>,
  "question" | "disabled" | "describedBy" | "invalid" | "inputRef" | "onCommit"
> & {
  type: "radio" | "checkbox";
  chosen: (index: number) => boolean;
  onPick: (index: number) => void;
  /** Shown above the options and read with each one ("Choose 2."). */
  hint?: string;
};

/**
 * The options as native radios or checkboxes. The card's fieldset and its legend name the group, so
 * `labelledBy` is not repeated here (a second group name would be read twice); each option reads its
 * own label, then the hint, the card's help and any error.
 */
function Choices({ question, type, chosen, onPick, disabled, describedBy, invalid, inputRef, onCommit, hint }: ChoicesProps) {
  const id = useId();
  const options = question.options ?? [];
  const first = Math.max(0, options.findIndex((_option, index) => chosen(index)));
  return (
    <div onBlur={onLeave(onCommit)}>
      {hint ? (
        <p id={`${id}-hint`} className="mb-2 text-xs text-text-secondary">
          {hint}
        </p>
      ) : null}
      <div className="space-y-2">
        {options.map((option, index) => (
          <label
            // biome-ignore lint/suspicious/noArrayIndexKey: options are positions in the question
            key={index}
            className={cn(
              "flex min-h-11 cursor-pointer items-start gap-2.5 rounded-lg border bg-card px-3 py-2 text-sm leading-6 transition-colors hover:border-brand/50",
              chosen(index) ? "border-brand" : "border-border",
            )}
          >
            <input
              type={type}
              name={id}
              ref={index === first ? (inputRef as Ref<HTMLInputElement>) : undefined}
              checked={chosen(index)}
              onChange={() => onPick(index)}
              disabled={disabled}
              aria-describedby={ids(invalid && `${id}-error`, hint && `${id}-hint`, describedBy)}
              aria-invalid={invalid ? true : undefined}
              className="focus-ring mt-1.5 shrink-0 accent-brand"
            />
            <span className="shrink-0 font-semibold">{LETTERS[index]}.</span>
            <span className="min-w-0 flex-1 break-words">
              <InlineMarkdown text={option} />
            </span>
          </label>
        ))}
      </div>
      <FieldError id={`${id}-error`} text={invalid} />
    </div>
  );
}

/** Single choice: one radio per option. */
function SingleInput({ value, onChange, ...props }: InputProps<number | null>) {
  return <Choices {...props} type="radio" chosen={(index) => value === index} onPick={onChange} />;
}

/** A single-choice question with its response: what was chosen, whether it was right, and why. */
function SingleResult({ question, number, open }: ResultProps) {
  const response = question.response;
  if (!response || question.correct === null) return null;
  const right = `${LETTERS[question.correct]}. `;
  const correct = response.status === "answered" && response.earned > 0;
  return (
    <>
      <QuestionLine number={number} text={question.text} />
      <OutcomeLine outcome={response.status === "skipped" ? "skipped" : correct ? "right" : "wrong"}>
        {response.status === "skipped" ? (
          <>Skipped. The answer is {right}</>
        ) : correct ? (
          <>Correct! You chose {right}</>
        ) : (
          <>
            Not quite: you chose {LETTERS[typeof response.value === "number" ? response.value : 0]}, the answer is {right}
          </>
        )}
        <InlineMarkdown text={question.options?.[question.correct] ?? ""} />
      </OutcomeLine>
      <Why text={question.explanation} open={open} />
    </>
  );
}

const single: KindModule<number | null> = {
  Input: SingleInput,
  Result: SingleResult,
  empty: () => null,
  isComplete: (_question, value) => value !== null,
  toRequest: (_question, value) => value ?? 0,
  fromRecord: (question) =>
    typeof question.draft === "number"
      ? question.draft
      : typeof question.response?.value === "number"
        ? question.response.value
        : null,
};

/** A new list with `item` added or taken out: always sorted and unique, the request's canonical form. */
export function toggled<T extends number | string>(list: readonly T[], item: T): T[] {
  return list.includes(item)
    ? list.filter((each) => each !== item)
    : [...list, item].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}

function letters(indices: readonly number[]): string {
  return indices.map((index) => LETTERS[index]).join(", ") || "none";
}

/** Multiple choice: one checkbox per option, "Choose 2." above them. */
function MultiInput({ value, onChange, question, ...props }: InputProps<number[]>) {
  const hint = question.choose ? `Choose ${question.choose}.` : "Choose every answer that applies.";
  return (
    <Choices
      {...props}
      question={question}
      type="checkbox"
      hint={hint}
      chosen={(index) => value.includes(index)}
      onPick={(index) => onChange(toggled(value, index))}
    />
  );
}

/** What one option was, once final: chosen or not, right or not (null: neither chosen nor right). */
function optionMark(picked: boolean, right: boolean, skipped: boolean): { outcome: Outcome; word: string } | null {
  if (picked) return right ? { outcome: "right", word: "your choice, correct" } : { outcome: "wrong", word: "your choice, wrong" };
  if (!right) return null;
  return skipped ? { outcome: "right", word: "correct" } : { outcome: "wrong", word: "correct, missed" };
}

/** A multiple-choice question with its response: the summary, then every option with its mark. */
function MultiResult({ question, number, open }: ResultProps) {
  const response = question.response;
  const key = question.correctList;
  if (!response || !key) return null;
  const outcome = outcomeOf(response);
  const given = outcome === "skipped" ? [] : (asNumbers(response.answer) ?? []);
  return (
    <>
      <QuestionLine number={number} text={question.text} />
      <OutcomeLine outcome={outcome}>
        {outcome === "skipped"
          ? `Skipped. The answer is ${letters(key)}.`
          : outcome === "right"
            ? `Correct! You chose ${letters(given)}.`
            : `Not quite: you chose ${letters(given)}; the answer is ${letters(key)}.`}
      </OutcomeLine>
      <ul className="mt-1 space-y-1">
        {(question.options ?? []).map((option, index) => {
          const mark = optionMark(given.includes(index), key.includes(index), outcome === "skipped");
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: options are positions in the question
            <li key={index} className="flex items-start gap-1.5">
              {mark ? <OutcomeIcon outcome={mark.outcome} /> : <span aria-hidden className="h-4 w-4 shrink-0" />}
              <span className="min-w-0 flex-1 break-words">
                <span className="mr-1 font-semibold">{LETTERS[index]}.</span>
                <InlineMarkdown text={option} />
                {mark ? <span className="text-xs font-medium text-text-secondary"> ({mark.word})</span> : null}
              </span>
            </li>
          );
        })}
      </ul>
      <Why text={question.explanation} open={open} />
    </>
  );
}

const multi: KindModule<number[]> = {
  Input: MultiInput,
  Result: MultiResult,
  empty: () => [],
  isComplete: (_question, value) => value.length > 0,
  toRequest: (_question, value) => [...new Set(value)].sort((a, b) => a - b),
  fromRecord: (question) => asNumbers(question.draft) ?? asNumbers(question.response?.answer),
};

const pack: KindPack = { mcq: single, multi };

export default pack;
