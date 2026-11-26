"use client";

import { Button } from "@v1/ui/button";
import { ConfirmDialog } from "@v1/ui/confirm-dialog";
import { EmptyState, ErrorState } from "@v1/ui/empty-state";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { Archive, ArchiveRestore, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useStores } from "@/features/chat/chat-context";
import { forgetAppPath } from "@/features/navigation/app-return";
import { deleteConversation, listConversations, updateConversation } from "@/lib/api/conversations";
import { describeError } from "@/lib/api/errors";
import type { Conversation } from "@/lib/api/types";
import { shortDate } from "@/lib/time";

const PAGE = 50;

/**
 * Archived chats (GET /v1/conversations?view=archived): out of the sidebar, kept with every message and file. Each can
 * be opened, restored to the sidebar, or deleted for good after a confirmation. Newest archive first, paged.
 */
export function ArchivedView() {
  const { conversations } = useStores();
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [query, setQuery] = useState("");
  const region = useRef<HTMLDivElement>(null);
  // The place of a row that just left (restored or deleted): focus goes to the row now there, or the page.
  const [refocus, setRefocus] = useState<number | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await listConversations({ view: "archived", limit: PAGE });
      setItems(page.items);
      setNext(page.nextCursor);
    } catch (failure) {
      setError(failure);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const more = async () => {
    if (!next) return;
    setLoadingMore(true);
    try {
      const page = await listConversations({ view: "archived", limit: PAGE, cursor: next });
      setItems((list) => {
        const known = new Set((list ?? []).map((item) => item.id));
        return [...(list ?? []), ...page.items.filter((item) => !known.has(item.id))];
      });
      setNext(page.nextCursor);
    } catch (failure) {
      toast.error(describeError(failure).message);
    } finally {
      setLoadingMore(false);
    }
  };

  const restore = async (conversation: Conversation) => {
    setBusy(conversation.id);
    try {
      const saved = await updateConversation(conversation.id, { archived: false });
      setRefocus(Math.max(0, shown.findIndex((item) => item.id === conversation.id)));
      setItems((list) => list?.filter((item) => item.id !== conversation.id) ?? null);
      conversations.unarchived(saved);
      toast.success(`“${conversation.title}” is back in your chats.`);
    } catch (failure) {
      toast.error(`Couldn't restore the chat. ${describeError(failure).message}`);
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    const target = deleting;
    setDeleting(null);
    if (!target) return;
    setBusy(target.id);
    try {
      await deleteConversation(target.id);
      forgetAppPath(`/c/${target.id}`);
      setRefocus(Math.max(0, shown.findIndex((item) => item.id === target.id)));
      setItems((list) => list?.filter((item) => item.id !== target.id) ?? null);
      toast.success("Chat deleted.");
    } catch (failure) {
      toast.error(`Couldn't delete the chat. ${describeError(failure).message}`);
    } finally {
      setBusy(null);
    }
  };

  const shown = useMemo(() => {
    const words = query.trim().toLowerCase();
    return words ? (items ?? []).filter((item) => item.title.toLowerCase().includes(words)) : (items ?? []);
  }, [items, query]);

  useEffect(() => {
    if (refocus === null) return;
    setRefocus(null);
    const restores = region.current?.querySelectorAll<HTMLButtonElement>("ul button[aria-label^='Restore']") ?? [];
    const target = restores[Math.min(refocus, restores.length - 1)];
    (target ?? region.current)?.focus();
  }, [refocus]);

  return (
    <div
      ref={region}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-focusable to be scrollable
      tabIndex={0}
      role="region"
      aria-label="Archived chats"
      className="scrollbar-subtle focus-ring-inset h-full overflow-y-auto"
    >
      <div className="page-container flex flex-col gap-5">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="font-display text-2xl font-bold tracking-tight text-foreground">Archived chats</h2>
            <p className="mt-1 text-sm text-text-secondary">Out of your sidebar, kept with every message. Restore or delete them here.</p>
          </div>
          {items && items.length > 5 && (
            <div className="relative w-full sm:w-64">
              <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-secondary" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search archived chats"
                aria-label="Search archived chats"
                className="focus-field h-10 w-full rounded-xl border border-input bg-card pl-9 pr-2 text-sm placeholder:text-text-secondary"
              />
            </div>
          )}
        </header>

        {error ? (
          <ErrorState
            title="Couldn't load your archived chats"
            description={describeError(error).message}
            action={
              <Button variant="outline" onClick={() => void load()}>
                Try again
              </Button>
            }
          />
        ) : items === null ? (
          <SkeletonGroup label="Loading archived chats" className="flex flex-col gap-2">
            {[0, 1, 2].map((key) => (
              <Skeleton key={key} className="h-16 w-full rounded-xl" />
            ))}
          </SkeletonGroup>
        ) : items.length === 0 ? (
          <EmptyState
            icon={Archive}
            title="No archived chats"
            description="Archive a chat from its … menu in the sidebar: it leaves the list but keeps every message."
            action={null}
          />
        ) : shown.length === 0 ? (
          <p className="text-sm text-text-secondary">No archived chat matches that.</p>
        ) : (
          <ul aria-label="Archived chats" className="flex flex-col divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card elev-1">
            {shown.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-3 px-4 py-3 sm:flex-nowrap">
                <Link href={`/c/${item.id}`} className="focus-ring min-w-0 flex-1 rounded-lg">
                  <span className="block truncate text-sm font-semibold text-foreground">{item.title}</span>
                  <span className="block text-xs text-text-secondary">
                    Archived {item.archivedAt ? shortDate(item.archivedAt) : ""} · last active {shortDate(item.updatedAt)}
                  </span>
                </Link>
                <div className="flex shrink-0 gap-1.5">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="gap-1.5"
                    disabled={busy === item.id}
                    onClick={() => void restore(item)}
                    aria-label={`Restore “${item.title}”`}
                  >
                    <ArchiveRestore aria-hidden className="h-4 w-4" />
                    Restore
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="gap-1.5 text-destructive hover:bg-destructive/10 hover:text-destructive"
                    disabled={busy === item.id}
                    onClick={() => setDeleting(item)}
                    aria-label={`Delete “${item.title}”`}
                  >
                    <Trash2 aria-hidden className="h-4 w-4" />
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {next && !query && (
          <Button type="button" variant="ghost" className="self-center" disabled={loadingMore} onClick={() => void more()}>
            {loadingMore ? "Loading…" : "Load more"}
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete this chat for good?"
        description={
          <>
            <span className="font-medium text-foreground">“{deleting?.title}”</span> and all its messages will be permanently
            deleted. A shared link to it stops working.
          </>
        }
        confirmLabel="Delete"
        destructive
        onConfirm={() => void remove()}
      />
    </div>
  );
}
