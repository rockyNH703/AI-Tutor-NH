"use client";

import { Button } from "@v1/ui/button";
import { Input } from "@v1/ui/input";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { X } from "lucide-react";
import { type KeyboardEvent, useId, useState } from "react";

import { describeError } from "@/lib/api/errors";
import {
  EXAM_DATE_MAX_DAYS,
  GOAL_MAX,
  type LearnerProfileField,
  MAIN_GOALS,
  type MainGoal,
  TARGET_EXAM_MAX,
  TOPICS_MAX,
  TOPIC_MAX,
} from "@/lib/api/types";
import { MAIN_GOAL_LABELS } from "./learning-labels";
import { addTopic, wholeNumber } from "./learning-model";
import { Disclosure, Field, Notice, SectionHeading, SettingsCard } from "./primitives";
import { SaveFooter, selectClass, useProfileForm } from "./profile-form";

/**
 * The learner profile's goal fields. The time goals (daily and weekly minutes, preferred time) are
 * in Practice & Lab Time (subjects-section.tsx), a form of their own over the same profile.
 */
const FIELDS = [
  "mainGoal",
  "targetExam",
  "examDate",
  "targetScorePercent",
  "weakTopics",
  "shortTermGoal",
  "longTermGoal",
] as const satisfies readonly LearnerProfileField[];

