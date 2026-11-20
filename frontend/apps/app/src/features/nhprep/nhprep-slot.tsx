"use client";

import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { Component, type ReactNode, Suspense, lazy, useEffect, useRef, useState } from "react";

import type { NhprepCard } from "@/lib/api/types";

const NhprepPanel = lazy(() => import("./nhprep-panel"));

export const COPY = {
  newerVersion: "This NHprep card needs a newer version of the app. Reload the page to see it.",
  crashed: "This NHprep card couldn't be shown. Reload the page to try again.",
} as const;

function Note({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="my-3 rounded-xl border border-border bg-study-card px-3.5 py-3 text-sm text-text-secondary">
      {children}
    </p>
  );
}

/** Catches a render error and a chunk that failed to download (a failed lazy import throws to here). */
class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? <Note>{COPY.crashed}</Note> : this.props.children;
  }
}

function Waiting() {
  return (
    <SkeletonGroup label="Loading the NHprep lesson" className="my-3">
      <Skeleton className="aspect-video w-full rounded-xl" />
    </SkeletonGroup>
  );
}

/** True once the element has come near the viewport (or at once where the browser can't tell). */
function useSeen(eager: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(eager);
  useEffect(() => {
    if (seen) return;
    if (eager || typeof IntersectionObserver === "undefined" || !ref.current) {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setSeen(true);
    }, { rootMargin: "300px 0px" });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [eager, seen]);
  return { ref, seen };
}

/**
 * An NHprep reply's lesson card, under its fixed text. Light on purpose: the panel (its own chunk) loads only
 * once the card nears the viewport, or at once for the chat's latest reply (`eager`); so does every request.
 */
export function NhprepSlot({ card, eager }: { card: NhprepCard; eager: boolean }) {
  const { ref, seen } = useSeen(eager);
  if (card.version !== 1) return <Note>{COPY.newerVersion}</Note>;
  return (
    <div ref={ref} data-nhprep-slot="" className="w-full">
      {seen ? (
        <Boundary>
          <Suspense fallback={<Waiting />}>
            <NhprepPanel card={card} />
          </Suspense>
        </Boundary>
      ) : (
        // Not a live status: twenty cards further up the chat must not each announce a wait.
        <Skeleton className="my-3 aspect-video w-full rounded-xl" />
      )}
    </div>
  );
}
