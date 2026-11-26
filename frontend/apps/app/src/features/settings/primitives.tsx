"use client";

import { Card, CardContent, CardDescription, CardFooter, CardTitle } from "@v1/ui/card";
import { Switch } from "@v1/ui/switch";
import { cn } from "@v1/ui/utils";
import { Check, ChevronRight, type LucideIcon } from "lucide-react";
import { Children, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { takeHeadingFocus } from "./heading-focus";
import { canToggle, chosenText } from "./learning-model";

/**
 * The building blocks every Settings section uses, so they all look and behave alike: the
 * section's own heading (`h2`, the app header owns the `h1`), cards with an `h3`, labelled
 * fields whose hint and error are announced with the field, switches and choice groups.
 *
 * Product settings, not a form: on wide screens a field is a row (what it is | the control) and a
 * choice group sits beside its explanation; on phones everything stacks as before. The rows turn on
 * where the CARD is wide enough (the Settings sidebar takes 256-288px from md up): fields at lg,
 * pill groups at xl. Only layout changes: labels, hints, errors and fieldset names are wired to
 * their controls exactly as before, in the same DOM order.
 */

/**
 * The section's title. It is where focus goes when a section opens in place of what was used to
 * open it (a row of the phone list, search, arriving from the app): `data-settings-heading`
 * marks it for the workspace, and it takes focus itself if it mounts after the request (the
 * section was still loading). Not the page's first element, so Next's own focus call skips it.
 */
export function SectionHeading({ title, description }: { title: string; description: string }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (takeHeadingFocus()) heading.current?.focus({ preventScroll: true });
  }, []);
  return (
    <div className="mb-5 md:mb-6">
      <h2
        ref={heading}
        tabIndex={-1}
        data-settings-heading=""
        className="font-display text-2xl font-bold tracking-tight text-foreground outline-none"
      >
        {title}
      </h2>
      <p className="mt-1 max-w-prose text-sm leading-relaxed text-text-secondary">{description}</p>
    </div>
  );
}

/**
 * A group of settings. One that is only an action (Google, Email, Delete account) is a single row: what it is on the
 * left, its button on the right. One with fields keeps its action row under them.
 */
export function SettingsCard({
  title,
  description,
  children,
  footer,
  tone = "default",
  titleAs = "h3",
}: {
  title: string;
  /** A card under a sub-heading of the section (the danger zone) steps down to h4. */
  titleAs?: "h3" | "h4";
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  tone?: "default" | "danger";
}) {
  // Children that render nothing (`{open && …}` while closed) leave a one-row card.
  const content = Children.toArray(children).length > 0;
  const row = !content && footer;
  return (
    <Card className={tone === "danger" ? "border-destructive/40" : undefined}>
      <div className={cn("flex flex-col gap-3 p-5 md:px-6", row && "sm:flex-row sm:items-center sm:justify-between sm:gap-6")}>
        <div className="flex min-w-0 flex-col gap-1">
          <CardTitle as={titleAs} className="text-base">
            {title}
          </CardTitle>
          {description && <CardDescription className="max-w-prose leading-relaxed">{description}</CardDescription>}
        </div>
        {row && <div className="flex shrink-0 flex-wrap items-center gap-2">{footer}</div>}
      </div>
      {content && <CardContent className="flex flex-col gap-5 px-5 pb-5 pt-0 md:px-6">{children}</CardContent>}
      {content && footer && (
        <CardFooter
          className={cn(
            "justify-end gap-2 rounded-b-2xl px-5 md:px-6",
            tone === "danger" ? "border-destructive/20 bg-destructive/5" : "bg-surface/60",
          )}
        >
          {footer}
        </CardFooter>
      )}
    </Card>
  );
}

/**
 * Detail on demand (a form used now and then, a long preview): a full-width disclosure row, as in macOS Settings: an
 * optional icon, its name, and a chevron that turns down while it is open. The whole row is the button; closed,
 * nothing of the detail is rendered.
 */
