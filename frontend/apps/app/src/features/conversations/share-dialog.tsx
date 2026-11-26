"use client";

import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { Input } from "@v1/ui/input";
import { Skeleton } from "@v1/ui/skeleton";
import { Check, Copy, Link2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { createShare, getShare, revokeShare } from "@/lib/api/conversations";
import { describeError } from "@/lib/api/errors";
import type { Conversation, ShareStatus } from "@/lib/api/types";
import { shortDate } from "@/lib/time";

type State =
  | { step: "loading" }
  | { step: "failed"; message: string }
  | { step: "off" }
  | { step: "on"; status: ShareStatus }
  | { step: "made"; url: string; messageCount: number };

/** The address a recipient opens (this origin; the page reads the snapshot on the server). */
function shareUrl(token: string): string {
  return `${window.location.origin}/share/${token}`;
}

/**
 * Share a chat (app/conversations/shares.py): a read-only SNAPSHOT of it as it is now, behind an unguessable link.
 * Nothing is shared until "Create link"; the link is shown once, when it is made (only its hash is kept), so a chat
 * shared earlier offers "Update link" (a new snapshot and link; the old link stops) and "Stop sharing".
 */
export function ShareDialog({
  conversation,
  onOpenChange,
  onCloseAutoFocus,
}: {
  conversation: Conversation | null;
  onOpenChange: (open: boolean) => void;
  /** Where focus goes on close when the opener is gone (call `event.preventDefault()`, then focus). */
  onCloseAutoFocus?: (event: Event) => void;
}) {
  const [state, setState] = useState<State>({ step: "loading" });
  const [pending, setPending] = useState<"make" | "stop" | null>(null);
  const [copied, setCopied] = useState(false);
  const fieldId = useId();
  const id = conversation?.id ?? null;
  // The chat this dialog is about now: an answer for another one (closed, or reopened on another chat) is dropped.
  const current = useRef<string | null>(null);
  // The last title shown, so the description keeps it while the dialog closes.
  const [title, setTitle] = useState(conversation?.title ?? "");
  if (conversation && conversation.title !== title) setTitle(conversation.title);

  useEffect(() => {
    current.current = id;
    if (!id) return;
    let live = true;
    setState({ step: "loading" });
    setPending(null);
    setCopied(false);
    getShare(id).then(
      (status) => live && setState(status.shared ? { step: "on", status } : { step: "off" }),
      (error) => live && setState({ step: "failed", message: describeError(error).message }),
    );
    return () => {
      live = false;
    };
  }, [id]);

  const make = async () => {
    if (!id) return;
    setPending("make");
    try {
      const made = await createShare(id);
      if (current.current !== id) return;
      setCopied(false);
      setState({ step: "made", url: shareUrl(made.token), messageCount: made.messageCount });
    } catch (error) {
      toast.error(`Couldn't create the link. ${describeError(error).message}`);
    } finally {
      if (current.current === id) setPending(null);
    }
  };

  const stop = async () => {
    if (!id) return;
    setPending("stop");
    try {
      await revokeShare(id);
      if (current.current === id) setState({ step: "off" });
      toast.success("Sharing stopped. The link no longer works.");
    } catch (error) {
      toast.error(`Couldn't stop sharing. ${describeError(error).message}`);
    } finally {
      if (current.current === id) setPending(null);
    }
  };

  const copy = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      toast.success("Link copied.");
    } catch {
      toast.error("Couldn't copy. Select the link and copy it yourself.");
    }
  };

  return (
    <Dialog open={conversation !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" onCloseAutoFocus={onCloseAutoFocus}>
        <DialogHeader>
          <DialogTitle>Share chat</DialogTitle>
          <DialogDescription>
            Anyone with the link can read “{title}” as it is now, including anything pasted into it (a config
            with keys, for example). Your files and account aren't shared, and messages you send later aren't added.
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-[5.5rem] py-2" aria-live="polite">
          {state.step === "loading" && (
            <div role="status" className="flex flex-col gap-2">
              <span className="sr-only">Checking whether this chat is shared</span>
              <Skeleton className="h-10 w-full rounded-xl" />
              <Skeleton className="h-4 w-2/3 rounded-md" />
            </div>
          )}
          {state.step === "failed" && <p className="text-sm text-destructive">{state.message}</p>}
          {state.step === "off" && (
            <p className="flex items-start gap-2.5 text-sm text-text-secondary">
              <Link2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
              Not shared. A link is made only when you create it, and you can turn it off at any time.
            </p>
          )}
          {state.step === "on" && (
            <p className="flex items-start gap-2.5 text-sm text-text-secondary">
              <Link2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
              <span>
                Shared {state.status.sharedAt ? shortDate(state.status.sharedAt) : ""} with {state.status.messageCount}{" "}
                {state.status.messageCount === 1 ? "message" : "messages"}. The link is shown only when it's made: update it
                to get a new one (the old link stops) with the chat as it is now.
              </span>
            </p>
          )}
          {state.step === "made" && (
            <div className="flex flex-col gap-2">
              <label htmlFor={fieldId} className="text-sm font-bold text-foreground">
                Link to this chat
              </label>
              <div className="flex gap-2">
                <Input
                  id={fieldId}
                  readOnly
                  value={state.url}
                  onFocus={(event) => event.currentTarget.select()}
                  className="min-w-0 flex-1 font-mono text-xs sm:text-sm"
                />
                <Button type="button" onClick={() => void copy(state.url)} className="shrink-0 gap-2">
                  {copied ? <Check aria-hidden className="h-4 w-4" /> : <Copy aria-hidden className="h-4 w-4" />}
                  {copied ? "Copied" : "Copy link"}
                </Button>
              </div>
              <p className="text-2xs text-text-secondary">
                {state.messageCount} {state.messageCount === 1 ? "message" : "messages"}, read-only. Save the link now: it
                isn't shown again.
              </p>
            </div>
          )}
        </div>

        <DialogFooter className="gap-2 sm:gap-2">
          {(state.step === "on" || state.step === "made") && (
            <Button type="button" variant="outline" onClick={() => void stop()} disabled={pending !== null}>
              {pending === "stop" ? "Stopping…" : "Stop sharing"}
            </Button>
          )}
          {state.step === "off" && (
            <Button type="button" onClick={() => void make()} disabled={pending !== null}>
              {pending === "make" ? "Creating…" : "Create link"}
            </Button>
          )}
          {state.step === "on" && (
            <Button type="button" onClick={() => void make()} disabled={pending !== null}>
              {pending === "make" ? "Updating…" : "Update link"}
            </Button>
          )}
          {state.step === "made" && (
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Done
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
