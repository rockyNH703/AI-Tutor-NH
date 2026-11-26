"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@v1/ui/dropdown-menu";
import { cn } from "@v1/ui/utils";
import { Archive, MoreHorizontal, Pencil, Pin, PinOff, Share2, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef } from "react";
import { toast } from "sonner";

import { useStores } from "@/features/chat/chat-context";
import { describeError } from "@/lib/api/errors";
import type { Conversation } from "@/lib/api/types";

/**
 * A chat's actions, behind one "…" button on its row (ChatGPT's pattern): share, rename, pin or unpin, archive and,
 * set apart, delete. Pin, unpin and archive happen at once (optimistic, features/conversations) with a short toast;
 * share, rename and delete open their dialogs, which the sidebar owns. They get this "…" button too: the menu item that
 * opened a dialog is gone by the time it closes, so the dialog gives focus back to the button instead.
 */
export function ChatActionsMenu({
  conversation,
  active = false,
  onShare,
  onRename,
  onDelete,
  className,
}: {
  conversation: Conversation;
  /** The chat open on screen: archiving it goes on to a new chat, like deleting it does. */
  active?: boolean;
  onShare: (conversation: Conversation, opener: HTMLElement | null) => void;
  onRename: (conversation: Conversation, opener: HTMLElement | null) => void;
  onDelete: (conversation: Conversation, opener: HTMLElement | null) => void;
  className?: string;
}) {
  const { conversations } = useStores();
  const router = useRouter();
  const pinned = Boolean(conversation.pinnedAt);
  const trigger = useRef<HTMLButtonElement>(null);

  const togglePin = () =>
    void conversations.setPinned(conversation, !pinned).then(
      () => toast.success(pinned ? "Unpinned." : "Pinned to the top."),
      (error) => toast.error(`Couldn't ${pinned ? "unpin" : "pin"} the chat. ${describeError(error).message}`),
    );
  const archive = () => {
    void conversations.archive(conversation).then(
      () => toast.success("Chat archived. Find it in Archived chats."),
      (error) => toast.error(`Couldn't archive the chat. ${describeError(error).message}`),
    );
    if (active) router.push("/");
  };

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          ref={trigger}
          type="button"
          aria-label={`Options for “${conversation.title}”`}
          className={cn(
            "focus-ring-inset grid h-7 w-7 place-items-center rounded-lg transition-colors hover:bg-foreground/10 data-[state=open]:bg-foreground/10 [@media(pointer:coarse)]:h-10 [@media(pointer:coarse)]:w-10",
            className,
          )}
        >
          <MoreHorizontal aria-hidden className="h-4 w-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="right" sideOffset={6} collisionPadding={8} className="min-w-[11rem] p-1">
        <DropdownMenuItem onSelect={() => onShare(conversation, trigger.current)} className="gap-2.5">
          <Share2 aria-hidden className="h-4 w-4 text-text-secondary" />
          Share
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onRename(conversation, trigger.current)} className="gap-2.5">
          <Pencil aria-hidden className="h-4 w-4 text-text-secondary" />
          Rename
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={togglePin} className="gap-2.5">
          {pinned ? (
            <PinOff aria-hidden className="h-4 w-4 text-text-secondary" />
          ) : (
            <Pin aria-hidden className="h-4 w-4 text-text-secondary" />
          )}
          {pinned ? "Unpin" : "Pin"}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={archive} className="gap-2.5">
          <Archive aria-hidden className="h-4 w-4 text-text-secondary" />
          Archive
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => onDelete(conversation, trigger.current)}
          className="gap-2.5 text-destructive focus:bg-destructive/10 focus:text-destructive"
        >
          <Trash2 aria-hidden className="h-4 w-4" />
          Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
