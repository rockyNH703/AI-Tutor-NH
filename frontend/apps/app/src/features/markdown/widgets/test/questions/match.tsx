"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@v1/ui/select";
import { useId } from "react";

import type { AssessmentQuestion } from "@/lib/api/types";
import { InlineMarkdown } from "../../../message-content";
import type { KindPack } from "../kinds";
import type { InputProps, KindModule, ResultProps } from "../question-props";
import { FieldError, OutcomeIcon, QuestionLine, Why, ids } from "./choice";

/** One choice per left item: an index into `right` (shown order), or null while none is chosen. */
type Choices = (number | null)[];

/** A saved or final value, if it fits this question (anything else is ignored, never drawn). */
export function asChoices(raw: unknown, question: AssessmentQuestion): Choices | null {
  const count = question.left?.length ?? 0;
  const of = question.right?.length ?? 0;
  const fits =
    Array.isArray(raw) &&
    raw.length === count &&
    raw.every((choice) => choice === null || (Number.isInteger(choice) && choice >= 0 && choice < of));
  return fits ? raw : null;
}

/** Matching: one select per left item, named by that item (no drag: the plan's choice). */
function MatchInput({ question, value, onChange, disabled, describedBy, invalid }: InputProps<Choices>) {
  const id = useId();
  const left = question.left ?? [];
  const described = ids(describedBy, invalid && `${id}-error`);
  const choose = (index: number, choice: string) =>
    onChange(left.map((_, at) => (at === index ? Number(choice) : (value[at] ?? null))));
  return (
    <>
      <ul className="space-y-3">
        {left.map((term, index) => {
          const chosen = value[index];
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: left items are positions in the question
            <li key={index} className="grid gap-1.5 sm:grid-cols-2 sm:items-center sm:gap-3">
              <span id={`${id}-${index}`} className="min-w-0 break-words text-sm font-medium leading-6">
                <InlineMarkdown text={term} />
              </span>
              <Select
                value={chosen === null || chosen === undefined ? "" : String(chosen)}
                onValueChange={(choice) => choose(index, choice)}
                disabled={disabled}
              >
                <SelectTrigger
                  aria-labelledby={`${id}-${index}`}
                  aria-describedby={described}
                  aria-invalid={invalid ? true : undefined}
                  className="h-auto min-h-11 whitespace-normal py-2 text-left [&>span]:line-clamp-none sm:min-h-10"
                >
                  <SelectValue placeholder="Choose a match" />
                </SelectTrigger>
                {/* Long options wrap inside the screen instead of widening the list past it. */}
                <SelectContent className="max-w-[var(--radix-select-content-available-width)]">
                  {(question.right ?? []).map((option, choice) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: options are positions in the question
                    <SelectItem key={choice} value={String(choice)} className="break-words">
                      <InlineMarkdown text={option} />
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </li>
          );
        })}
      </ul>
      <FieldError id={`${id}-error`} text={invalid} />
    </>
  );
}

/** Wrapping rows "OSPF → Link-state IGP · correct"; a skipped question lists the right pairs. */
function MatchResult({ question, number, open }: ResultProps) {
  const response = question.response;
  const key = question.correctList;
  if (!response || !key) return null;
  const right = question.right ?? [];
  const given = response.status === "answered" ? asChoices(response.answer, question) : null;
  return (
    <>
      <QuestionLine number={number} text={question.text} />
      <p className="mt-1">
        {given ? `${response.earned} of ${response.possible} pairs right.` : "Skipped. The pairs:"}
      </p>
      <ul className="mt-1 space-y-1">
        {(question.left ?? []).map((term, index) => {
          const answer = right[key[index] ?? -1] ?? "";
          const choice = given ? given[index] : key[index];
          const shown = choice === null || choice === undefined ? null : (right[choice] ?? null);
          const ok = given ? (response.marks?.[index] ?? choice === key[index]) : null;
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: left items are positions in the question
            <li key={index} className="flex items-start gap-1.5">
              {ok === null ? null : <OutcomeIcon outcome={ok ? "right" : "wrong"} />}
              <span className="min-w-0 flex-1 break-words">
                <InlineMarkdown text={term} /> → {shown === null ? "no choice" : <InlineMarkdown text={shown} />}
                {ok === null ? null : ok ? (
                  " · correct"
                ) : (
                  <>
                    {" · wrong, should be "}
                    <InlineMarkdown text={answer} />
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      <Why text={question.explanation} open={open} />
    </>
  );
}

const match: KindModule<Choices> = {
  Input: MatchInput,
  Result: MatchResult,
  empty: (question) => (question.left ?? []).map(() => null),
  isComplete: (question, value) => (question.left ?? []).every((_, index) => typeof value[index] === "number"),
  toRequest: (_question, value) => value,
  fromRecord: (question) => asChoices(question.draft, question) ?? asChoices(question.response?.answer, question),
};

const pack: KindPack = { match };

export default pack;