export function Disclosure({
  label,
  openLabel,
  icon: Icon,
  defaultOpen = false,
  children,
}: {
  label: string;
  /** The button's name while open (default: the same, as macOS keeps it). */
  openLabel?: string;
  /** What the row leads to (an eye for a preview, sliders for more settings), never decoration. */
  icon?: LucideIcon;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className="flex flex-col">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((value) => !value)}
        className="focus-ring flex min-h-11 w-full items-center gap-3 rounded-xl bg-surface-2/70 px-3.5 py-2.5 text-left text-sm font-semibold text-foreground transition-colors duration-fast hover:bg-surface-2 active:bg-border/70"
      >
        {Icon && <Icon aria-hidden className="h-4 w-4 shrink-0 text-text-secondary" />}
        <span className="min-w-0 flex-1">{open ? (openLabel ?? label) : label}</span>
        <ChevronRight
          aria-hidden
          className={cn(
            "h-4 w-4 shrink-0 text-text-secondary motion-safe:transition-transform motion-safe:duration-fast",
            open && "rotate-90",
          )}
        />
      </button>
      {open && (
        <div id={id} className="mt-3">
          {children}
        </div>
      )}
    </div>
  );
}

/** A labelled control. `children` gets the ids that tie the label, hint and error to it. */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: (props: { id: string; "aria-describedby"?: string; "aria-invalid"?: true }) => ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  // Stacked: label, control, hint, error. From lg: label and hint on the left, the control and its
  // error on the right (grid placement only, so the DOM and reading order stay the same).
  return (
    <div className="flex flex-col gap-1.5 lg:grid lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:items-start lg:gap-x-8 lg:gap-y-1">
      <label htmlFor={id} className="text-sm font-bold text-foreground lg:col-start-1 lg:row-start-1 lg:pt-2.5">
        {label}
      </label>
      <div className="min-w-0 lg:col-start-2 lg:row-span-2 lg:row-start-1">
        {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      </div>
      {hint && (
        <p id={hintId} className="text-2xs text-text-secondary lg:col-start-1 lg:row-start-2">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-sm text-destructive lg:col-start-2 lg:row-start-3">
          {error}
        </p>
      )}
    </div>
  );
}

export function ToggleRow({
  label,
  description,
  checked,
  onCheckedChange,
  disabled,
}: {
  label: string;
  description?: ReactNode;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-bold text-foreground">
          {label}
        </label>
        {description && (
          <p id={`${id}-description`} className="mt-0.5 text-sm text-text-secondary">
            {description}
          </p>
        )}
      </div>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        aria-describedby={description ? `${id}-description` : undefined}
        className="mt-0.5"
      />
    </div>
  );
}

