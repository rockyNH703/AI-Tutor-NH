"use client";

import { Input } from "@v1/ui/input";
import { type Ref, useId } from "react";

import { shouldSubmit } from "@/features/chat/keyboard";
import type { KindPack } from "../kinds";
import type { InputProps, KindModule, ResultProps } from "../question-props";
import { FieldError, ids } from "./choice";
import { Outcome } from "./listing";

const CODE = "rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.8125rem] break-all";

/**
 * A command typed at the author's prompt. The prompt is drawn before the field but hidden from screen
 * readers there, and read with the field instead ("Prompt: R1(config-if)#"). Enter checks the answer
 * (never while an input method is composing); the browser's own submit on Enter is always stopped, so
 * a composing Enter can't send half a word.
 */
function CliInput({
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
  const promptId = useId();
  const triesId = useId();
  const errorId = useId();
  return (
    <form
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit?.();
      }}
      className="space-y-1.5"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        {question.prompt ? (
          <span aria-hidden className="break-all font-mono text-sm text-text-secondary">
            {question.prompt}
          </span>
        ) : null}
        <Input
          ref={inputRef as Ref<HTMLInputElement> | undefined}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onBlur={onCommit}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            if (!shouldSubmit(event)) return;
            onCommit?.();
            onSubmit?.();
          }}
          disabled={disabled}
          maxLength={300}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          aria-labelledby={labelledBy}
          aria-describedby={ids(question.prompt && promptId, tries.length > 0 && triesId, describedBy, invalid && errorId)}
          aria-invalid={invalid ? true : undefined}
          className="min-w-0 flex-1 basis-48 font-mono"
        />
      </div>
      {question.prompt ? (
        <span id={promptId} className="sr-only">
          Prompt: {question.prompt}
        </span>
      ) : null}
      {tries.length > 0 ? (
        <p id={triesId} className="flex flex-wrap items-baseline gap-1.5 text-xs text-text-secondary">
          <span>Tried:</span>
          {tries.map((tried, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: tries are positions in the history
            <code key={index} className={CODE}>
              {tried}
            </code>
          ))}
        </p>
      ) : null}
      <FieldError id={errorId} text={invalid} />
    </form>
  );
}

/** What was typed, every accepted form, the earlier tries, and what the device printed (a console block). */
function CliResult({ question, number, open }: ResultProps) {
  const response = question.response;
  const typed = typeof response?.answer === "string" ? response.answer : "";
  const accepted = question.accepted ?? [];
  const prompt = question.prompt ? `${question.prompt} ` : "";
  return (
    <Outcome
      question={question}
      number={number}
      open={open}
      given={
        <>
          You typed <code className={CODE}>{typed}</code>.
        </>
      }
      right={null}
    >
      {response?.tries?.length ? (
        <p className="mt-1 flex flex-wrap items-baseline gap-1.5 text-sm text-text-secondary">
          <span>Earlier tries:</span>
          {response.tries.map((tried, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: tries are positions in the history
            <code key={index} className={CODE}>
              {tried}
            </code>
          ))}
        </p>
      ) : null}
      {accepted.length > 0 ? (
        <div className="mt-1 text-sm">
          <p>{accepted.length === 1 ? "Accepted:" : "Accepted forms:"}</p>
          <ul className="mt-0.5 flex flex-wrap gap-1.5">
            {accepted.map((form) => (
              <li key={form}>
                <code className={CODE}>{form}</code>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {question.output ? (
        <pre
          // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
          tabIndex={0}
          role="region"
          aria-label="Output"
          className="focus-ring mt-2 max-h-72 overflow-auto rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-[0.8125rem] leading-6"
        >
          {accepted[0] ? `${prompt}${accepted[0]}\n` : ""}
          {question.output}
        </pre>
      ) : null}
    </Outcome>
  );
}

export const cli: KindModule<string> = {
  Input: CliInput,
  Result: CliResult,
  empty: () => "",
  isComplete: (_question, value) => value.trim() !== "",
  toRequest: (_question, value) => value,
  fromRecord: (question) =>
    typeof question.draft === "string"
      ? question.draft
      : typeof question.response?.answer === "string"
        ? question.response.answer
        : null,
};

const pack: KindPack = { cli };

export default pack;
