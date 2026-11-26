"use client";

import { Button } from "@v1/ui/button";
import { Input } from "@v1/ui/input";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { ArrowDown, ArrowUp, RotateCcw, X } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { InlineAction } from "@/components/inline-action";
import { ApiError, describeError } from "@/lib/api/errors";
import { getSubjects, replaceSubjects } from "@/lib/api/learning";
import {
  DAILY_MINUTES_MAX,
  type LearnerProfileField,
  SUBJECTS_MAX,
  type SubjectItem,
  type SubjectList,
  WEEKLY_MINUTES_MAX,
} from "@/lib/api/types";
import { catalogOptions, useCatalog } from "./learning-data";
import { addSubject, moveSubject, removeSubject, toggleSubject, wholeNumber } from "./learning-model";
import { Field, Notice, SectionHeading, SettingsCard } from "./primitives";
import { SaveFooter, selectClass, useProfileForm } from "./profile-form";
import { useUnsavedChanges } from "./use-unsaved-changes";

/** The learner profile's time goals: Practice & Lab Time's own form (the other goals are in goals-section.tsx). */
const TIME_FIELDS = [
  "dailyActiveMinutesGoal",
  "weeklyActiveMinutesGoal",
  "preferredStudyTime",
] as const satisfies readonly LearnerProfileField[];

/** A control still loading, at its own size: the card keeps its final layout when the profile arrives. */
function Pending({ className }: { className: string }) {
  return <Skeleton className={`rounded-xl ${className}`} />;
}

/** Two short fields side by side on phones; from lg the wrapper drops out and they are rows like the others. */
const PAIR = "flex flex-col gap-5 min-[360px]:grid min-[360px]:grid-cols-2 min-[360px]:items-end min-[360px]:gap-3 lg:contents";

/**
 * Settings > Practice & Lab Time (slug `subjects`, its address since it was Subjects & Study): the
 * daily and weekly practice goals and the preferred practice time, which Practice & Goals, Lab
 * Activity and the daily reminder use. What the user works on is the Engineering Profile's focus
 * areas and platforms now, so this section points there instead of showing the subject list
 * (SubjectListEditor, kept below: the stored list is unchanged and still read by the API).
 */
export function SubjectsSection() {
  const form = useProfileForm(TIME_FIELDS, "Practice goals saved.");
  const draft = form.draft;
  const check = (field: LearnerProfileField) => (form.invalid(field) ? "Check this value." : null);
  const minutes = (field: "dailyActiveMinutesGoal" | "weeklyActiveMinutesGoal", label: string, max: number) => (
    <Field label={label} error={check(field)}>
      {(props) =>
        draft ? (
          <Input
            {...props}
            className="w-full sm:w-32"
            type="number"
            inputMode="numeric"
            min={0}
            max={max}
            value={draft[field] ?? ""}
            onChange={(event) => form.change({ [field]: wholeNumber(event.target.value, max) })}
          />
        ) : (
          <Pending className="h-11 w-full sm:h-10 sm:w-32" />
        )
      }
    </Field>
  );

  // One tree loading and loaded: only the controls are placeholders, so nothing moves when they arrive.
  const goals = (
    <form onSubmit={form.submit}>
      <SettingsCard title="Time goals" footer={<SaveFooter dirty={form.dirty} pending={form.pending} onCancel={form.reset} />}>
        {form.notice && <Notice tone="warning">{form.notice}</Notice>}
        <div className={PAIR}>
          {minutes("dailyActiveMinutesGoal", "Daily goal (minutes)", DAILY_MINUTES_MAX)}
          {minutes("weeklyActiveMinutesGoal", "Weekly goal (minutes)", WEEKLY_MINUTES_MAX)}
        </div>
        <Field
          label="Preferred practice time"
          hint="In your time zone. The daily reminder, if it's on, comes then."
          error={check("preferredStudyTime")}
        >
          {(props) =>
            draft ? (
              <Input
                {...props}
                className="w-full sm:w-40"
                type="time"
                step={60}
                value={draft.preferredStudyTime ?? ""}
                onChange={(event) => form.change({ preferredStudyTime: event.target.value.slice(0, 5) || null })}
              />
            ) : (
              <Pending className="h-11 w-full sm:h-10 sm:w-40" />
            )
          }
        </Field>
      </SettingsCard>
    </form>
  );

  return (
    <>
      <SectionHeading
        title="Practice & Lab Time"
        description="Your goals count active time: minutes you're using NetSec AI, not all your lab work. Practice & Goals tracks them."
      />
      <div className="flex flex-col gap-4">
        {form.loadError ? (
          <SettingsCard title="Couldn't load your practice goals">
            <p className="text-sm text-text-secondary">{describeError(form.loadError).message}</p>
            <InlineAction icon={RotateCcw} onClick={form.retry} className="-ml-2 mt-1">
              Try again
            </InlineAction>
          </SettingsCard>
        ) : draft ? (
          goals
        ) : (
          <SkeletonGroup label="Loading your practice goals">{goals}</SkeletonGroup>
        )}
        <p className="text-sm text-text-secondary">
          Your focus areas and platforms are in your{" "}
          <Link href="/settings/engineering" className="focus-ring rounded font-semibold text-brand underline underline-offset-2">
            Engineering Profile
          </Link>
          .
        </p>
      </div>
    </>
  );
}

