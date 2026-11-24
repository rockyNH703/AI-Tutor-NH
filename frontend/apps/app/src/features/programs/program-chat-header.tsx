"use client";

import { Button } from "@v1/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@v1/ui/dropdown-menu";
import { ArrowLeft, Check, History, Plus } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { listProgramChats } from "@/lib/api/programs";
import type { ChatPlace, Conversation } from "@/lib/api/types";
import { relativeTime } from "@/lib/time";
import { placeText, programChatPath } from "./program-nav";
import { usePrograms } from "./use-programs";

/**
 * The bar above a program chat, one row on every screen: the way back (a month's tutor to Course Module, its mock
 * interview to Mock Interviews; Q&A is a section of its own), the place (a "Month 2" chip and the month's topic, from
 * GET /v1/programs, never from the address), New chat and this place's History.
 */
export function ProgramChatHeader({ place, conversationId }: { place: ChatPlace; conversationId: string | null }) {
  const { data } = usePrograms();
  const text = placeText(place, data?.programs.find((item) => item.slug === place.program) ?? null);
  return (
    <header className="shrink-0 border-b border-border">
      <div className="chat-column flex h-14 items-center gap-1.5 sm:gap-2">
        {text.back && (
          <Link
            href={text.back.href}
            aria-label={`Back to ${text.back.label}`}
            className="focus-ring inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-lg px-2 text-sm text-text-secondary transition-colors hover:bg-surface-2 hover:text-foreground"
          >
            <ArrowLeft aria-hidden className="h-4 w-4" />
            <span className="hidden sm:inline">{text.back.label}</span>
          </Link>
        )}
        <h2
          className="flex min-w-0 flex-1 items-center gap-2 text-sm font-semibold text-foreground"
          title={text.label ? `${text.label}: ${text.title}` : text.title}
        >
          {text.label && (
            <span className="shrink-0 rounded-md bg-brand-soft px-1.5 py-0.5 text-xs font-semibold text-brand">{text.label}</span>
          )}
          <span className="truncate">{text.title}</span>
        </h2>
        <Button asChild variant="outline" size="sm" className="min-h-11 min-w-11 shrink-0 gap-1.5">
          <Link href={programChatPath(place.program, place.month, place.mode)} aria-label="New chat">
            <Plus aria-hidden className="h-4 w-4" />
            <span className="hidden sm:inline">New chat</span>
          </Link>
        </Button>
        <ChatHistory place={place} conversationId={conversationId} />
      </div>
    </header>
  );
}

/**
 * This place's chats, newest first: only its own kind (a month's tutor chats, or its mock interviews), read each time
 * the menu opens (the last list shows meanwhile). A menu (Radix): arrow keys, Escape and a click outside close it,
 * focus goes back to History, and picking a chat closes it.
 */
function ChatHistory({ place, conversationId }: { place: ChatPlace; conversationId: string | null }) {
  const [chats, setChats] = useState<Conversation[] | null>(null);
  const [failed, setFailed] = useState(false);
  const request = useRef(0);
  const load = () => {
    const id = ++request.current; // only the latest read lands
    setFailed(false);
    listProgramChats(place.program, place.month).then(
      (page) => id === request.current && setChats(page.items.filter((chat) => (chat.program?.mode ?? "tutor") === place.mode)),
      () => id === request.current && setFailed(true),
    );
  };
  const base = programChatPath(place.program, place.month, place.mode);
  return (
    <DropdownMenu onOpenChange={(open) => open && load()}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="min-h-11 min-w-11 shrink-0 gap-1.5" aria-label="History">
          <History aria-hidden className="h-4 w-4" />
          <span className="hidden sm:inline">History</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 max-w-[calc(100vw-2rem)] p-0">
        {/* The house menu clips its content: the list scrolls inside it. */}
        <div className="scrollbar-subtle max-h-[min(24rem,60dvh)] overflow-y-auto p-1.5">
          {failed ? (
            <>
              <p className="px-2.5 py-2 text-sm text-text-secondary">Your chats couldn't be loaded.</p>
              <DropdownMenuItem
                onSelect={(event) => {
                  event.preventDefault(); // stay open while it reads again
                  load();
                }}
                className="font-medium text-brand"
              >
                Try again
              </DropdownMenuItem>
            </>
          ) : chats === null ? (
            <p role="status" className="px-2.5 py-2 text-sm text-text-secondary">
              Loading…
            </p>
          ) : chats.length === 0 ? (
            <p className="px-2.5 py-2 text-sm text-text-secondary">No chats here yet.</p>
          ) : (
            chats.map((chat) => {
              const current = chat.id === conversationId;
              return (
                <DropdownMenuItem key={chat.id} asChild>
                  <Link href={`${base}/c/${chat.id}`} aria-current={current ? "page" : undefined} className="gap-2">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-foreground">{chat.title}</span>
                      <span className="block text-xs text-text-secondary">{relativeTime(chat.updatedAt)}</span>
                    </span>
                    {current && <Check aria-hidden className="h-4 w-4 shrink-0 text-brand" />}
                  </Link>
                </DropdownMenuItem>
              );
            })
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
