import type { Metadata } from "next";
import { headers } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";

import { BrandLockup } from "@/components/brand/brand-mark";
import { ServiceUnavailable } from "@/components/service-unavailable";
import { ThemeSwitcher } from "@/components/theme-switcher";
import { MessageContent } from "@/features/markdown/lazy-message-content";
import type { SharedChat } from "@/lib/api/types";
import { apiBaseUrl } from "@/lib/server/config";
import { clientIp } from "@/lib/server/proxy-core";
import { shortDate } from "@/lib/time";

// Read on every request: a revoked link must stop at once, so nothing here is cached (the API says no-store too).
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Shared chat",
  robots: { index: false, follow: false },
  // The token is in this page's address: never send it on to another site.
  referrer: "no-referrer",
};

const TOKEN = /^[A-Za-z0-9_-]{32}$/;

/** The snapshot, read on the server with no session (GET /v1/shared/{token}): null when there is none. */
async function readShared(token: string): Promise<SharedChat | null | "unavailable"> {
  // The API's per-address limit needs the visitor's address: Cloudflare's, as the BFF sends it, never the visitor's
  // own X-Forwarded-For (which would choose its own bucket).
  const ip = clientIp(await headers());
  try {
    const response = await fetch(`${apiBaseUrl()}/v1/shared/${token}`, {
      headers: { Accept: "application/json", ...(ip ? { "X-Forwarded-For": ip } : {}) },
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    if (response.status === 404 || response.status === 422) return null;
    if (!response.ok) return "unavailable";
    return (await response.json()) as SharedChat;
  } catch {
    return "unavailable";
  }
}

/**
 * A shared chat (app/conversations/shares.py): a read-only copy of a chat as it was when it was shared. Public (no
 * account, lib/server/gate.ts), never indexed, never cached; an unknown, revoked or deleted link is a plain 404.
 */
export default async function SharedChatPage(props: { params: Promise<{ token: string }> }) {
  const { token } = await props.params;
  if (!TOKEN.test(token)) notFound();
  const chat = await readShared(token);
  if (chat === "unavailable") return <ServiceUnavailable />;
  if (chat === null) notFound();

  return (
    <div className="relative flex min-h-dvh w-full flex-col bg-background">
      <header className="sticky top-0 z-10 flex h-header items-center justify-between gap-3 border-b border-border bg-background/95 px-4 backdrop-blur-sm sm:px-6">
        <Link href="/" className="focus-ring rounded-xl">
          <BrandLockup compact />
        </Link>
        <div className="flex items-center gap-2">
          <ThemeSwitcher />
          <Link
            href="/signup"
            className="focus-ring hidden h-10 items-center rounded-xl bg-primary px-4 text-sm font-semibold text-primary-foreground sm:inline-flex"
          >
            Try NetSec AI
          </Link>
        </div>
      </header>

      <main id="main" tabIndex={-1} className="flex-1 outline-none">
        <article className="chat-column flex flex-col gap-6 pb-16 pt-8">
          <header className="flex flex-col gap-1 border-b border-border pb-5">
            <h1 className="font-display text-2xl font-bold tracking-tight text-foreground sm:text-3xl">{chat.title}</h1>
            <p className="text-sm text-text-secondary">
              Shared {shortDate(chat.sharedAt)} · {chat.messages.length} {chat.messages.length === 1 ? "message" : "messages"} ·
              read-only copy
            </p>
          </header>

          <ol className="flex flex-col gap-6">
            {chat.messages.map((message, index) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: a snapshot's messages never move or change
              <li key={index} className={message.role === "user" ? "flex flex-col items-end gap-1.5" : "flex flex-col gap-1.5"}>
                <span className="text-2xs font-semibold uppercase tracking-[0.08em] text-text-secondary">
                  {message.role === "user" ? "Question" : "NetSec AI"}
                </span>
                {message.role === "user" ? (
                  <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-surface-2 px-4 py-2.5 text-[0.9375rem] leading-7 text-foreground">
                    {message.content}
                  </div>
                ) : (
                  <div className="min-w-0">
                    <MessageContent content={message.content} />
                  </div>
                )}
                {message.attachments > 0 && (
                  <span className="text-2xs text-text-secondary">
                    {message.attachments} {message.attachments === 1 ? "file was" : "files were"} attached (not shared)
                  </span>
                )}
              </li>
            ))}
          </ol>

          <footer className="mt-4 flex flex-col items-start gap-3 rounded-2xl border border-border bg-card p-5 elev-1 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-text-secondary">
              A copy of a chat shared from NetSec AI. It doesn't change when the chat does.
            </p>
            <Link
              href="/"
              className="focus-ring inline-flex h-10 shrink-0 items-center rounded-xl border border-border px-4 text-sm font-semibold text-foreground hover:bg-surface-2"
            >
              Start your own chat
            </Link>
          </footer>
        </article>
      </main>
    </div>
  );
}
