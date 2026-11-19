"use client";

import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { CircleCheck, CircleX, PencilLine } from "lucide-react";
import { Fragment, type ReactNode, type Ref, useState } from "react";

import type { Assessment } from "@/lib/api/types";
import type { TestState } from "../context";
import { InlineMarkdown } from "../message-content";
import type { WidgetProps } from "../rich-block";
import { CardFrame } from "../study-card";
// From record.tsx, not quiz.tsx: the quiz's blanks kind imports BlankText from here (no import cycle).
import { Preparing, failure, useRecord } from "./test/record";

/**
 * ```fill-blank: an exercise the server keeps (app/assessments). The student types into the blanks
 * and checks once; the server grades each blank (ignoring case, spacing and a trailing full stop),
 * records it, and tells the tutor. The card draws only the record: the text and its blanks, then
 * what was typed, each blank right or wrong with its answer, the score and the explanation. A reload
 * shows the same; nothing is graded or kept here.
 */
export default function FillBlank({ source, onError }: WidgetProps) {
  const { tests, record, waiting } = useRecord(source, onError, "exercise");
  if (waiting) return <Preparing noun="exercise" />;
  if (!record || !tests) return null;
  return <Exercise record={record} tests={tests} />;
}

function Exercise({ record, tests }: { record: Assessment; tests: TestState }) {
  const exercise = record.questions[0];
  const segments = exercise?.segments ?? [];
  const blanks = exercise?.blanks ?? 0;
  const response = exercise?.response ?? null;
  const [given, setGiven] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const done = response !== null;
  const typed = Array.isArray(response?.value) ? response.value : given;
  const open = !done && record.answerable;

  const send = async (action: "answer" | "skip") => {
    if (!open || saving) return;
    setSaving(true);
    setProblem(null);
    try {
      await tests.respond(record.position, {
        hash: record.hash,
        question: 0,
        action,
        ...(action === "answer" ? { value: Array.from({ length: blanks }, (_, index) => given[index] ?? "") } : {}),
      });
    } catch (error) {
      setProblem(failure(error, "exercise"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <CardFrame kind="fill-blank" icon={PencilLine} label="Fill in the blanks">
      <BlankText
        segments={segments}
        blanks={blanks}
        values={typed}
        disabled={!open || saving}
        onChange={(index, value) => setGiven((all) => Object.assign([...all], { [index]: value }))}
        graded={done ? { marks: response?.marks, answers: exercise?.answers } : undefined}
      />
      {open ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            disabled={saving || !given.some((value) => value?.trim())}
            onClick={() => void send("answer")}
          >
            {saving ? "Saving…" : "Check answers"}
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={saving} onClick={() => void send("skip")}>
            Show answers
          </Button>
        </div>
      ) : !done ? (
        <p className="mt-2 text-xs text-text-secondary">This exercise can no longer be answered.</p>
      ) : null}
      <p role="status" className={cn("text-sm", done || problem ? "mt-2" : "sr-only", problem && "text-destructive")}>
        {problem ??
          (done
            ? response.status === "skipped"
              ? "Answers shown."
              : `${response.earned} of ${blanks} right.`
            : "")}
      </p>
      {done && exercise?.explanation ? (
        <p className="mt-2 text-sm leading-6">
          <InlineMarkdown text={exercise.explanation} />
        </p>
      ) : null}
    </CardFrame>
  );
}

export type BlankTextProps = {
  /** The text around the blanks (one more than the blanks). */
  segments: readonly string[];
  blanks: number;
  /** What is typed into each blank. */
  values: readonly string[];
  onChange?: (index: number, value: string) => void;
  disabled: boolean;
  /** Once graded: each blank right or wrong (no marks when skipped), a wrong one shown with its answer. */
  graded?: { marks?: readonly boolean[] | null; answers?: readonly (readonly string[])[] | null };
  /** Drawn before the text, inside the same paragraph ("Q3."). */
  lead?: ReactNode;
  describedBy?: string;
  invalid?: boolean;
  /** The first blank, for focus. */
  inputRef?: Ref<HTMLInputElement>;
  onBlur?: () => void;
};

/**
 * The sentence with an input in each blank, shared by the ```fill-blank exercise and a fill-in question
 * in a ```quiz (test/questions/blanks.tsx): typed while open, then each blank right or wrong.
 */
export function BlankText({
  segments,
  blanks,
  values,
  onChange,
  disabled,
  graded,
  lead,
  describedBy,
  invalid,
  inputRef,
  onBlur,
}: BlankTextProps) {
  return (
    <p className="leading-9">
      {lead}
      {segments.map((segment, index) => {
        const value = values[index] ?? "";
        const mark = graded?.marks?.[index];
        const answer = graded?.answers?.[index]?.[0];
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: segments are positions in the sentence
          <Fragment key={index}>
            <InlineMarkdown text={segment} />
            {index < blanks && (
              <span className="mx-1 inline-flex max-w-full items-center gap-1 align-baseline">
                <input
                  type="text"
                  aria-label={`Blank ${index + 1}`}
                  ref={index === 0 ? inputRef : undefined}
                  value={value}
                  disabled={disabled}
                  maxLength={200}
                  onChange={(event) => onChange?.(index, event.target.value)}
                  onBlur={onBlur}
                  aria-describedby={describedBy}
                  aria-invalid={invalid || undefined}
                  style={{ width: `${Math.min(16, Math.max(6, value.length)) + 2}ch` }}
                  className={cn(
                    "blank-field max-w-full rounded-md border bg-card px-1.5 py-0.5 text-center text-sm leading-6 focus-ring",
                    graded ? (mark ? "border-success" : "border-destructive") : invalid ? "border-destructive" : "border-input",
                  )}
                />
                {graded &&
                  (mark ? (
                    <CircleCheck aria-label="correct" className="h-4 w-4 shrink-0 text-success" />
                  ) : (
                    <span className="inline-flex items-center gap-0.5 text-xs text-destructive">
                      <CircleX aria-hidden className="h-4 w-4 shrink-0" />
                      <span aria-label={`the answer is ${answer ?? ""}`}>{answer}</span>
                    </span>
                  ))}
              </span>
            )}
          </Fragment>
        );
      })}
    </p>
  );
}
