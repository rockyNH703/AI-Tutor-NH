"use client";

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@v1/ui/dropdown-menu";
import { cn } from "@v1/ui/utils";
import { Check, ChevronDown, ChevronLeft } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

import { LinkPending } from "@/components/link-pending";
import type { ProgramRef } from "@/lib/api/programs";
import { type ProgramArea, programRoute, programTabs } from "./program-nav";
import { usePrograms } from "./use-programs";

/**
 * The bar on every program page and program chat: the way back to My Programs, the program (a switcher when the
 * account has more than one), and the program's areas as tabs. The tabs are links (each area is its own page), the
 * current one marked with aria-current. On a phone the tab row scrolls sideways on its own (its bar hidden, as
 * ViewTabs does), so the page itself never does. It only links: the API decides on every request what opens.
 */
export function ProgramNav({ slug, className }: { slug: string; className?: string }) {
  const pathname = usePathname();
  const { data } = usePrograms();
  const route = programRoute(pathname);
  return (
    <ProgramNavView
      slug={slug}
      area={route?.slug === slug ? route.area : null}
      programs={data?.programs ?? []}
      className={className}
    />
  );
}

/** The bar itself, from plain values (unit-tested). `programs` are the account's open programs (none while loading). */
export function ProgramNavView({
  slug,
  area,
  programs,
  className,
}: {
  slug: string;
  area: ProgramArea | null;
  programs: readonly ProgramRef[];
  className?: string;
}) {
  const current = programs.find((program) => program.slug === slug);
  const tabs = programTabs(slug);
  const currentTab = useRef<HTMLAnchorElement | null>(null);
  useEffect(() => {
    // On a phone the current area may sit past the edge of the row: bring it into view (instant, never smooth).
    currentTab.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [area, slug]);
  return (
    <nav aria-label="Program" className={cn("min-w-0", className)}>
      <div className="flex min-h-11 min-w-0 items-center gap-1">
        <Link
          href="/programs"
          className="focus-ring -ml-2 inline-flex min-h-11 shrink-0 items-center gap-1 rounded-lg px-2 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-2 hover:text-foreground"
        >
          <ChevronLeft aria-hidden className="h-4 w-4" />
          My Programs
          <LinkPending />
        </Link>
        {current && (
          <>
            <span aria-hidden className="shrink-0 text-text-secondary">
              /
            </span>
            {programs.length > 1 ? (
              <ProgramSwitcher programs={programs} current={current} area={area} />
            ) : (
              <span className="min-w-0 truncate px-2 text-sm font-semibold text-foreground" title={current.title}>
                {current.title}
              </span>
            )}
          </>
        )}
      </div>
      {/* The program's areas. Links, not ARIA tabs: each one is a page of its own. */}
      <ul
        className="-mx-1 mt-1 flex gap-1 overflow-x-auto overflow-y-hidden px-1 shadow-[inset_0_-1px_0_hsl(var(--border))] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {tabs.map((tab) => {
          const selected = tab.area === area;
          return (
            <li key={tab.href} className="shrink-0">
              <Link
                ref={selected ? currentTab : undefined}
                href={tab.href}
                aria-current={selected ? "page" : undefined}
                className={cn(
                  "focus-ring-inset flex min-h-11 items-center whitespace-nowrap rounded-t-lg border-b-2 px-3 text-sm font-semibold transition-colors",
                  selected ? "border-brand text-foreground" : "border-transparent text-text-secondary hover:text-foreground",
                )}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** Another of the account's programs, at the same area (its own page from a chat: a month may not exist there). */
function ProgramSwitcher({ programs, current, area }: { programs: readonly ProgramRef[]; current: ProgramRef; area: ProgramArea | null }) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`${current.title}: switch program`}
          className="focus-ring inline-flex min-h-11 min-w-0 items-center gap-1.5 rounded-lg px-2 text-sm font-semibold text-foreground transition-colors hover:bg-surface-2"
        >
          <span className="truncate">{current.title}</span>
          <ChevronDown aria-hidden className="h-4 w-4 shrink-0 text-text-secondary" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-80 max-w-[calc(100vw-2rem)]">
        {programs.map((program) => {
          const selected = program.slug === current.slug;
          const href = programTabs(program.slug).find((tab) => tab.area === area)?.href ?? `/programs/${program.slug}`;
          return (
            <DropdownMenuItem key={program.slug} asChild>
              <Link href={href} aria-current={selected ? "page" : undefined} className="min-h-11 gap-2">
                <span className="min-w-0 flex-1">{program.title}</span>
                {selected && <Check aria-hidden className="h-4 w-4 shrink-0 text-brand" />}
              </Link>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
