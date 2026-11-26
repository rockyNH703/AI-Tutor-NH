"use client";

import { cn } from "@v1/ui/utils";
import { usePathname, useSearchParams } from "next/navigation";
import { type KeyboardEvent, type ReactNode, useCallback, useId, useRef } from "react";

/**
 * A page split into views, so a long page becomes a few short ones: WAI-ARIA tabs with automatic activation
 * (arrows, Home and End move and select; Tab goes on into the open panel). Only the open panel is rendered, so a view's
 * state that must survive a switch lives with the caller.
 */

export type ViewTab<T extends string> = { value: T; label: string; count?: number | null };
export type TabIds = { tab: (value: string) => string; panel: (value: string) => string };

/** The ids that tie each tab to its panel. */
export function useTabIds(): TabIds {
  const base = useId();
  return { tab: (value) => `${base}-tab-${value}`, panel: (value) => `${base}-panel-${value}` };
}

/**
 * The open view, kept in the address (`?view=`): a reload, a shared link and Back keep it. Replaces the entry, so
 * switching views does not fill the history. Anything unknown is the first view.
 */
export function useViewParam<T extends string>(values: readonly T[], name = "view"): [T, (value: T) => void] {
  const params = useSearchParams();
  const pathname = usePathname();
  const asked = params?.get(name);
  const value = values.find((item) => item === asked) ?? values[0];
  const set = useCallback(
    (next: T) => {
      const search = new URLSearchParams(window.location.search);
      if (next === values[0]) search.delete(name);
      else search.set(name, next);
      const query = search.toString();
      window.history.replaceState(window.history.state, "", `${pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    },
    [name, pathname, values],
  );
  return [value as T, set];
}

export function ViewTabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
  ids,
  className,
}: {
  label: string;
  tabs: readonly ViewTab<T>[];
  value: T;
  onChange: (value: T) => void;
  ids: TabIds;
  className?: string;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const select = (index: number) => {
    const next = (index + tabs.length) % tabs.length;
    const tab = tabs[next];
    if (!tab) return;
    onChange(tab.value);
    buttons.current[next]?.focus();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const moves: Record<string, number> = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 };
    const target = moves[event.key];
    if (target === undefined) return;
    event.preventDefault();
    select(target);
  };
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("scrollbar-subtle flex gap-1 overflow-x-auto border-b border-border", className)}
    >
      {tabs.map((tab, index) => {
        const selected = tab.value === value;
        return (
          <button
            key={tab.value}
            ref={(node) => {
              buttons.current[index] = node;
            }}
            type="button"
            role="tab"
            id={ids.tab(tab.value)}
            aria-selected={selected}
            aria-controls={ids.panel(tab.value)}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.value)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              "focus-ring-inset -mb-px flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded-t-lg border-b-2 px-3 text-sm font-semibold transition-colors",
              selected ? "border-brand text-foreground" : "border-transparent text-text-secondary hover:text-foreground",
            )}
          >
            {tab.label}
            {tab.count != null && (
              <span
                className={cn(
                  "min-w-5 rounded-full px-1.5 text-center text-2xs font-bold tabular-nums",
                  selected ? "bg-brand-soft text-brand" : "bg-surface-2 text-text-secondary",
                )}
              >
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/**
 * A view's panel: focusable, so the keyboard reaches content that has no control of its own. `hidden` keeps a closed
 * panel in the page (a form's fields stay mounted); otherwise only the open panel is rendered at all.
 */
export function TabPanel({
  ids,
  value,
  hidden,
  children,
  className,
}: {
  ids: TabIds;
  value: string;
  hidden?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="tabpanel"
      hidden={hidden}
      id={ids.panel(value)}
      aria-labelledby={ids.tab(value)}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a tab panel is focusable when its content may have no control (WAI-ARIA tabs)
      tabIndex={0}
      className={cn("focus-ring-inset rounded-lg outline-none", className)}
    >
      {children}
    </div>
  );
}
