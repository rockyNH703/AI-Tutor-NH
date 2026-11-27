"use client";

import { cn } from "@v1/ui/utils";
import { type RefObject, useId } from "react";

import { PALETTE_LABEL, type PaletteState } from "@/features/friendzone/exam-model";
import { StateGlyph } from "@/features/friendzone/exam-palette";
import type { ExamRules, PaperQuestion } from "@/lib/api/practice-exams";
import { rulesText } from "./practice-model";

/**
 * One question of a practice exam: its number and the scoring rule, the stem, and native inputs (radios for one
 * answer, checkboxes for several). The heading takes focus when the student moves to the question.
 */
export function ExamQuestion({
  question,
  total,
  rules,
  keys,
  state,
  unsaved,
  locked,
  headingRef,
  onChoose,
}: {
  question: PaperQuestion;
  total: number;
  rules: ExamRules;
  keys: readonly string[];
  state: PaletteState;
  unsaved: boolean;
  locked: boolean;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onChoose: (key: string) => void;
}) {
  const id = useId();
  const number = question.index + 1;
  const shown = state === "not-visited" ? "not-answered" : state;
  const last = question.options[question.options.length - 1]?.key ?? "D";
  return (
    <div className="rounded-2xl border border-border bg-card p-4 sm:p-6">
      <div className="flex flex-wrap items-center gap-2">
        <h2 ref={headingRef} tabIndex={-1} className="focus-ring rounded-md text-lg font-semibold text-foreground">
          Question {number} of {total}
          <span className="text-sm font-normal text-text-secondary"> · {rulesText(rules)}</span>
        </h2>
        <span className="flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2 py-0.5 text-xs font-medium text-foreground">
          <StateGlyph state={shown} unsaved={unsaved} className="h-3 w-3 shrink-0" />
          {PALETTE_LABEL[shown]}
          {unsaved && " · Not saved"}
        </span>
      </div>
      <p className="mt-1 text-xs text-text-secondary">
        Quiz: <bdi dir="auto">{question.quiz}</bdi>
      </p>
      <p id={`${id}-stem`} dir="auto" className="mt-4 whitespace-pre-wrap break-words text-base text-foreground">
        {question.stem}
      </p>
      <fieldset className="mt-4" aria-describedby={`${id}-stem ${id}-help`}>
        <legend className="sr-only">
          {question.multi ? "Choose all that apply" : "Choose one answer"} for question {number}
        </legend>
        <div className="flex flex-col gap-2">
          {question.options.map((option, index) => {
            const chosen = keys.includes(option.key);
            return (
              <label
                key={option.key}
                className={cn(
                  "flex min-h-11 items-start gap-3 rounded-xl border px-4 py-3 text-sm text-foreground transition-colors duration-fast ease-out",
                  chosen ? "border-brand bg-brand-soft" : "border-border bg-card",
                  locked ? "cursor-default" : "cursor-pointer hover:border-brand/50",
                )}
              >
                <input
                  type={question.multi ? "checkbox" : "radio"}
                  name={`${id}-options`}
                  value={option.key}
                  checked={chosen}
                  disabled={locked}
                  aria-keyshortcuts={`${index + 1} ${option.key}`}
                  onChange={() => onChoose(option.key)}
                  className="focus-ring mt-1 h-4 w-4 shrink-0 accent-brand"
                />
                <span className="shrink-0 font-semibold">{option.key}.</span>
                <span dir="auto" className="min-w-0 flex-1 break-words">
                  {option.text}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <p id={`${id}-help`} className="mt-3 text-xs text-text-secondary">
        {question.multi ? "More than one answer may be right: choose all that apply. " : ""}
        Keys 1–{question.options.length} or A–{last} {question.multi ? "tick or untick" : "choose"} an option. Clear removes the answer.
      </p>
    </div>
  );
}
