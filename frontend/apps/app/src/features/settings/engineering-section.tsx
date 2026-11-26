"use client";

import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { type ReactNode, useEffect, useState } from "react";

import { TabPanel, ViewTabs, useTabIds } from "@/components/view-tabs";
import { describeError } from "@/lib/api/errors";
import { type EngineeringCatalog, type EngineeringProfileField, FOCUS_AREAS_MAX, PLATFORMS_MAX } from "@/lib/api/types";
import { catalogOptions, useEngineeringCatalog } from "./learning-data";
import { engineeringPreview, toggleCode } from "./learning-model";
import { TutorPreviewCard } from "./preferences-sections";
import { ChipGroup, Notice, SectionHeading, SettingsCard } from "./primitives";
import { CatalogSelect, type EngineeringFormDraft, SaveFooter, useEngineeringForm } from "./profile-form";

const CHECK = "Choose one from the list.";
const CHECK_LIST = "One of these can't be saved: it may no longer be offered. Check your choices.";

/** A catalog's entries as chips: every offered one, plus a deprecated one while it is saved or chosen. */
function chips(entries: EngineeringCatalog["focusAreas"], kept: readonly string[]) {
  return catalogOptions(entries, kept).map((entry) => ({
    value: entry.code,
    label: entry.deprecated ? `${entry.name} (no longer offered)` : entry.name,
  }));
}

/**
 * The Engineering Profile's fields, in two cards: who you are at work (role, experience,
 * certification goal) and what you work on (focus areas and platforms, at most 8 and 12). Pure
 * props, so it renders without the API (engineering-section.test.tsx).
 */
export function EngineeringFields({
  catalog,
  base,
  draft,
  change,
  invalid,
  notice,
  footer,
}: {
  catalog: EngineeringCatalog;
  /** The profile as saved: a deprecated code it has stays offered until it is saved away. */
  base: EngineeringFormDraft | null;
  draft: EngineeringFormDraft;
  change: (values: EngineeringFormDraft) => void;
  invalid: (field: EngineeringProfileField) => boolean;
  notice?: string | null;
  footer?: ReactNode;
}) {
  const focusAreas = draft.focusAreas ?? [];
  const platforms = draft.platforms ?? [];
  const ids = useTabIds();
  const refused = invalid("focusAreas") ? "focus" : invalid("platforms") ? "platforms" : null;
  const [shown, setShown] = useState<"focus" | "platforms">(refused ?? "focus");
  // A group the API refused opens on its own, so its error is not left on a closed tab.
  useEffect(() => {
    if (refused) setShown(refused);
  }, [refused]);
  return (
    <>
      <SettingsCard title="Your role" description="Anything left as Not set is simply not used.">
        {notice && <Notice tone="warning">{notice}</Notice>}
        <CatalogSelect
          label="Role"
          entries={catalog.roles}
          value={draft.role}
          onChange={(role) => change({ role })}
          error={invalid("role") ? CHECK : null}
        />
        <CatalogSelect
          label="Experience"
          entries={catalog.experienceLevels}
          value={draft.experienceLevel}
          onChange={(experienceLevel) => change({ experienceLevel })}
          error={invalid("experienceLevel") ? CHECK : null}
        />
        <CatalogSelect
          label="Certification goal"
          hint="The certification you're working towards, if any."
          entries={catalog.certifications}
          value={draft.certificationGoal}
          onChange={(certificationGoal) => change({ certificationGoal })}
          error={invalid("certificationGoal") ? CHECK : null}
        />
      </SettingsCard>
      <SettingsCard
        title="What you work on"
        description="Examples, commands and configuration are written for these."
        footer={footer}
      >
        <ViewTabs
          label="What you work on"
          ids={ids}
          value={shown}
          onChange={setShown}
          tabs={[
            { value: "focus", label: "Focus areas", count: focusAreas.length || null },
            { value: "platforms", label: "Platforms", count: platforms.length || null },
          ]}
        />
        <TabPanel ids={ids} value="focus" hidden={shown !== "focus"}>
        <ChipGroup
          legend="Focus areas"
          description={`The technologies you work with most. Up to ${FOCUS_AREAS_MAX}.`}
          options={chips(catalog.focusAreas, [...(base?.focusAreas ?? []), ...focusAreas])}
          value={focusAreas}
          max={FOCUS_AREAS_MAX}
          onToggle={(code) => change({ focusAreas: toggleCode(focusAreas, code, FOCUS_AREAS_MAX) })}
          error={invalid("focusAreas") ? CHECK_LIST : null}
        />
        </TabPanel>
        <TabPanel ids={ids} value="platforms" hidden={shown !== "platforms"}>
        <ChipGroup
          legend="Platforms"
          description={`Vendors, operating systems and clouds you use. Up to ${PLATFORMS_MAX}.`}
          options={chips(catalog.platforms, [...(base?.platforms ?? []), ...platforms])}
          value={platforms}
          max={PLATFORMS_MAX}
          onToggle={(code) => change({ platforms: toggleCode(platforms, code, PLATFORMS_MAX) })}
          error={invalid("platforms") ? CHECK_LIST : null}
        />
        </TabPanel>
      </SettingsCard>
    </>
  );
}

/**
 * Settings > Engineering Profile: what NetSec AI is told about the user's work. Saved as one
 * versioned edit (PATCH /v1/engineering/profile), like every profile form; the preview below shows
 * what the assistant would be told with the form as it is, saved or not.
 */
export function EngineeringSection() {
  const { catalog, error: catalogError, retry: retryCatalog } = useEngineeringCatalog();
  const form = useEngineeringForm("Engineering Profile saved.");
  const loadError = catalogError ?? form.loadError;

  return (
    <>
      <SectionHeading
        title="Engineering Profile"
        description="Your role and the technology you work with: NetSec AI picks its depth, vendor syntax and examples from them."
      />
      {loadError ? (
        <SettingsCard title="Couldn't load your Engineering Profile">
          <p className="text-sm text-text-secondary">{describeError(loadError).message}</p>
          <button
            type="button"
            onClick={() => (catalogError ? retryCatalog() : form.retry())}
            className="focus-ring self-start rounded text-sm font-medium text-brand underline underline-offset-2"
          >
            Try again
          </button>
        </SettingsCard>
      ) : !catalog || !form.draft ? (
        <SkeletonGroup label="Loading your Engineering Profile">
          <Skeleton className="h-96 w-full rounded-2xl" />
        </SkeletonGroup>
      ) : (
        <div className="flex flex-col gap-4">
          <form onSubmit={form.submit} className="flex flex-col gap-4">
            <EngineeringFields
              catalog={catalog}
              base={form.base}
              draft={form.draft}
              change={form.change}
              invalid={form.invalid}
              notice={form.notice}
              footer={<SaveFooter dirty={form.dirty} pending={form.pending} onCancel={form.reset} />}
            />
          </form>
          <TutorPreviewCard
            draft={{ engineering: engineeringPreview(form.draft) }}
            description="With the choices above, saved or not. Never your name, email or photo."
          />
        </div>
      )}
    </>
  );
}
