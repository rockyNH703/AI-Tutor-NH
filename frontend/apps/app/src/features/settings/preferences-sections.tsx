"use client";

import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { useEffect, useRef, useState } from "react";

import { describeError, isAbortError } from "@/lib/api/errors";
import { getLearningPreferences, previewTutor, updateLearningPreferences } from "@/lib/api/learning";
import type {
  LearningPreferenceField,
  LearningPreferenceValues,
  LearningPreferences,
  TutorPreviewRequest,
} from "@/lib/api/types";
import { LEARNING_FIELDS, PREFERENCES, TUTOR_FIELDS, reachesAssistant } from "./learning-labels";
import { ChoiceGroup, Disclosure, SectionHeading, SettingsCard } from "./primitives";
import { useAutoSaver } from "./use-auto-saver";

function preferenceChanges(set: Partial<LearningPreferences>): Partial<LearningPreferenceValues> {
  const changes: Partial<LearningPreferenceValues> = {};
  for (const field of [...LEARNING_FIELDS, ...TUTOR_FIELDS]) {
    if (set[field] !== undefined) (changes as Record<string, unknown>)[field] = set[field];
  }
  return changes;
}

function usePreferences(onSaved?: () => void) {
  return useAutoSaver<LearningPreferences>({
    load: getLearningPreferences,
    save: (version, set) => updateLearningPreferences({ version, set: preferenceChanges(set) }),
    onSaved,
  });
}

function Choices({
  fields,
  value,
  change,
}: {
  fields: readonly LearningPreferenceField[];
  value: LearningPreferences;
  change: (changes: Partial<LearningPreferences>) => void;
}) {
  return (
    <>
      {fields.map((field) => {
        const info = PREFERENCES[field];
        const options = Object.entries(info.options) as [string, string][];
        return (
          <ChoiceGroup<string>
            key={field}
            legend={info.label}
            description={info.description}
            value={value[field]}
            options={options.map(([option, label]) => ({ value: option, label }))}
            onChange={(option) => change({ [field]: option } as Partial<LearningPreferences>)}
          />
        );
      })}
    </>
  );
}

/**
 * The preferences that add nothing to the assistant right now: kept, editable and saved like the
 * others, but folded away under a heading that says so, so the ones that matter come first.
 */
function NotUsedNow({
  fields,
  value,
  change,
}: {
  fields: readonly LearningPreferenceField[];
  value: LearningPreferences;
  change: (changes: Partial<LearningPreferences>) => void;
}) {
  if (!fields.length) return null;
  return (
    <SettingsCard title="Not used by NetSec AI right now" description="Still saved, but they don't change answers at the moment.">
      <Disclosure
        label={`Show ${fields.length} more ${fields.length === 1 ? "preference" : "preferences"}`}
        openLabel={`Hide ${fields.length === 1 ? "it" : "them"}`}
      >
        <div className="flex flex-col gap-5">
          <Choices fields={fields} value={value} change={change} />
        </div>
      </Disclosure>
    </SettingsCard>
  );
}

function LoadFailed({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <SettingsCard title="Couldn't load your preferences">
      <p className="text-sm text-text-secondary">{describeError(error).message}</p>
      <button
        type="button"
        onClick={retry}
        className="focus-ring self-start rounded text-sm font-medium text-brand underline underline-offset-2"
      >
        Try again
      </button>
    </SettingsCard>
  );
}

const NEUTRAL_NOTE = "Saved as you choose. No preference leaves it to NetSec AI.";

const LEARNING_USED = LEARNING_FIELDS.filter(reachesAssistant);
const LEARNING_UNUSED = LEARNING_FIELDS.filter((field) => !reachesAssistant(field));
const TUTOR_USED = TUTOR_FIELDS.filter(reachesAssistant);
const TUTOR_UNUSED = TUTOR_FIELDS.filter((field) => !reachesAssistant(field));