type Focus = { code: string; action: "up" | "down" } | null;

/**
 * The stored subject list (GET/PUT /v1/learning/subjects), in the order chosen, each optionally
 * marked as a priority or as needing work. Edited as a whole list and saved as one versioned PUT.
 * Reordering works from the keyboard: focus stays on the moved entry's button.
 *
 * Superseded in Settings by the Engineering Profile's focus areas and platforms (SubjectsSection
 * links there) and not shown; kept, with the data it edits, rather than deleted.
 */
export function SubjectListEditor() {
  const { catalog, error: catalogError, retry: retryCatalog } = useCatalog();
  const [saved, setSaved] = useState<SubjectList | null>(null);
  const [items, setItems] = useState<SubjectItem[]>([]);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const [adding, setAdding] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [focus, setFocus] = useState<Focus>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const addId = useId();
  const dirty = saved !== null && JSON.stringify(items) !== JSON.stringify(saved.items);
  useUnsavedChanges(dirty);

  // One fetch per attempt.
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    getSubjects().then(
      (list) => {
        if (cancelled) return;
        setSaved(list);
        setItems(list.items);
      },
      (failure) => !cancelled && setLoadError(failure),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // After a move, the moved subject's button keeps the focus (React moved its row).
  useEffect(() => {
    if (!focus) return;
    const own = buttons.current.get(`${focus.code}:${focus.action}`);
    const other = buttons.current.get(`${focus.code}:${focus.action === "up" ? "down" : "up"}`);
    (own && !own.disabled ? own : other)?.focus();
    setFocus(null);
  }, [focus]);

  const nameOf = (code: string) => catalog?.subjects.find((entry) => entry.code === code);
  const available = catalog
    ? catalogOptions(catalog.subjects, null).filter((entry) => !items.some((item) => item.code === entry.code))
    : [];

  const move = (index: number, delta: -1 | 1, code: string) => {
    setItems((list) => moveSubject(list, index, delta));
    setFocus({ code, action: delta < 0 ? "up" : "down" });
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!saved || pending || !dirty) return;
    setPending(true);
    setNotice(null);
    try {
      const next = await replaceSubjects(saved.version, items);
      setSaved(next);
      setItems(next.items);
      toast.success("Subjects saved.");
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "version_conflict" && failure.current) {
        setSaved(failure.current as SubjectList);
        setNotice("Your subjects were just changed somewhere else. Your list is still here: check it and save again.");
      } else {
        toast.error(describeError(failure).message);
      }
    } finally {
      setPending(false);
    }
  };

  const error = catalogError ?? loadError;

  return (
    <>
      <SectionHeading
        title="Subjects & Study"
        description="The subjects you take, in the order you want them. The tutor pays extra attention to priorities and to subjects that need work."
      />
      {error ? (
        <SettingsCard title="Couldn't load your subjects">
          <p className="text-sm text-text-secondary">{describeError(error).message}</p>
          <InlineAction icon={RotateCcw} onClick={() => (catalogError ? retryCatalog() : setAttempt((count) => count + 1))} className="-ml-2 mt-1">
            Try again
          </InlineAction>
        </SettingsCard>
      ) : !catalog || !saved ? (
        <SkeletonGroup label="Loading your subjects">
          <Skeleton className="h-72 w-full rounded-2xl" />
        </SkeletonGroup>
      ) : (
        <form onSubmit={save}>
          <SettingsCard
            title="Your subjects"
            description={`Up to ${SUBJECTS_MAX}.`}
            footer={
              <>
                {dirty && (
                  <Button type="button" size="sm" variant="ghost" onClick={() => setItems(saved.items)} disabled={pending}>
                    Cancel
                  </Button>
                )}
                <Button type="submit" size="sm" disabled={pending || !dirty}>
                  {pending ? "Saving…" : "Save"}
                </Button>
              </>
            }
          >
            {notice && <Notice tone="warning">{notice}</Notice>}
            <div className="flex flex-col gap-1.5">
              <label htmlFor={addId} className="text-sm font-bold text-foreground">
                Add a subject
              </label>
              <div className="flex w-full gap-2 sm:max-w-[28rem]">
                <select
                  id={addId}
                  value={adding}
                  onChange={(event) => setAdding(event.target.value)}
                  className={selectClass}
                  disabled={items.length >= SUBJECTS_MAX}
                >
                  <option value="">Choose a subject</option>
                  {available.map((entry) => (
                    <option key={entry.code} value={entry.code}>
                      {entry.name}
                    </option>
                  ))}
                </select>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!adding || items.length >= SUBJECTS_MAX}
                  onClick={() => {
                    setItems((list) => addSubject(list, adding, SUBJECTS_MAX));
                    setAdding("");
                  }}
                >
                  Add
                </Button>
              </div>
            </div>

            {items.length === 0 ? (
              <p className="text-sm text-text-secondary">No subjects yet.</p>
            ) : (
              <ol aria-label="Your subjects" className="divide-y divide-border rounded-xl border border-border">
                {items.map((item, index) => {
                  const entry = nameOf(item.code);
                  const name = entry?.name ?? item.code;
                  return (
                    <li key={item.code} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5">
                      <span className="min-w-0 flex-1 text-sm font-bold text-foreground">
                        <span className="mr-2 font-normal text-text-secondary">{index + 1}.</span>
                        {name}
                        {entry?.deprecated && <span className="ml-1 font-normal text-text-secondary">(no longer offered)</span>}
                      </span>
                      <label className="flex items-center gap-1.5 text-sm text-text-secondary">
                        <input
                          type="checkbox"
                          checked={item.isPriority}
                          onChange={() => setItems((list) => toggleSubject(list, item.code, "isPriority"))}
                          aria-label={`Priority: ${name}`}
                          className="h-4 w-4 accent-[hsl(var(--brand))]"
                        />
                        Priority
                      </label>
                      <label className="flex items-center gap-1.5 text-sm text-text-secondary">
                        <input
                          type="checkbox"
                          checked={item.needsImprovement}
                          onChange={() => setItems((list) => toggleSubject(list, item.code, "needsImprovement"))}
                          aria-label={`Needs work: ${name}`}
                          className="h-4 w-4 accent-[hsl(var(--brand))]"
                        />
                        Needs work
                      </label>
                      <span className="flex gap-1">
                        <Button
                          ref={(node) => {
                            if (node) buttons.current.set(`${item.code}:up`, node);
                            else buttons.current.delete(`${item.code}:up`);
                          }}
                          type="button"
                          size="icon"
                          variant="ghost"
                          className="h-8 w-8"
                          aria-label={`Move ${name} up`}
                          disabled={index === 0}
                          onClick={() => move(index, -1, item.code)}
                        >
                          <ArrowUp aria-hidden className="h-4 w-4" />
                        </Button>
                        <Button
                          ref={(node) => {
                            if (node) buttons.current.set(`${item.code}:down`, node);
                            else buttons.current.delete(`${item.code}:down`);
                          }}
                          type="button"
                          size="icon"
                          variant="ghost"
                          className="h-8 w-8"
                          aria-label={`Move ${name} down`}
                          disabled={index === items.length - 1}
                          onClick={() => move(index, 1, item.code)}
                        >
                          <ArrowDown aria-hidden className="h-4 w-4" />
                        </Button>
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          className="h-8 w-8"
                          aria-label={`Remove ${name}`}
                          onClick={() => setItems((list) => removeSubject(list, item.code))}
                        >
                          <X aria-hidden className="h-4 w-4" />
                        </Button>
                      </span>
                    </li>
                  );
                })}
              </ol>
            )}
          </SettingsCard>
        </form>
      )}
    </>
  );
}
