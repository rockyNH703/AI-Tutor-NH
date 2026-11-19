"use client";

import { ConfirmDialog } from "@v1/ui/confirm-dialog";
import { cn } from "@v1/ui/utils";
import { Check, Copy, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { maskExamFences } from "@/features/markdown/assessment";
import { describeError } from "@/lib/api/errors";
import type { FeedbackRating } from "@/lib/api/types";
import type { ChatMessage } from "./chat-store";
import { useStores } from "./chat-context";

const actionClass =
  "focus-ring grid h-8 w-8 place-items-center rounded-lg text-text-secondary transition-colors hover:bg-surface-2 hover:text-foreground disabled:pointer-events-none disabled:opacity-40";

/**
 * Copy, regenerate, 👍/👎 under an assistant reply.
 *
 * Always visible on touch screens and for the latest reply; revealed on hover or
 * keyboard focus otherwise (never hover-only — that would hide them from keyboard
 * users).
 */
export function MessageActions({
  chatKey,
  message,
  canRegenerate,
  alwaysVisible,
}: {
  chatKey: string;
  message: ChatMessage;
  canRegenerate: boolean;
  alwaysVisible: boolean;
}) {
  const { chat } = useStores();
  const [copied, setCopied] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    try {
      // A finished reply's records are known: only an unsubmitted mock exam's fence is left out.
      await navigator.clipboard.writeText(maskExamFences(message.content, message.metadata?.assessments ?? []));
      setCopied(true);
    } catch {
      toast.error("Couldn't copy to the clipboard.");
    }
  };

  const rate = async (rating: FeedbackRating) => {
    try {
      await chat.setFeedback(chatKey, message.id, message.feedback === rating ? null : rating);
    } catch (error) {
      toast.error(describeError(error).title);
    }
  };

  const regenerate = () => {
    if (message.conversationId) void chat.regenerate(message.conversationId);
  };
  // The server keeps a mock exam's saved answers with this reply: a new reply would lose them.
  const savedExam =
    message.metadata?.assessments?.some(
      (test) => test.mode === "exam" && !test.submitted && test.questions.some((question) => question.draft != null),
    ) ?? false;

  return (
    <div
      className={cn(
        "mt-1 flex items-center gap-0.5 transition-opacity",
        alwaysVisible ? "opacity-100" : "opacity-100 md:[@media(hover:hover)_and_(pointer:fine)]:opacity-0 md:focus-within:opacity-100 md:group-hover:opacity-100",
      )}
    >
      <button type="button" onClick={copy} className={actionClass} aria-label={copied ? "Copied" : "Copy reply"}>
        {copied ? <Check aria-hidden className="h-4 w-4" /> : <Copy aria-hidden className="h-4 w-4" />}
      </button>
      {canRegenerate && (
        <button
          type="button"
          onClick={savedExam ? () => setConfirming(true) : regenerate}
          className={actionClass}
          aria-label="Regenerate reply"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
        </button>
      )}
      <ConfirmDialog
        open={confirming && canRegenerate}
        onOpenChange={setConfirming}
        title="Regenerate and lose your saved answers?"
        description="A new reply replaces this mock exam, and the answers you saved in it are not kept."
        confirmLabel="Regenerate"
        destructive
        onConfirm={() => {
          setConfirming(false);
          regenerate();
        }}
      />
      <button
        type="button"
        onClick={() => rate("up")}
        aria-pressed={message.feedback === "up"}
        aria-label="Good reply"
        className={cn(actionClass, message.feedback === "up" && "text-brand")}
      >
        <ThumbsUp aria-hidden className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => rate("down")}
        aria-pressed={message.feedback === "down"}
        aria-label="Bad reply"
        className={cn(actionClass, message.feedback === "down" && "text-destructive")}
      >
        <ThumbsDown aria-hidden className="h-4 w-4" />
      </button>
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </div>
  );
}