export function LearningSection() {
  const { value, change, loadError, retry } = usePreferences();
  return (
    <>
      <SectionHeading
        title="Learning Preferences"
        description="How answers are explained: language, depth and visuals. A request in a chat overrides them."
      />
      {loadError ? (
        <LoadFailed error={loadError} retry={retry} />
      ) : !value ? (
        <SkeletonGroup label="Loading your preferences">
          <Skeleton className="h-96 w-full rounded-2xl" />
        </SkeletonGroup>
      ) : (
        <div className="flex flex-col gap-4">
          <SettingsCard title="How answers are explained" description={NEUTRAL_NOTE}>
            <Choices fields={LEARNING_USED} value={value} change={change} />
          </SettingsCard>
          <NotUsedNow fields={LEARNING_UNUSED} value={value} change={change} />
        </div>
      )}
    </>
  );
}

const PREVIEW_NOTE = "Your profile and answer preferences, as NetSec AI receives them. Never your name, email or photo.";

/**
 * "What NetSec AI is told": the API's own preview (POST /v1/learning/tutor-preview), built by the
 * same function the chat uses, so it cannot differ from a real conversation. `draft` lays unsaved
 * edits over what is stored (the Engineering Profile form sends its own), so the preview is what
 * saving them would tell the assistant. Refreshed after every save and every change of the draft
 * (debounced; a newer request cancels an older one). It is instructions, never shown as a reply.
 */
export function TutorPreviewCard({
  refreshKey = 0,
  draft,
  description = PREVIEW_NOTE,
}: {
  refreshKey?: number;
  draft?: TutorPreviewRequest;
  description?: string;
}) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const first = useRef(true);
  const body = JSON.stringify(draft ?? {});

  useEffect(() => {
    const controller = new AbortController();
    const delay = first.current ? 0 : 300;
    first.current = false;
    const timer = setTimeout(() => {
      previewTutor(JSON.parse(body) as TutorPreviewRequest, controller.signal).then(
        (preview) => {
          setText(preview.instructions);
          setError(null);
        },
        (failure) => {
          if (!isAbortError(failure)) setError(describeError(failure).message);
        },
      );
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [refreshKey, body]);

  return (
    <SettingsCard title="What NetSec AI is told" description={description}>
      <Disclosure label="Show the preview" openLabel="Hide the preview">
      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : text === null ? (
        <SkeletonGroup label="Loading the preview">
          <Skeleton className="h-24 w-full rounded-xl" />
        </SkeletonGroup>
      ) : text === "" ? (
        <p className="text-sm text-text-secondary">
          Nothing yet: with nothing chosen, NetSec AI gets only its standard instructions.
        </p>
      ) : (
        <pre
          aria-live="polite"
          className="max-h-96 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-border bg-surface p-3.5 font-sans text-sm leading-relaxed text-foreground"
        >
          {text}
        </pre>
      )}
      </Disclosure>
    </SettingsCard>
  );
}

export function TutorSection() {
  const [saves, setSaves] = useState(0);
  const { value, change, loadError, retry } = usePreferences(() => setSaves((count) => count + 1));
  return (
    <>
      <SectionHeading
        title="AI Assistant"
        description="Answer length, how it works through problems, and what it is told."
      />
      {loadError ? (
        <LoadFailed error={loadError} retry={retry} />
      ) : !value ? (
        <SkeletonGroup label="Loading your assistant settings">
          <Skeleton className="h-96 w-full rounded-2xl" />
        </SkeletonGroup>
      ) : (
        <div className="flex flex-col gap-4">
          <SettingsCard title="Your assistant" description={NEUTRAL_NOTE}>
            <Choices fields={TUTOR_USED} value={value} change={change} />
          </SettingsCard>
          <TutorPreviewCard refreshKey={saves} />
          <NotUsedNow fields={TUTOR_UNUSED} value={value} change={change} />
        </div>
      )}
    </>
  );
}
