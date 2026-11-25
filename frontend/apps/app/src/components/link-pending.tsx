"use client";

import { cn } from "@v1/ui/utils";
import { useLinkStatus } from "next/link";

/**
 * Inside a <Link>: a small mark at the row's end while that link's navigation is pending, so a
 * click answers at once even when the next page takes a moment (a prefetched page swaps in before
 * it ever shows). Decorative: the page's new heading is what assistive technology hears.
 */
export function LinkPending({ className }: { className?: string }) {
  const { pending } = useLinkStatus();
  return (
    <span
      aria-hidden
      data-link-pending={pending ? "" : undefined}
      className={cn(
        "ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-brand transition-opacity duration-fast",
        pending ? "opacity-100 motion-safe:animate-soft-pulse" : "opacity-0",
        className,
      )}
    />
  );
}
