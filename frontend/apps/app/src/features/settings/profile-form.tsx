"use client";

import { Button } from "@v1/ui/button";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ApiError, describeError } from "@/lib/api/errors";
import type {
  CatalogEntry,
  EngineeringProfileField,
  EngineeringProfileValues,
  LearnerProfileField,
  LearnerProfileValues,
} from "@/lib/api/types";
import { ProfileConflict, catalogOptions, useEngineeringProfile, useLearnerProfile } from "./learning-data";
import { editFor, hasChanges } from "./learning-model";
import { Field } from "./primitives";
import { useUnsavedChanges } from "./use-unsaved-changes";

/** A form's values: some fields of a versioned profile, each of which may be unset. */
export type DraftOf<V, F extends keyof V> = { [K in F]?: V[K] | null };
export type ProfileDraft = DraftOf<LearnerProfileValues, LearnerProfileField>;
export type EngineeringFormDraft = DraftOf<EngineeringProfileValues, EngineeringProfileField>;

/** The Engineering Profile's fields, in the order the form shows them. */
export const ENGINEERING_FIELDS = [
  "role",
  "experienceLevel",
  "certificationGoal",
  "focusAreas",
  "platforms",
] as const satisfies readonly EngineeringProfileField[];

function pick<D>(profile: object, fields: readonly string[]): D {
  const values: Record<string, unknown> = {};
  for (const field of fields) values[field] = (profile as Record<string, unknown>)[field];
  return values as D;
}

type VersionedProfile<P, V, F> = {
  profile: P | null;
  error: unknown;
  save: (set: Partial<V>, reset: F[]) => Promise<P>;
  retry: () => void;
};

/**
 * One form over some fields of a versioned profile (the Engineering Profile; the learner profile's
 * Skills & Certification Goals and Practice & Lab Time). Saves only what changed as one versioned edit; keeps the
 * user's edit on a conflict; blocks double submits; warns before leaving unsaved; shows the API's
 * field errors next to their fields (`set.platforms.1` marks `platforms`).
 */
function useVersionedForm<V, F extends keyof V & string, P extends object>(
  { profile, error, save, retry }: VersionedProfile<P, V, F>,
  fields: readonly F[],
  savedMessage: string,
) {
  type Draft = DraftOf<V, F>;
  const base = useMemo(() => (profile ? pick<Draft>(profile, fields) : null), [profile, fields]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [invalid, setInvalid] = useState<Set<string>>(new Set());
  const edit =
    base && draft
      ? editFor<Record<string, unknown>>(base as Record<string, unknown>, draft as Record<string, unknown>, fields)
      : null;
  const dirty = edit ? hasChanges(edit) : false;
  useUnsavedChanges(dirty);

  // The loaded (or conflicting) profile becomes the form, unless the user has edits in it.
  // Follows `base` only while the form is clean.
  useEffect(() => {
    if (base && (!draft || !dirty)) setDraft(base);
  }, [base]);

  const change = (values: Draft) => {
    setDraft((current) => ({ ...current, ...values }));
    setInvalid((current) => {
      const next = new Set(current);
      for (const key of Object.keys(values)) next.delete(key);
      return next;
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (pending || !edit || !dirty) return;
    setPending(true);
    setNotice(null);
    setInvalid(new Set());
    try {
      const saved = await save(edit.set as Partial<V>, edit.reset as F[]);
      setDraft(pick<Draft>(saved, fields));
      toast.success(savedMessage);
    } catch (failure) {
      if (failure instanceof ProfileConflict) {
        setNotice("This was just changed somewhere else. Your edits are still here: check them and save again.");
      } else if (failure instanceof ApiError && failure.fields?.length) {
        const names = failure.fields.map((entry) => entry.field.replace(/^set\./, "").split(".")[0] ?? "");
        setInvalid(new Set(names));
        if (!names.some((name) => fields.includes(name as F))) toast.error(describeError(failure).message);
      } else {
        toast.error(describeError(failure).message);
      }
    } finally {
      setPending(false);
    }
  };

  return {
    /** The values as saved, for what a field may keep (a deprecated code). */
    base,
    draft,
    loadError: error,
    retry,
    change,
    submit,
    reset: () => base && setDraft(base),
    dirty,
    pending,
    notice,
    invalid: (field: F) => invalid.has(field),
  };
}

/** A form over some of the learner profile's fields (Skills & Certification Goals, Practice & Lab Time). */
export function useProfileForm(fields: readonly LearnerProfileField[], savedMessage: string) {
  const resource = useLearnerProfile();
  return useVersionedForm<LearnerProfileValues, LearnerProfileField, NonNullable<typeof resource.profile>>(
    resource,
    fields,
    savedMessage,
  );
}

/** The Engineering Profile's form: every field of it. */
export function useEngineeringForm(savedMessage: string) {
  const resource = useEngineeringProfile();
  return useVersionedForm<EngineeringProfileValues, EngineeringProfileField, NonNullable<typeof resource.profile>>(
    resource,
    ENGINEERING_FIELDS,
    savedMessage,
  );
}

export function SaveFooter({ dirty, pending, onCancel }: { dirty: boolean; pending: boolean; onCancel: () => void }) {
  return (
    <>
      {dirty && (
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
      )}
      <Button type="submit" size="sm" disabled={pending || !dirty}>
        {pending ? "Saving…" : "Save"}
      </Button>
    </>
  );
}

const selectClass = "focus-field h-10 w-full rounded-xl border border-input bg-card px-3 text-base sm:max-w-[20rem] sm:text-sm";

/** A catalog-driven choice. A deprecated value is kept and shown as such, never offered anew. */
export function CatalogSelect({
  label,
  hint,
  entries,
  value,
  onChange,
  error,
}: {
  label: string;
  hint?: string;
  entries: readonly CatalogEntry[];
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  error?: string | null;
}) {
  return (
    <Field label={label} hint={hint} error={error}>
      {(props) => (
        <select {...props} value={value ?? ""} onChange={(event) => onChange(event.target.value || null)} className={selectClass}>
          <option value="">Not set</option>
          {catalogOptions(entries, value).map((entry) => (
            <option key={entry.code} value={entry.code}>
              {entry.deprecated ? `${entry.name} (no longer offered)` : entry.name}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}

export { selectClass };