/** "YYYY-MM-DD" of the day `offset` days from today, on this device's calendar. */
function localDay(offset = 0): string {
  const date = new Date();
  date.setDate(date.getDate() + offset);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** "YYYY-MM-DD" of the day `offset` days from today in UTC, the calendar the API checks an exam date on. */
function utcDay(offset: number): string {
  return new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
}

/** A control still loading, at its own size: the card keeps its final layout when the profile arrives. */
function Pending({ className }: { className: string }) {
  return <Skeleton className={`rounded-xl ${className}`} />;
}

/** A goal's box, and its placeholder while loading: two lines (16px text on phones, so iOS doesn't zoom). */
const GOAL_BOX = "h-[4.125rem] w-full sm:h-[3.625rem] sm:max-w-[28rem]";

/** Two short fields side by side on phones; from lg the wrapper drops out and they are rows like the others. */
const PAIR = "flex flex-col gap-5 min-[360px]:grid min-[360px]:grid-cols-2 min-[360px]:items-end min-[360px]:gap-3 lg:contents";

function TopicsEditor({
  topics,
  onChange,
  invalid,
}: {
  /** null while the profile loads. */
  topics: string[] | null;
  onChange: (topics: string[]) => void;
  invalid: boolean;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const listId = useId();

  const add = () => {
    if (!topics) return;
    const result = addTopic(topics, text, { max: TOPICS_MAX, maxLength: TOPIC_MAX });
    setError(result.error);
    if (!result.error) {
      onChange(result.topics);
      setText("");
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault(); // Enter adds the topic; it does not submit the whole form
      add();
    }
  };

  return (
    // From lg the chips sit under the input: the same two columns as a Field row.
    <div className="flex flex-col gap-2 lg:grid lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:gap-x-8">
      <div className="lg:col-span-2">
        <Field
          label="Topics you find difficult"
          hint={`Up to ${TOPICS_MAX}.`}
          error={error ?? (invalid ? "Check these topics." : null)}
        >
          {(props) =>
            topics ? (
              <div className="flex w-full gap-2 sm:max-w-[28rem]">
                <Input
                  {...props}
                  value={text}
                  maxLength={TOPIC_MAX}
                  placeholder="e.g. BGP path selection"
                  onChange={(event) => setText(event.target.value)}
                  onKeyDown={onKeyDown}
                  aria-controls={listId}
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-11 sm:h-10"
                  onClick={add}
                  disabled={!text.trim() || topics.length >= TOPICS_MAX}
                >
                  Add
                </Button>
              </div>
            ) : (
              <Pending className="h-11 w-full sm:h-10 sm:max-w-[28rem]" />
            )
          }
        </Field>
      </div>
      {topics && (
        <ul id={listId} aria-label="Your difficult topics" className="flex flex-wrap gap-2 empty:hidden lg:col-start-2">
          {topics.map((topic) => (
            <li
              key={topic}
              className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface-2 py-1 pl-3 pr-1 text-sm text-foreground [@media(pointer:coarse)]:py-0 [@media(pointer:coarse)]:pr-0"
            >
              {topic}
              <button
                type="button"
                onClick={() => onChange(topics.filter((item) => item !== topic))}
                aria-label={`Remove ${topic}`}
                className="focus-ring grid h-6 w-6 place-items-center rounded-md text-text-secondary hover:bg-border hover:text-foreground [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11"
              >
                <X aria-hidden className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function GoalsSection() {
  const form = useProfileForm(FIELDS, "Goals saved.");
  const draft = form.draft;
  const check = (field: LearnerProfileField) => (form.invalid(field) ? "Check this value." : null);
  const today = localDay();
  const goalBox = (field: "shortTermGoal" | "longTermGoal", label: string) => (
    <Field label={label} error={check(field)}>
      {(props) =>
        draft ? (
          <textarea
            {...props}
            rows={2}
            maxLength={GOAL_MAX}
            value={draft[field] ?? ""}
            onChange={(event) => form.change({ [field]: event.target.value })}
            className={`focus-field rounded-xl border border-input bg-card px-3 py-2 text-base sm:text-sm ${GOAL_BOX}`}
          />
        ) : (
          <Pending className={GOAL_BOX} />
        )
      }
    </Field>
  );

  // One tree loading and loaded: only the controls are placeholders, so nothing moves when they arrive.
  const goals = (
    <form onSubmit={form.submit} className="flex flex-col gap-4">
      {form.notice && <Notice tone="warning">{form.notice}</Notice>}
      <SettingsCard title="Your goals" footer={<SaveFooter dirty={form.dirty} pending={form.pending} onCancel={form.reset} />}>
        <Field label="Main goal" error={check("mainGoal")}>
          {(props) =>
            draft ? (
              <select
                {...props}
                value={draft.mainGoal ?? ""}
                onChange={(event) => form.change({ mainGoal: (event.target.value || null) as MainGoal | null })}
                className={selectClass}
              >
                <option value="">Not set</option>
                {MAIN_GOALS.map((goal) => (
                  <option key={goal} value={goal}>
                    {MAIN_GOAL_LABELS[goal]}
                  </option>
                ))}
              </select>
            ) : (
              <Pending className="h-10 w-full sm:max-w-[20rem]" />
            )
          }
        </Field>
        <Field label="Certification or exam you're preparing for" error={check("targetExam")}>
          {(props) =>
            draft ? (
              <Input
                {...props}
                className="w-full sm:max-w-[20rem]"
                value={draft.targetExam ?? ""}
                maxLength={TARGET_EXAM_MAX}
                placeholder="e.g. CCNA 200-301"
                onChange={(event) => form.change({ targetExam: event.target.value })}
              />
            ) : (
              <Pending className="h-11 w-full sm:h-10 sm:max-w-[20rem]" />
            )
          }
        </Field>
        <div className={PAIR}>
          <Field label="Exam date" error={check("examDate")}>
            {(props) =>
              draft ? (
                <Input
                  {...props}
                  className="w-full sm:w-44"
                  type="date"
                  // A new date is from today; one that has passed is left as it is, so saving the
                  // other goals never stops at it. The last day is the API's (UTC), which this
                  // device's calendar may already be a day past.
                  min={draft.examDate && draft.examDate < today ? undefined : today}
                  max={utcDay(EXAM_DATE_MAX_DAYS)}
                  value={draft.examDate ?? ""}
                  onChange={(event) => form.change({ examDate: event.target.value || null })}
                />
              ) : (
                <Pending className="h-11 w-full sm:h-10 sm:w-44" />
              )
            }
          </Field>
          <Field label="Target score (%)" error={check("targetScorePercent")}>
            {(props) =>
              draft ? (
                <Input
                  {...props}
                  className="w-full sm:w-32"
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={100}
                  value={draft.targetScorePercent ?? ""}
                  onChange={(event) => form.change({ targetScorePercent: wholeNumber(event.target.value, 100) })}
                />
              ) : (
                <Pending className="h-11 w-full sm:h-10 sm:w-32" />
              )
            }
          </Field>
        </div>
        {goalBox("shortTermGoal", "Short-term goal")}
        {goalBox("longTermGoal", "Long-term goal")}
        <TopicsEditor
          topics={draft ? (draft.weakTopics ?? []) : null}
          onChange={(weakTopics) => form.change({ weakTopics })}
          invalid={form.invalid("weakTopics")}
        />
        <Disclosure label="About these goals">
          <p className="max-w-prose text-sm leading-relaxed text-text-secondary">
            As the exam date nears, your practice plan turns to review. The target score is your aim in the exam or in
            practice tests. A difficult topic is a protocol, a feature or a skill you want to get right. Goals are kept on
            one line, up to {GOAL_MAX} characters each.
          </p>
        </Disclosure>
      </SettingsCard>
    </form>
  );

  return (
    <>
      <SectionHeading
        title="Skills & Certification Goals"
        description="My Learning's practice plan counts down to your exam date."
      />
      {form.loadError ? (
        <SettingsCard title="Couldn't load your goals">
          <p className="text-sm text-text-secondary">{describeError(form.loadError).message}</p>
          <button
            type="button"
            onClick={form.retry}
            className="focus-ring self-start rounded text-sm font-medium text-brand underline underline-offset-2"
          >
            Try again
          </button>
        </SettingsCard>
      ) : draft ? (
        goals
      ) : (
        <SkeletonGroup label="Loading your goals">{goals}</SkeletonGroup>
      )}
    </>
  );
}
