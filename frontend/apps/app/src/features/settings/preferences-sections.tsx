"use client";

import { Eye, RotateCcw, SlidersHorizontal } from "lucide-react";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { useEffect, useRef, useState } from "react";

import { InlineAction } from "@/components/inline-action";
import { describeError, isAbortError } from "@/lib/api/errors";
import { getLearningPreferences, previewTutor, updateLearningPreferences } from "@/lib/api/learning";
import type {
  LearningPreferenceField,
  LearningPreferenceValues,
  LearningPreferences,
  TutorPreviewRequest,
} from "@/lib/api/types";
import { LEARNING_FIELDS, PREFERENCES, TUTOR_FIELDS, isMainField } from "./learning-labels";
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
 * The rest of a page's preferences (all of them reach NetSec AI): folded away so the page stays about one screen
 * and the main choices come first.
 */
function MorePreferences({
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
    <SettingsCard title="More preferences" description="These shape NetSec AI's answers too.">
      <Disclosure
        icon={SlidersHorizontal}
        label={`Explore ${fields.length} more ${fields.length === 1 ? "preference" : "preferences"}`}
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
      <InlineAction icon={RotateCcw} onClick={retry} className="-ml-2 mt-1">
        Try again
      </InlineAction>
    </SettingsCard>
  );
}

const NEUTRAL_NOTE = "Saved as you choose. No preference leaves it to NetSec AI.";

const LEARNING_MAIN = LEARNING_FIELDS.filter(isMainField);
const LEARNING_MORE = LEARNING_FIELDS.filter((field) => !isMainField(field));
const TUTOR_MAIN = TUTOR_FIELDS.filter(isMainField);
const TUTOR_MORE = TUTOR_FIELDS.filter((field) => !isMainField(field));

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
            <Choices fields={LEARNING_MAIN} value={value} change={change} />
          </SettingsCard>
          <MorePreferences fields={LEARNING_MORE} value={value} change={change} />
        </div>
      )}
    </>
  );
}

const PREVIEW_NOTE =
  "Your profile and answer preferences, added to NetSec AI's standard instructions. Never your name, email or photo.";

/**
 * "What your settings tell NetSec AI": the API's own preview (POST /v1/learning/tutor-preview), built by the
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
    <SettingsCard title="What your settings tell NetSec AI" description={description}>
      <Disclosure icon={Eye} label="Preview what AI sees">
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
            <Choices fields={TUTOR_MAIN} value={value} change={change} />
          </SettingsCard>
          <TutorPreviewCard refreshKey={saves} />
          <MorePreferences fields={TUTOR_MORE} value={value} change={change} />
        </div>
      )}
    </>
  );
}
