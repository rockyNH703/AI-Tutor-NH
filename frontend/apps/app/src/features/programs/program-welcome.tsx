"use client";

import { useState } from "react";

import { useStores } from "@/features/chat/chat-context";
import type { ChatPlace } from "@/lib/api/types";
import { placeText } from "./program-nav";
import { usePrograms } from "./use-programs";

/**
 * Under a new program chat's greeting: what this tutor answers from, and a suggestion or two to start with. A
 * suggestion is sent as it is (each names a real month title, so the tutor's search finds that material); once one is
 * sent the chat is no longer empty and this goes away.
 */
export function ProgramWelcome({ chatKey, place }: { chatKey: string; place: ChatPlace }) {
  const { chat } = useStores();
  const { data } = usePrograms();
  const text = placeText(place, data?.programs.find((item) => item.slug === place.program) ?? null);
  const [sent, setSent] = useState(false);
  return (
    <div className="mt-3 flex flex-col items-center gap-4">
      <p className="max-w-prose text-balance text-center text-sm text-text-secondary sm:text-base">{text.line}</p>
      {text.suggestions.length > 0 && (
        <div role="group" aria-label="Suggestions" className="flex max-w-full flex-wrap justify-center gap-2">
          {text.suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              disabled={sent}
              onClick={() => {
                setSent(true);
                void chat.send(chatKey, suggestion);
              }}
              className="focus-ring min-h-11 max-w-full rounded-2xl border border-border bg-card px-4 py-2 text-left text-sm text-foreground elev-1 transition-colors hover:bg-surface-2 disabled:opacity-50"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
