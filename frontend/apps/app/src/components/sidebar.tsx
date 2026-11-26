"use client";

import { ConfirmDialog } from "@v1/ui/confirm-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@v1/ui/dropdown-menu";
import { PromptDialog } from "@v1/ui/prompt-dialog";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@v1/ui/tooltip";
import { cn } from "@v1/ui/utils";
import { InlineAction } from "@/components/inline-action";
import { PROGRAM_AREAS, isExpanded, parseSaved, programRoute } from "@/features/programs/program-nav";
import { usePrograms } from "@/features/programs/use-programs";
import {
  Archive,
  Award,
  ChevronRight,
  ChevronsUpDown,
  FolderOpen,
  GraduationCap,
  LogOut,
  type LucideIcon,
  Pin,
  Plus,
  RotateCcw,
  Search,
  Settings,
  Timer,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactElement, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { toast } from "sonner";

import { useConversations, useStores } from "@/features/chat/chat-context";
import { ChatActionsMenu } from "@/features/conversations/chat-actions-menu";
import { pinnedChats } from "@/features/conversations/conversations-store";
import { ShareDialog } from "@/features/conversations/share-dialog";
import { forgetAppPath } from "@/features/navigation/app-return";
import { DEFAULT_SECTION } from "@/features/settings/sections";
import { describeError } from "@/lib/api/errors";
import type { Conversation } from "@/lib/api/types";
import { TITLE_MAX } from "@/lib/api/types";
import { APP_NAME } from "@/lib/brand";
import { BrandLockup, BrandMark } from "./brand/brand-mark";
import { LinkPending } from "./link-pending";
import { MarqueeTitle } from "./marquee-title";
import { hubItems, isActive } from "./nav-items";
import { usePreview } from "./preview-context";
import { useSignOut } from "./use-sign-out";
import { UserAvatar } from "./user-avatar";
import { useUser } from "./user-context";

/** Which conversation is on screen — from the URL only (no copy of it in state). */
export function activeConversationId(pathname: string | null): string | null {
  return /^\/c\/([0-9a-f-]{36})$/i.exec(pathname ?? "")?.[1] ?? null;
}

const COLLAPSE_KEY = "aichat.sidebar.collapsed";
const COLLAPSE_EVENT = "aichat:sidebar-collapse";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false; // storage unavailable: stay expanded
  }
}

function subscribeCollapsed(onChange: () => void): () => void {
  window.addEventListener(COLLAPSE_EVENT, onChange);
  return () => window.removeEventListener(COLLAPSE_EVENT, onChange);
}

/**
 * Expanded on the server and while hydrating (storage is unknown there), then the saved value.
 * Read synchronously on a client-side mount, so the rail doesn't flash open when the app shell
 * comes back after Settings.
 */
export function useCollapsedSidebar() {
  const collapsed = useSyncExternalStore(subscribeCollapsed, readCollapsed, () => false);
  const toggle = () => {
    try {
      window.localStorage.setItem(COLLAPSE_KEY, collapsed ? "0" : "1");
    } catch {
      // ignore
    }
    window.dispatchEvent(new Event(COLLAPSE_EVENT));
  };
  return { collapsed, toggle };
}

/** Where each copy of the sidebar was scrolled, kept while the app shell is away (Settings). */
const scrollMemory = new Map<string, number>();

