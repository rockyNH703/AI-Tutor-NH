"use client";

import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@v1/ui/sheet";
import { cn } from "@v1/ui/utils";
import { Menu, PanelLeftClose, PanelLeftOpen, Plus, Share2, Trophy } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type CSSProperties, type ReactNode, type RefObject, useEffect, useRef, useState } from "react";

import { useAppearance } from "@/components/appearance-provider";
import { useConversations } from "@/features/chat/chat-context";
import { pinnedChats } from "@/features/conversations/conversations-store";
import { ShareDialog } from "@/features/conversations/share-dialog";
import type { Conversation } from "@/lib/api/types";
import { rememberAppPath } from "@/features/navigation/app-return";
import { useFocusOnArrival } from "@/features/navigation/route-focus";
import { programRoute } from "@/features/programs/program-nav";
import { usePrograms } from "@/features/programs/use-programs";
import { NotificationBell } from "@/features/notifications/notification-bell";
import { CelebrationHost } from "@/features/streak/celebrations/celebration-host";
import { DayCountdown, StreakBadge, dayCountdownVisible, streakBadgeVisible } from "@/features/streak/header-streak";
import { useStreak } from "@/features/streak/streak-store";
import { StudyControl, studyControlVisible } from "@/features/study/study-control";
import { useStudy } from "@/features/study/study-store";
import { APP_NAME } from "@/lib/brand";
import { navItemFor } from "./nav-items";
import { PreviewBadge } from "./preview-badge";
import { usePreview } from "./preview-context";
import { SidebarContent, activeConversationId, useCollapsedSidebar } from "./sidebar";
import { SidebarResizer } from "./sidebar-resizer";
import { saveSidebarWidth } from "./sidebar-width";
import { ThemeMenu } from "./theme-menu";

/**
 * Sidebar | (header + page). A flex layout the height of the dynamic viewport,
 * so the chat's composer sits at the bottom on every breakpoint without the old
 * hand-maintained `calc(100dvh - chrome)` arithmetic.
 */
export function AppShell({ children, sidebarWidth }: { children: ReactNode; sidebarWidth: number }) {
  const { collapsed, toggle } = useCollapsedSidebar();
  // The rail's width, from the server's cookie on the first paint; dragging its edge changes it.
  const [width, setWidth] = useState(sidebarWidth);
  const rail = useRef<HTMLElement>(null);
  const commitWidth = (next: number) => {
    rail.current?.style.setProperty("--sidebar-width", `${next}px`);
    setWidth(next);
    saveSidebarWidth(next);
  };
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname();
  const heading = useRef<HTMLHeadingElement>(null);

  // Where "Close settings" comes back to (features/navigation/app-return.ts).
  useEffect(() => {
    if (pathname) rememberAppPath(pathname);
  }, [pathname]);
  // Back from Settings: the chat focuses its message box itself; any other page, its title.
  useFocusOnArrival(() => heading.current);

  return (
    // The page's own ground (body, globals.css) shows through: one background, not two.
    <div className="app-frame">
      {/* Desktop rail. Not rendered below md (the drawer below is the only copy there). Its width is a CSS variable,
          so a drag repaints the rail without re-rendering it; folded to icons, it has no edge to drag. */}
      <aside
        ref={rail}
        id="app-sidebar"
        className="group/rail relative hidden h-full shrink-0 md:block"
        style={{ "--sidebar-width": `${width}px` } as CSSProperties}
      >
        <SidebarContent collapsed={collapsed} />
        {!collapsed && <SidebarResizer target={rail} controls="app-sidebar" width={width} onCommit={commitWidth} />}
      </aside>

      {/* The drawer's own trigger is the header's menu button, so closing the
          drawer gives focus back to it. The Sheet root renders no element. */}
      <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
        <SheetContent side="left" aria-describedby={undefined} className="md:hidden">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SidebarContent onNavigate={() => setMenuOpen(false)} />
        </SheetContent>

        <div className="flex min-w-0 flex-1 flex-col">
          <Header collapsed={collapsed} onToggleCollapse={toggle} headingRef={heading} />
          <main id="main" tabIndex={-1} className="min-h-0 flex-1 outline-none">
            {children}
          </main>
        </div>
      </Sheet>
      {/* Practice streak celebrations, from the server's events only (features/streak/celebrations). */}
      <CelebrationHost />
    </div>
  );
}


/** On an ordinary chat's page: share it (the same dialog as its sidebar menu). Not on a new or a program chat. */
function ShareChatButton() {
  const pathname = usePathname();
  const list = useConversations();
  const [open, setOpen] = useState<Conversation | null>(null);
  const id = activeConversationId(pathname);
  const conversation = id ? (list.items.find((item) => item.id === id) ?? pinnedChats(list).find((item) => item.id === id)) : undefined;
  if (!conversation || conversation.program) return null;
  return (
    <>
      {/* From 640px: below that the header has no room left for the title (Share is in the chat's "…" menu). */}
      <button
        type="button"
        onClick={() => setOpen(conversation)}
        aria-label="Share chat"
        title="Share chat"
        className="icon-button hidden sm:grid"
      >
        <Share2 aria-hidden className="h-[18px] w-[18px]" />
      </button>
      <ShareDialog conversation={open} onOpenChange={(next) => !next && setOpen(null)} />
    </>
  );
}

