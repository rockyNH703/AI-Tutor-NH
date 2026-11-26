"use client";

import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import type { JSX } from "react";

import { useAppearance } from "@/components/appearance-provider";
import { THEME_LABELS, type UserTheme, userTheme } from "@/components/theme-switcher";
import type { FontSize } from "@/lib/api/types";
import { ChoiceGroup, SectionHeading, SettingsCard, ToggleRow } from "./primitives";

const THEME_ICONS: Record<UserTheme, JSX.Element> = {
  dark: <Moon aria-hidden className="h-4 w-4" />,
  light: <Sun aria-hidden className="h-4 w-4" />,
  system: <Monitor aria-hidden className="h-4 w-4" />,
};
const SIZE_LABELS: Record<FontSize, string> = {
  small: "Small",
  medium: "Default",
  large: "Large",
  x_large: "Extra large",
};

/** A divided list of switches: one row each, first and last flush with the card. */
const LIST = "flex flex-col divide-y divide-border [&>*]:py-3 [&>*:first-child]:pt-0 [&>*:last-child]:pb-0";

/**
 * Saved to the account as it changes (one versioned PATCH per burst of changes) and applied
 * at once to the whole app, so this page is its own preview.
 */
export function AppearanceSection() {
  const context = useAppearance();
  const { setTheme } = useTheme();
  const appearance = context?.appearance;

  return (
    <>
      <SectionHeading
        title="Appearance & Accessibility"
        description="Saved to your account, so it follows you to every device."
      />
      {!context || !appearance ? (
        <SkeletonGroup label="Loading appearance" className="flex flex-col gap-4">
          <Skeleton className="h-48 w-full rounded-2xl" />
          <Skeleton className="h-60 w-full rounded-2xl" />
        </SkeletonGroup>
      ) : (
        <div className="flex flex-col gap-4">
          <SettingsCard title="Display">
            <ChoiceGroup<UserTheme>
              legend="Theme"
              description="System follows your device."
              value={userTheme(appearance.theme)}
              options={(Object.keys(THEME_LABELS) as UserTheme[]).map((value) => ({
                value,
                label: THEME_LABELS[value],
                icon: THEME_ICONS[value],
              }))}
              onChange={(theme) => {
                setTheme(theme);
                context.change({ theme });
              }}
            />
            <ChoiceGroup<FontSize>
              legend="Text size"
              description="Scales text and spacing."
              value={appearance.fontSize}
              options={(Object.keys(SIZE_LABELS) as FontSize[]).map((value) => ({ value, label: SIZE_LABELS[value] }))}
              onChange={(fontSize) => context.change({ fontSize })}
            />
          </SettingsCard>
          <SettingsCard title="Accessibility">
            <div className={LIST}>
              <ToggleRow
                label="High contrast"
                description="Darker secondary text and clearer borders."
                checked={appearance.highContrast}
                onCheckedChange={(highContrast) => context.change({ highContrast })}
              />
              <ToggleRow
                label="Reduce motion"
                description="Turns off animations. Your device's setting is respected too."
                checked={appearance.reduceMotion}
                onCheckedChange={(reduceMotion) => context.change({ reduceMotion })}
              />
              <ToggleRow
                label="Comfortable reading"
                description="More line, letter and word spacing in answers."
                checked={appearance.readingComfort}
                onCheckedChange={(readingComfort) => context.change({ readingComfort })}
              />
            </div>
          </SettingsCard>
        </div>
      )}
    </>
  );
}
