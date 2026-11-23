"use client";

import { ArrowLeft, History, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { listProgramChats } from "@/lib/api/programs";
import type { ChatPlace, Conversation } from "@/lib/api/types";
import { usePrograms } from "./use-programs";

/** Where a program chat lives: /programs/<slug>/month/<n> (or /qna), /interview for a mock interview. */
export function programChatPath(slug: string, month: number | null, mode: "tutor" | "interview"): string {
  const base = `/programs/${slug}/${month === null ? "qna" : `month/${month}`}`;
  return mode === "interview" ? `${base}/interview` : base;
}

/**
 * The bar above a program chat: the way back, the program, the month and its topic, New chat and this month's
 * History. Titles come from GET /v1/programs (the server's), never from the address.
 */
export function ProgramChatHeader({ place, conversationId }: { place: ChatPlace; conversationId: string | null }) {
  const { data } = usePrograms();
  const program = data?.programs.find((item) => item.slug === place.program);
  const month = place.month === null ? null : program?.months.find((item) => item.month === place.month);
  const [chats, setChats] = useState<Conversation[] | null>(null);

  useEffect(() => {
    let live = true;
    listProgramChats(place.program, place.month).then(
      (page) => live && setChats(page.items),
      () => live && setChats([]),
    );
    return () => {
      live = false;
    };
  }, [place.program, place.month, conversationId]);

  const back = place.month === null ? `/programs/${place.program}` : `/programs/${place.program}/modules`;
  const heading =
    place.month === null ? "Program Q&A" : `Month ${place.month}${month ? ` · ${month.title}` : ""}`;

  return (
    <header className="shrink-0 border-b border-border">
      <div className="chat-column flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
        <Link
          href={back}
          className="focus-ring inline-flex min-h-11 items-center gap-1.5 rounded-lg px-1 text-sm text-text-secondary hover:text-foreground"
        >
          <ArrowLeft aria-hidden className="h-4 w-4" />
          {place.month === null ? "Program" : "Course Module"}
        </Link>
        {/* At least 12rem, so on a phone New chat and History wrap to a second row instead of hiding the month. */}
        <div className="min-w-0 flex-1 basis-48">
          <p className="truncate text-xs text-text-secondary">
            {program?.title ?? "NH program"}
            {place.mode === "interview" ? " · Mock interview" : ""}
          </p>
          <h2 className="truncate text-sm font-semibold text-foreground">{heading}</h2>
        </div>
        <Link
          href={programChatPath(place.program, place.month, place.mode)}
          className="focus-ring inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2 text-sm font-semibold text-brand hover:bg-surface-2"
        >
          <Plus aria-hidden className="h-4 w-4" />
          New chat
        </Link>
        <details className="relative">
          <summary className="focus-ring inline-flex min-h-11 cursor-pointer list-none items-center gap-1.5 rounded-lg px-2 text-sm text-text-secondary hover:bg-surface-2 hover:text-foreground">
            <History aria-hidden className="h-4 w-4" />
            History
          </summary>
          <div className="absolute right-0 z-20 mt-1 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-border bg-background p-2 shadow-lg">
            {chats === null ? (
              <p className="p-2 text-sm text-text-secondary">Loading…</p>
            ) : chats.length === 0 ? (
              <p className="p-2 text-sm text-text-secondary">
                No chats yet in this {place.month === null ? "Q&A" : "month"}.
              </p>
            ) : (
              <ul className="max-h-80 overflow-y-auto" aria-label="Chats in this month">
                {chats.map((chat) => (
                  <li key={chat.id}>
                    <Link
                      href={`${programChatPath(place.program, place.month, chat.program?.mode ?? "tutor")}/c/${chat.id}`}
                      aria-current={chat.id === conversationId ? "page" : undefined}
                      className="focus-ring block truncate rounded-lg px-2 py-2 text-sm text-foreground hover:bg-surface-2 aria-[current=page]:bg-brand-soft aria-[current=page]:text-brand"
                    >
                      {chat.title}
                      {chat.program?.mode === "interview" ? " (mock interview)" : ""}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </details>
      </div>
    </header>
  );
}
