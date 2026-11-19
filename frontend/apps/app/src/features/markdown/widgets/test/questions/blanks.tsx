"use client";

import { type Ref, useId } from "react";

import type { AssessmentQuestion } from "@/lib/api/types";
import { BlankText } from "../../fill-blank";
import type { KindPack } from "../kinds";
import type { InputProps, KindModule, ResultProps } from "../question-props";
import { FieldError, OutcomeLine, Why, asTexts, ids, outcomeOf } from "./choice";

/** A fill-in question inside a ```quiz: the exercise card's sentence and blanks (widgets/fill-blank.tsx). */
function BlanksInput({ question, value, onChange, onCommit, disabled, describedBy, invalid, inputRef }: InputProps<string[]>) {
  const error = useId();
  return (
    <div>
      <BlankText
        segments={question.segments ?? []}
        blanks={question.blanks ?? 0}
        values={value}
        onChange={(index, text) => onChange(Object.assign([...value], { [index]: text }))}
        onBlur={onCommit}
        disabled={disabled}
        describedBy={ids(invalid && error, describedBy)}
        invalid={Boolean(invalid)}
        inputRef={inputRef as Ref<HTMLInputElement>}
      />
      <FieldError id={error} text={invalid} />
    </div>
  );
}

function BlanksResult({ question, number, open }: ResultProps) {
  const response = question.response;
  if (!response) return null;
  const blanks = question.blanks ?? 0;
  const outcome = outcomeOf(response);
  return (
    <>
      <BlankText
        lead={number !== null ? <span className="mr-1 font-medium text-text-secondary">Q{number}.</span> : null}
        segments={question.segments ?? []}
        blanks={blanks}
        values={asTexts(response.value) ?? []}
        disabled
        graded={{ marks: response.marks, answers: question.answers }}
      />
      <OutcomeLine outcome={outcome}>
        {outcome === "skipped" ? "Skipped. The answers are shown in the blanks." : `${response.earned} of ${blanks} right.`}
      </OutcomeLine>
      <Why text={question.explanation} open={open} />
    </>
  );
}

/** One text per blank, "" where nothing was typed (the server grades every blank). */
function filled(question: AssessmentQuestion, value: readonly string[]): string[] {
  return Array.from({ length: question.blanks ?? 0 }, (_, index) => value[index] ?? "");
}

const fillBlank: KindModule<string[]> = {
  Input: BlanksInput,
  Result: BlanksResult,
  empty: (question) => filled(question, []),
  isComplete: (_question, value) => value.some((text) => text?.trim()),
  toRequest: filled,
  fromRecord: (question) => asTexts(question.draft) ?? asTexts(question.response?.value),
};

const pack: KindPack = { fill_blank: fillBlank };

export default pack;
