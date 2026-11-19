"use client";

import { Input } from "@v1/ui/input";
import { type ReactNode, type Ref, useId } from "react";

import { shouldSubmit } from "@/features/chat/keyboard";
import { InlineMarkdown } from "../../../message-content";
import type { KindPack } from "../kinds";
import type { InputProps, KindModule, ResultProps } from "../question-props";
import { FieldError, OutcomeLine, QuestionLine, Why, asText, ids, outcomeOf } from "./choice";

/** The fixed format hint: the same for every question, so it never hints at an answer. */
export const NUMERIC_HINT = "Digits, with an optional leading minus and a full stop for decimals (for example -12.5)";

// The server's grammar (app/assessments/rules.py NUMBER): ASCII digits, at most 30 before the point and 9 after.
const NUMBER = /^-?[0-9]{1,30}(\.[0-9]{1,9})?$/;
const TOO_LONG = /^-?[0-9]+(\.[0-9]+)?$/;

/** Why a typed number would be refused, or null when it is fine (or empty: nothing to say yet). */
export function numberProblem(typed: string): string | null {
  const text = typed.trim();
  if (!text || NUMBER.test(text)) return null;
  if (text.includes(",")) return "Use a full stop for decimals, not a comma.";
  if (TOO_LONG.test(text)) return "Use at most 30 digits before the full stop and 9 after it.";
  return `Enter a number. ${NUMERIC_HINT}.`;
}

function NumericInput({
  question,
  value,
  onChange,
  onCommit,
  onSubmit,
  disabled,
  tries,
  labelledBy,
  describedBy,
  invalid,
  inputRef,
}: InputProps<string>) {
  const id = useId();
  const unit = question.unit ? `${id}-unit` : null;
  const tried = tries.length > 0 ? `${id}-tries` : null;
  const check = () => {
    onCommit?.();
    onSubmit?.();
  };
  return (
    <form
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        check();
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        {/* No inputMode: the iPhone's decimal pad has no minus key, so a negative answer couldn't be typed. */}
        <Input
          type="text"
          ref={inputRef as Ref<HTMLInputElement>}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onBlur={onCommit}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault(); // Enter while an input method is composing confirms it, never checks
            if (shouldSubmit(event)) check();
          }}
          disabled={disabled}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          aria-labelledby={labelledBy}
          aria-describedby={ids(invalid && `${id}-error`, unit, `${id}-hint`, tried, describedBy)}
          aria-invalid={invalid ? true : undefined}
          className="w-48 max-w-full font-mono tabular-nums"
        />
        {question.unit && unit ? (
          <span id={unit} className="text-sm">
            <InlineMarkdown text={question.unit} />
          </span>
        ) : null}
      </div>
      <p id={`${id}-hint`} className="mt-1.5 text-xs text-text-secondary">
        {NUMERIC_HINT}
      </p>
      {tried ? (
        <p id={tried} className="mt-1 text-xs text-text-secondary">
          Tried so far: {tries.join(", ")}
        </p>
      ) : null}
      <FieldError id={`${id}-error`} text={invalid} />
    </form>
  );
}

/** "12.5 ± 0.5 ms", or "12.5 ms ± 5%" for a share; just "12.5 ms" when it must be exact. */
function Accepted({ value, tolerance, unit }: { value: string; tolerance: string | null | undefined; unit: ReactNode }) {
  const spread = tolerance && tolerance !== "0" ? ` ± ${tolerance}` : "";
  return tolerance?.endsWith("%") ? (
    <>
      {value}
      {unit}
      {spread}
    </>
  ) : (
    <>
      {value}
      {spread}
      {unit}
    </>
  );
}

function NumericResult({ question, number, open }: ResultProps) {
  const response = question.response;
  const accepted = question.accepted?.[0];
  if (!response || accepted === undefined) return null;
  const outcome = outcomeOf(response);
  const unit = question.unit ? (
    <>
      {" "}
      <InlineMarkdown text={question.unit} />
    </>
  ) : null;
  const answer = <Accepted value={accepted} tolerance={question.tolerance} unit={unit} />;
  const given = (
    <>
      {asText(response.answer)}
      {unit}
    </>
  );
  return (
    <>
      <QuestionLine number={number} text={question.text} />
      <OutcomeLine outcome={outcome}>
        {outcome === "skipped" ? (
          <>Skipped. The accepted answer is {answer}.</>
        ) : outcome === "right" ? (
          <>
            Correct! You gave {given}; the accepted answer is {answer}.
          </>
        ) : (
          <>
            Not quite: you gave {given}; the accepted answer is {answer}.
          </>
        )}
      </OutcomeLine>
      {response.tries?.length ? (
        <p className="mt-1 text-xs text-text-secondary">Earlier tries: {response.tries.join(", ")}</p>
      ) : null}
      <Why text={question.explanation} open={open} />
    </>
  );
}

const numeric: KindModule<string> = {
  Input: NumericInput,
  Result: NumericResult,
  empty: () => "",
  isComplete: (_question, value) => value.trim() !== "",
  check: (_question, value) => numberProblem(value),
  toRequest: (_question, value) => value.trim(),
  fromRecord: (question) => asText(question.draft) ?? asText(question.response?.answer),
};

const pack: KindPack = { numeric };

export default pack;