/** One choice from a few, as real radio buttons (arrow keys move between them). */
export function ChoiceGroup<T extends string>({
  legend,
  description,
  value,
  options,
  onChange,
  stacked = false,
}: {
  legend: string;
  description?: string;
  value: T;
  options: readonly { value: T; label: string; icon?: ReactNode }[];
  onChange: (value: T) => void;
  /** Legend above the pills at every width (a narrow column), instead of beside them on wide screens. */
  stacked?: boolean;
}) {
  const name = useId();
  // A <fieldset> named by aria-labelledby rather than a <legend>, so the name can sit in a column
  // beside the pills: still a group with that name, of real radio buttons.
  return (
    <fieldset
      aria-labelledby={`${name}-legend`}
      aria-describedby={description ? `${name}-description` : undefined}
      className={stacked ? undefined : "xl:flex xl:items-start xl:justify-between xl:gap-8"}
    >
      <div className={stacked ? "min-w-0" : "min-w-0 xl:max-w-[16rem] xl:flex-1 xl:pt-2"}>
        <p id={`${name}-legend`} className="text-sm font-bold text-foreground">
          {legend}
        </p>
        {description && (
          <p id={`${name}-description`} className="mt-0.5 text-sm text-text-secondary">
            {description}
          </p>
        )}
      </div>
      <div className={stacked ? "mt-2 flex flex-wrap gap-2" : "mt-2 flex flex-wrap gap-2 xl:mt-0 xl:flex-[1.6] xl:justify-start"}>
        {options.map((option) => (
          <label key={option.value} className="relative">
            {/* The real radio lies over its pill, invisible: it is what a pointer, a keyboard and
                assistive technology all reach, and the pill below only shows its state. */}
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="peer absolute inset-0 z-10 cursor-pointer opacity-0"
            />
            <span
              className={cn(
                "flex min-h-10 cursor-pointer items-center gap-2 rounded-xl border px-3.5 py-2 text-sm font-medium transition-colors",
                "border-border bg-card text-text-secondary hover:bg-surface-2 hover:text-foreground",
                "peer-checked:border-brand peer-checked:bg-brand-soft peer-checked:text-brand",
                "peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2",
              )}
            >
              {option.icon}
              {option.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/**
 * Several choices from a list, up to `max`: real checkboxes drawn as chips (Tab reaches each one,
 * Space checks it). How many are chosen is shown and announced; once the list is full, the
 * unchecked chips are disabled until one is unchecked, so the limit holds before the API's.
 */
export function ChipGroup({
  legend,
  description,
  options,
  value,
  max,
  onToggle,
  error,
}: {
  legend: string;
  description?: string;
  options: readonly { value: string; label: string }[];
  value: readonly string[];
  max: number;
  onToggle: (value: string) => void;
  error?: string | null;
}) {
  const name = useId();
  const describedBy = [description ? `${name}-description` : null, `${name}-count`, error ? `${name}-error` : null]
    .filter(Boolean)
    .join(" ");
  return (
    <fieldset aria-labelledby={`${name}-legend`} aria-describedby={describedBy}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p id={`${name}-legend`} className="text-sm font-bold text-foreground">
          {legend}
        </p>
        <p id={`${name}-count`} aria-live="polite" className="text-2xs text-text-secondary">
          {chosenText(value.length, max)}
        </p>
      </div>
      {description && (
        <p id={`${name}-description`} className="mt-0.5 text-sm text-text-secondary">
          {description}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {options.map((option) => {
          const checked = value.includes(option.value);
          return (
            <label key={option.value} className="relative">
              {/* The real checkbox lies over its chip, invisible: it is what a pointer, a keyboard
                  and assistive technology all reach, and the chip below only shows its state. */}
              <input
                type="checkbox"
                value={option.value}
                checked={checked}
                disabled={!canToggle(value, option.value, max)}
                onChange={() => onToggle(option.value)}
                className="peer absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
              />
              <span
                className={cn(
                  "flex min-h-10 items-center gap-1.5 rounded-xl border px-3 py-2 text-sm font-medium transition-colors",
                  "border-border bg-card text-text-secondary hover:bg-surface-2 hover:text-foreground",
                  "peer-checked:border-brand peer-checked:bg-brand-soft peer-checked:text-brand",
                  "peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2",
                  "peer-disabled:opacity-50",
                )}
              >
                {checked && <Check aria-hidden className="h-3.5 w-3.5 shrink-0" />}
                {option.label}
              </span>
            </label>
          );
        })}
      </div>
      {error && (
        <p id={`${name}-error`} className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}
    </fieldset>
  );
}

/** A calm notice inside a card: information, not an error. */
export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warning" }) {
  return (
    <div
      className={cn(
        "rounded-xl border px-3.5 py-3 text-sm leading-relaxed",
        tone === "warning"
          ? "border-accent-warm/30 bg-accent-warm-soft text-foreground"
          : "border-brand/20 bg-brand-soft text-foreground",
      )}
    >
      {children}
    </div>
  );
}