/** In the collapsed rail a control is an icon only, so it names itself on hover and focus. */
function RailTip({ label, collapsed, children }: { label: string; collapsed: boolean; children: ReactElement }) {
  if (!collapsed) return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

/**
 * The brand, "New chat", the Tools (only when there are tools besides the chat), the
 * chat list ("Chats") and the account. Rendered in the desktop rail and inside the mobile drawer
 * (`onNavigate` closes the drawer). The collapse control lives in the header.
 */
/** A sidebar place (Skills & Certifications, File Library, Lab Activity): its page and everything under it. */
function PlaceLink({
  href,
  label,
  icon: Icon,
  collapsed,
  onNavigate,
}: {
  href: string;
  label: string;
  icon: LucideIcon;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const current = pathname === href || pathname?.startsWith(`${href}/`);
  return (
    <RailTip label={label} collapsed={collapsed}>
      <Link
        href={href}
        onClick={onNavigate}
        aria-current={current ? "page" : undefined}
        aria-label={collapsed ? label : undefined}
        className={cn(
          "focus-ring flex items-center gap-3 rounded-xl text-sm font-semibold transition-colors",
          collapsed ? "h-11 w-11 justify-center" : "w-full px-3 py-2.5",
          current ? "bg-surface-2 text-brand" : "text-text-secondary hover:bg-surface-2 hover:text-foreground",
        )}
      >
        <Icon aria-hidden className="h-5 w-5 shrink-0" />
        {!collapsed && label}
        {!collapsed && <LinkPending />}
      </Link>
    </RailTip>
  );
}

/** Which program groups are open, by slug (UI state only, features/programs/program-nav.ts `isExpanded`). */
const PROGRAMS_KEY = "netsecai.sidebar.programs";
const PROGRAMS_EVENT = "netsecai:sidebar-programs";

function readProgramGroups(): string | null {
  try {
    return window.localStorage.getItem(PROGRAMS_KEY); // a string: a stable snapshot for useSyncExternalStore
  } catch {
    return null; // storage unavailable: every group follows where you are
  }
}

function subscribeProgramGroups(onChange: () => void): () => void {
  window.addEventListener(PROGRAMS_EVENT, onChange);
  return () => window.removeEventListener(PROGRAMS_EVENT, onChange);
}

function saveProgramGroups(groups: Record<string, boolean>) {
  try {
    window.localStorage.setItem(PROGRAMS_KEY, JSON.stringify(groups));
  } catch {
    // ignore: the choice lasts until the page is left
  }
  window.dispatchEvent(new Event(PROGRAMS_EVENT));
}

/**
 * "My Programs": the NH programs this account's invoices open (GET /v1/programs; nothing while it loads or fails, and
 * nothing for anyone without one). Each program is a group that opens and closes on its own; a choice is remembered,
 * and without one the program you are in is open. Only the current area is highlighted (or the program's row, while its
 * group is closed). In the icon rail each program is a link to its page.
 */
function ProgramsNav({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const { data } = usePrograms();
  const pathname = usePathname();
  const raw = useSyncExternalStore(subscribeProgramGroups, readProgramGroups, () => null);
  const saved = useMemo(() => parseSaved(raw), [raw]);
  if (!data?.programs.length) return null;
  const route = programRoute(pathname);
  const toggle = (slug: string, open: boolean) => {
    const listed = new Set(data.programs.map((program) => program.slug)); // forget programs no longer listed
    saveProgramGroups({ ...Object.fromEntries(Object.entries(saved).filter(([key]) => listed.has(key))), [slug]: !open });
  };
  return (
    <nav
      aria-labelledby="programs-heading"
      className={cn("shrink-0 space-y-1", collapsed ? "flex flex-col items-center px-3 pb-3" : "px-3 pb-3")}
    >
      <h2
        id="programs-heading"
        className={cn("mb-2 px-3 text-2xs font-semibold uppercase tracking-[0.08em] text-text-secondary", collapsed && "sr-only")}
      >
        My Programs
      </h2>
      {data.programs.map((program) =>
        collapsed ? (
          <PlaceLink
            key={program.slug}
            href={`/programs/${program.slug}`}
            label={program.title}
            icon={GraduationCap}
            collapsed
            onNavigate={onNavigate}
          />
        ) : (
          <ProgramGroup
            key={program.slug}
            slug={program.slug}
            title={program.title}
            area={route?.slug === program.slug ? route.area : null}
            inside={route?.slug === program.slug}
            open={isExpanded(saved, program.slug, route?.slug ?? null)}
            onToggle={toggle}
            onNavigate={onNavigate}
          />
        ),
      )}
    </nav>
  );
}

function ProgramGroup({
  slug,
  title,
  area,
  inside,
  open,
  onToggle,
  onNavigate,
}: {
  slug: string;
  title: string;
  area: string | null;
  inside: boolean;
  open: boolean;
  onToggle: (slug: string, open: boolean) => void;
  onNavigate?: () => void;
}) {
  const id = useId();
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => onToggle(slug, open)}
        title={title}
        className={cn(
          "focus-ring flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-semibold transition-colors",
          inside && !open
            ? "bg-surface-2 text-brand"
            : inside
              ? "text-foreground hover:bg-surface-2"
              : "text-text-secondary hover:bg-surface-2 hover:text-foreground",
        )}
      >
        <GraduationCap aria-hidden className="h-5 w-5 shrink-0" />
        <span className="line-clamp-2 min-w-0 flex-1">{title}</span>
        <ChevronRight
          aria-hidden
          className={cn("h-4 w-4 shrink-0 transition-transform duration-fast ease-standard", open && "rotate-90")}
        />
      </button>
      {/* An accordion: the row's height follows its content (grid rows 0fr to 1fr), so what is below glides instead of
          jumping (a motion exception, globals.css). Closed, its links are inert: out of the tab order and unread. */}
      <div
        id={id}
        inert={!open || undefined}
        className={cn(
          "grid transition-[grid-template-rows,opacity] duration-base ease-standard",
          open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
        )}
      >
        <div className="min-h-0 overflow-hidden">
          <ul className="ml-5 mt-0.5 space-y-0.5 border-l border-border pb-1 pl-2">
            {PROGRAM_AREAS.map((item) => (
              <li key={item.path}>
                <Link
                  href={`/programs/${slug}/${item.path}`}
                  onClick={onNavigate}
                  aria-current={area === item.path ? "page" : undefined}
                  className={cn(
                    // Inset focus ring: the accordion clips anything drawn outside a link. 36px with a mouse, 44 on touch.
                    "focus-ring-inset flex min-h-9 items-center rounded-lg px-3 text-sm transition-colors [@media(pointer:coarse)]:min-h-11",
                    area === item.path
                      ? "bg-surface-2 font-semibold text-brand"
                      : "text-text-secondary hover:bg-surface-2 hover:text-foreground",
                  )}
                >
                  {item.label}
                  <LinkPending />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

export function SidebarContent({
  collapsed = false,
  onNavigate,
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { chat, conversations } = useStores();
  const list = useConversations();
  const { user } = useUser();
  const preview = usePreview();
  const hub = hubItems(preview);
  const active = activeConversationId(pathname);
  const [search, setSearch] = useState("");
  const [renaming, setRenaming] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [sharing, setSharing] = useState<Conversation | null>(null);
  // The row's "…" button that opened a dialog: focus goes back to it when the dialog closes (if the row is still there).
  const opener = useRef<HTMLElement | null>(null);
  const backToOpener = (event: Event) => {
    if (!opener.current?.isConnected) return;
    event.preventDefault();
    opener.current.focus();
  };
  const openWith =
    (open: (conversation: Conversation) => void) => (conversation: Conversation, from: HTMLElement | null) => {
      opener.current = from;
      open(conversation);
    };
  const { signOut, signingOut } = useSignOut();
  // The drawer is the phone layout, where Settings opens on its list of sections.
  const variant = onNavigate ? "drawer" : "rail";
  const settingsHref = variant === "drawer" ? "/settings" : `/settings/${DEFAULT_SECTION}`;
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    element.scrollTop = scrollMemory.get(variant) ?? 0;
    const remember = () => scrollMemory.set(variant, element.scrollTop);
    element.addEventListener("scroll", remember, { passive: true });
    return () => element.removeEventListener("scroll", remember);
  }, [variant]);

  // Pinned chats once, at the top; the rest below by activity. Search looks through both.
  const pins = useMemo(() => pinnedChats(list), [list]);
  const { visible, visiblePins } = useMemo(() => {
    const query = search.trim().toLowerCase();
    const matches = (item: Conversation) => !query || item.title.toLowerCase().includes(query);
    const pinnedIds = new Set(pins.map((item) => item.id));
    return {
      visiblePins: pins.filter(matches),
      visible: list.items.filter((item) => !pinnedIds.has(item.id) && matches(item)),
    };
  }, [list.items, pins, search]);
  const searchable = list.items.length + pins.length > 5;

  const row = (item: Conversation) => {
    const isActive = item.id === active;
    return (
      <li
        key={item.id}
        className={cn(
          "group flex items-center gap-1 rounded-xl transition-colors",
          isActive ? "bg-surface-2 font-medium text-foreground" : "text-foreground/85 hover:bg-surface-2 hover:text-foreground",
        )}
      >
        <Link
          href={`/c/${item.id}`}
          onClick={onNavigate}
          // Start reading the messages with the click, alongside the page itself, instead of
          // after it arrives (an idempotent read: chat-store ensureLoaded).
          onNavigate={() => void chat.ensureLoaded(item.id)}
          aria-current={isActive ? "page" : undefined}
          className="focus-ring-inset flex h-9 min-w-0 flex-1 items-center gap-2 rounded-xl px-3 text-sm [@media(pointer:coarse)]:h-11"
        >
          <span className="min-w-0 flex-1">
            <MarqueeTitle title={item.title} />
          </span>
          <LinkPending />
        </Link>
        {/* Always visible on touch and while its menu is open; on hover or focus with a pointer. */}
        <span className="flex shrink-0 items-center pr-1 opacity-100 transition-opacity has-[[data-state=open]]:opacity-100 md:[@media(hover:hover)_and_(pointer:fine)]:opacity-0 md:focus-within:opacity-100 md:group-hover:opacity-100">
          <ChatActionsMenu
            conversation={item}
            active={isActive}
            onShare={openWith(setSharing)}
            onRename={openWith(setRenaming)}
            onDelete={openWith(setDeleting)}
          />
        </span>
      </li>
    );
  };

  const rename = async (title: string) => {
    const target = renaming;
    setRenaming(null);
    if (!target) return;
    try {
      await conversations.rename(target.id, title.slice(0, TITLE_MAX));
    } catch (error) {
      toast.error(`Couldn't rename the chat. ${describeError(error).message}`);
    }
  };

  const remove = async () => {
    const target = deleting;
    setDeleting(null);
    if (!target) return;
    conversations.remove(target.id);
    forgetAppPath(`/c/${target.id}`);
    if (active === target.id) router.push("/");
    try {
      await chat.deleteConversation(target.id);
    } catch (error) {
      conversations.restore(target);
      toast.error(`Couldn't delete the chat. ${describeError(error).message}`);
    }
  };

  const name = user.displayName || user.email;

  return (
    <div
      className={cn(
        "rail flex h-full flex-col border-r border-border bg-surface transition-[width] duration-slow ease-standard",
        // The desktop rail's width is the aside's --sidebar-width (dragged, 200-420px), never more than 45% of the
        // window; while its edge is dragged it follows the pointer with no transition. The drawer keeps 18rem.
        collapsed
          ? "w-20"
          : "w-[var(--sidebar-width,18rem)] max-w-full md:max-w-[45vw] group-has-[[data-dragging]]/rail:transition-none",
      )}
    >
      {/* Outside the scrolling list: the brand stays in view, and a scrollbar never narrows the slogan's line.
          The header's height, so this hairline and the header's are one line. */}
      <div className={cn("relative flex h-header shrink-0 items-center", collapsed ? "justify-center" : "px-4")}>
        <Link href="/" onClick={onNavigate} className="focus-ring rounded-2xl">
          {collapsed ? (
            <>
              <BrandMark size={44} />
              <span className="sr-only">{APP_NAME}</span>
            </>
          ) : (
            <BrandLockup />
          )}
        </Link>
        {/* NH's signal line: the logo pill's red and blue, where the header's hairline meets the rail. */}
        <span aria-hidden className="signal-line absolute inset-x-0 bottom-0" />
      </div>

      <div ref={scroller} className="scrollbar-subtle flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain">

        <div className={cn("shrink-0 px-3 py-4", collapsed && "flex justify-center")}>
          <RailTip label="New chat" collapsed={collapsed}>
            <Link
              href="/"
              onClick={onNavigate}
              aria-label={collapsed ? "New chat" : undefined}
              className={cn(
                "focus-ring group flex items-center justify-center gap-2 rounded-xl bg-foreground font-semibold text-background transition hover:bg-foreground/90 active:scale-[0.98]",
                collapsed ? "h-11 w-11" : "w-full px-4 py-3 text-sm",
              )}
            >
              <Plus aria-hidden className="h-5 w-5" />
              {!collapsed && "New chat"}
            </Link>
          </RailTip>
        </div>

        {/* Places of their own, not tools: your certification practice, the files NetSec AI can read, your lab time. */}
        <div className={cn("shrink-0 space-y-1", collapsed ? "flex flex-col items-center px-3 pb-3" : "px-3 pb-3")}>
          <PlaceLink href="/learning" label="Skills & Certifications" icon={Award} collapsed={collapsed} onNavigate={onNavigate} />
          <PlaceLink href="/library" label="File Library" icon={FolderOpen} collapsed={collapsed} onNavigate={onNavigate} />
          <PlaceLink href="/study" label="Lab Activity" icon={Timer} collapsed={collapsed} onNavigate={onNavigate} />
        </div>

        <ProgramsNav collapsed={collapsed} onNavigate={onNavigate} />

        {hub.length > 0 && (
          <nav aria-labelledby="hub-heading" className={cn("shrink-0 space-y-1", collapsed ? "px-3" : "px-3")}>
            <h2
              id="hub-heading"
              className={cn(
                "mb-2 px-3 text-2xs font-semibold uppercase tracking-[0.08em] text-text-secondary",
                collapsed && "sr-only",
              )}
            >
              Tools
            </h2>
            {hub.map((item) => {
              const Icon = item.icon;
              const current = isActive(item, pathname);
              return (
                <RailTip key={item.id} label={item.label} collapsed={collapsed}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={current ? "page" : undefined}
                    aria-label={collapsed ? item.label : undefined}
                    className={cn(
                      "focus-ring relative flex items-center rounded-xl text-left transition-[color,background-color,box-shadow]",
                      collapsed ? "mx-auto h-11 w-11 justify-center" : "w-full gap-3 px-3 py-2.5",
                      current
                        ? "bg-surface-2 font-semibold text-foreground"
                        : "text-text-secondary hover:bg-surface-2 hover:text-foreground",
                    )}
                  >
                    <span
                      aria-hidden
                      className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg", current ? "bg-card text-brand" : item.accent)}
                    >
                      <Icon className="h-[18px] w-[18px]" />
                    </span>
                    {!collapsed && (
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium leading-tight">{item.label}</span>
                        <span className="block truncate text-2xs text-text-secondary">
                          {item.description}
                        </span>
                      </span>
                    )}
                    {current && !collapsed && <span aria-hidden className="h-6 w-1 shrink-0 rounded-full bg-brand" />}
                  </Link>
                </RailTip>
              );
            })}
          </nav>
        )}

        {collapsed ? (
          <div className="min-h-0 flex-1" />
        ) : (
          <nav aria-labelledby="chats-heading" className="mt-5 flex min-h-[10rem] flex-1 flex-col">
            <div className="flex items-center justify-between px-6 pb-2">
              <h2 id="chats-heading" className="text-2xs font-semibold uppercase tracking-[0.08em] text-text-secondary">
                Chats
              </h2>
            </div>
            {searchable && (
              <div className="relative mx-3 mb-2">
                <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-secondary" />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search chats"
                  aria-label="Search chats"
                  className="focus-field h-9 w-full rounded-xl border border-input bg-card pl-9 pr-2 text-sm transition-colors placeholder:text-text-secondary"
                />
              </div>
            )}
            <div className="px-3 pb-3">
              {list.status === "loading" && list.items.length === 0 ? (
                <SkeletonGroup label="Loading chats" className="space-y-1.5 pt-1">
                  {[0, 1, 2, 3].map((row) => (
                    <Skeleton key={row} className="h-9 w-full rounded-xl" />
                  ))}
                </SkeletonGroup>
              ) : list.status === "error" && list.items.length === 0 ? (
                <div className="px-3 py-3 text-sm text-text-secondary">
                  <p>Couldn't load your chats.</p>
                  <InlineAction icon={RotateCcw} onClick={() => void conversations.refresh()} className="-ml-2 mt-1">
                    Try again
                  </InlineAction>
                </div>
              ) : (
                <>
                  {/* Pinned: its own small group at the top, only when something is pinned. */}
                  {visiblePins.length > 0 && (
                    <div className="mb-3">
                      <h3 className="flex items-center gap-1.5 px-3 pb-1 text-2xs font-semibold uppercase tracking-[0.08em] text-text-secondary">
                        <Pin aria-hidden className="h-3 w-3" />
                        Pinned
                      </h3>
                      <ul aria-label="Pinned chats" className="space-y-0.5">
                        {visiblePins.map(row)}
                      </ul>
                    </div>
                  )}
                  {visible.length === 0 ? (
                    visiblePins.length === 0 && (
                      <p className="px-3 py-3 text-sm text-text-secondary">
                        {list.items.length + pins.length === 0 ? "Your chats will appear here." : "No chat matches that."}
                      </p>
                    )
                  ) : (
                    <ul className="space-y-0.5">{visible.map(row)}</ul>
                  )}
                </>
              )}
              {list.nextCursor && !search && (
                <button
                  type="button"
                  onClick={() => void conversations.loadMore().catch((error) => toast.error(describeError(error).message))}
                  disabled={list.loadingMore}
                  className="focus-ring mt-2 w-full rounded-xl px-3 py-2 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-2 hover:text-foreground disabled:opacity-50"
                >
                  {list.loadingMore ? "Loading…" : "Load more"}
                </button>
              )}
            </div>
          </nav>
        )}
      </div>

      {/* Only the account here: Settings and Log out are in its menu. */}
      <div className="shrink-0 border-t border-border bg-surface p-2">
        <DropdownMenu modal={false}>
          <RailTip label={name} collapsed={collapsed}>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={`Account: ${name}`}
                className={cn(
                  "focus-ring flex w-full items-center gap-3 rounded-xl p-1.5 text-left transition-colors hover:bg-surface-2 data-[state=open]:bg-surface-2",
                  collapsed && "justify-center",
                )}
              >
                <UserAvatar user={user} />
                {!collapsed && (
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-foreground">{name}</span>
                    {user.displayName && <span className="block truncate text-xs text-text-secondary">{user.email}</span>}
                  </span>
                )}
                {!collapsed && <ChevronsUpDown aria-hidden className="h-4 w-4 shrink-0 text-text-secondary" />}
              </button>
            </DropdownMenuTrigger>
          </RailTip>
          <DropdownMenuContent
            side="top"
            align="start"
            sideOffset={6}
            collisionPadding={8}
            className={cn("rail min-w-[12rem] p-1", !collapsed && "w-[var(--radix-dropdown-menu-trigger-width)]")}
          >
            <DropdownMenuItem asChild className="gap-2.5 font-medium">
              <Link href={settingsHref} onClick={onNavigate}>
                <Settings aria-hidden className="h-4 w-4 text-text-secondary" />
                Settings
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild className="gap-2.5 font-medium">
              <Link href="/archived" onClick={onNavigate}>
                <Archive aria-hidden className="h-4 w-4 text-text-secondary" />
                Archived chats
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => void signOut()}
              disabled={signingOut}
              className="gap-2.5 font-medium text-destructive focus:bg-destructive/10 focus:text-destructive"
            >
              <LogOut aria-hidden className="h-4 w-4" />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <PromptDialog
        open={renaming !== null}
        onOpenChange={(open) => !open && setRenaming(null)}
        title="Rename chat"
        label="Name"
        initialValue={renaming?.title ?? ""}
        maxLength={TITLE_MAX}
        submitLabel="Rename"
        onSubmit={rename}
        onCloseAutoFocus={backToOpener}
      />
      <ShareDialog
        conversation={sharing}
        onOpenChange={(open) => !open && setSharing(null)}
        onCloseAutoFocus={backToOpener}
      />
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete this chat?"
        description={
          <>
            <span className="font-medium text-foreground">“{deleting?.title}”</span> and all its messages will be
            permanently deleted.
          </>
        }
        confirmLabel="Delete"
        destructive
        onConfirm={remove}
        onCloseAutoFocus={backToOpener}
      />
    </div>
  );
}