/** What the page is (the `h1`), a line about it, and the sidebar controls. */
function useHeading(pathname: string | null): { title: string; subtitle: string } {
  const { items } = useConversations();
  const programs = usePrograms(); // the sidebar's list, already read: no extra request
  if (pathname === "/help") return { title: "Help", subtitle: "Answers to common questions" };
  if (pathname === "/about") return { title: "About", subtitle: `What ${APP_NAME} is and how it uses your data` };
  if (pathname === "/library") return { title: "File Library", subtitle: "Files you've added for NetSec AI to read" };
  if (pathname === "/knowledge") return { title: "Knowledge Library", subtitle: "Owner-reviewed sources and revisions" };
  if (pathname === "/study") return { title: "Lab Activity", subtitle: "Time spent practising in labs and study sessions" };
  if (pathname === "/learning") return { title: "Skills & Certifications", subtitle: "Your certification practice, topic mastery and progress" };
  if (pathname === "/archived") return { title: "Archived chats", subtitle: "Chats you took out of the sidebar" };
  // A program's pages and chats: the program's own name (each section names itself below the bar).
  const route = programRoute(pathname);
  if (route) return { title: programs.data?.programs.find((item) => item.slug === route.slug)?.title ?? "My Programs", subtitle: "" };
  const item = navItemFor(pathname);
  if (item?.id === "tutor") {
    // The chat's own name (named after its first answered question), "New chat" until then.
    const active = activeConversationId(pathname);
    const title = active ? (items.find((conversation) => conversation.id === active)?.title ?? "Chat") : "New chat";
    return { title, subtitle: "" };
  }
  if (item) return { title: item.label, subtitle: item.description };
  return { title: APP_NAME, subtitle: "" };
}

function Header({
  collapsed,
  onToggleCollapse,
  headingRef,
}: {
  collapsed: boolean;
  onToggleCollapse: () => void;
  headingRef: RefObject<HTMLHeadingElement | null>;
}) {
  const pathname = usePathname();
  const preview = usePreview();
  const appearance = useAppearance();
  const streak = useStreak();
  const study = useStudy();
  const showStudy = studyControlVisible(study, pathname);
  const showStreak = streakBadgeVisible(appearance);
  const showCountdown = dayCountdownVisible(streak);
  const showCapsule = showStudy || showStreak || showCountdown;
  const showCapsuleOnMobile = showStudy || showStreak;
  const { title, subtitle } = useHeading(pathname);

  return (
    <header className="z-10 flex h-header shrink-0 items-center gap-2 border-b border-border bg-background px-2 sm:gap-3 sm:px-4">
      <SheetTrigger asChild>
        <button type="button" aria-label="Open menu" className="icon-button md:hidden">
          <Menu aria-hidden className="h-5 w-5" />
        </button>
      </SheetTrigger>
      <button
        type="button"
        onClick={onToggleCollapse}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className="icon-button hidden md:grid"
      >
        {collapsed ? <PanelLeftOpen aria-hidden className="h-5 w-5" /> : <PanelLeftClose aria-hidden className="h-5 w-5" />}
      </button>

      <div className="min-w-0 flex-1 px-1">
        <h1 ref={headingRef} tabIndex={-1} className="truncate text-base font-semibold text-foreground outline-none">
          {title}
        </h1>
        {subtitle && <p className="hidden truncate text-xs text-text-secondary sm:block">{subtitle}</p>}
      </div>

      {preview && (
        <div className="flex shrink-0 items-center gap-2">
          <PreviewBadge className="hidden lg:inline-flex" />
          <span className="hidden items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-xs font-semibold text-text-secondary lg:flex">
            <span aria-hidden className="h-2 w-2 rounded-full bg-success motion-safe:animate-pulse" />
            Preview features
          </span>
          <button
            type="button"
            disabled
            className="flex items-center gap-1.5 rounded-xl bg-brand-soft px-3 py-2 text-xs font-bold text-brand disabled:cursor-not-allowed"
          >
            <Trophy aria-hidden className="h-4 w-4 text-streak" />
            <span className="hidden sm:inline">Leaderboard</span>
            <span className="sr-only sm:hidden">Leaderboard</span>
            <PreviewBadge label="Soon" />
          </button>
        </div>
      )}

      <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
        {showCapsule && (
          <div
            className={cn(
              "shrink-0 items-center gap-0.5 rounded-full border border-border bg-card p-0.5 sm:gap-1",
              showCapsuleOnMobile ? "flex" : "hidden sm:flex",
            )}
          >
            <StudyControl />
            <StreakBadge />
            <DayCountdown className="hidden sm:inline-flex" />
          </div>
        )}
        <ShareChatButton />
        <NotificationBell />
        <ThemeMenu />
      </div>

      <Link href="/" aria-label="New chat" className="icon-button md:hidden">
        <Plus aria-hidden className="h-5 w-5" />
      </Link>
    </header>
  );
}
