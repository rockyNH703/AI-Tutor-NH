"use client";

import { Button } from "@v1/ui/button";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";

import { ApiError, describeError } from "@/lib/api/errors";

export function Loading({ label }: { label: string }) {
  return (
    <SkeletonGroup label={label} className="flex flex-col gap-2 py-1">
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-1/2" />
    </SkeletonGroup>
  );
}

/**
 * A read that failed, as a status (not an alert: a card further up the chat that nobody touched must not
 * interrupt). 404 is the same for "missing" and "not for this account", with nothing to retry.
 */
export function Failure({ error, retry }: { error: unknown; retry: () => void }) {
  const notFound = error instanceof ApiError && error.code === "not_found";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1">
      <p role="status" className="text-sm text-text-secondary">
        {notFound ? "This NHprep lesson isn't available to your account." : describeError(error).message}
      </p>
      {notFound ? null : (
        <Button type="button" variant="outline" size="sm" className="min-h-11 sm:min-h-9" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function Line({ children }: { children: string }) {
  return <p className="py-1 text-sm text-text-secondary">{children}</p>;
}
