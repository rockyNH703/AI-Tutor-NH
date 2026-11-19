"use client";

import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { Check, TriangleAlert } from "lucide-react";

/** A pill's name: its number, then what is true of it (a failed save outranks answered). */
export function pillName(number: number, answered: boolean, unsaved: boolean): string {
  return `Question ${number}${unsaved ? ", not saved" : answered ? ", answered" : ""}`;
}

/**
 * A mock exam's question pills: one per question, the current one marked (aria-current, border and
 * surface), a check when answered and a warning when its save failed. A pill never moves focus: it
 * stays on the pill, and the question below changes.
 */
export function Navigator({
  count,
  at,
  answered,
  unsaved,
  onPick,
}: {
  count: number;
  at: number;
  answered: readonly boolean[];
  unsaved: ReadonlySet<number>;
  onPick: (index: number) => void;
}) {
  return (
    <nav aria-label="Questions" className="mb-3">
      <ol className="flex flex-wrap gap-1.5">
        {Array.from({ length: count }, (_, index) => {
          const failed = unsaved.has(index);
          const done = Boolean(answered[index]);
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: pills are positions in the test
            <li key={index}>
              <Button
                type="button"
                variant="outline"
                aria-label={pillName(index + 1, done, failed)}
                aria-current={index === at ? "step" : undefined}
                onClick={() => onPick(index)}
                className={cn(
                  "min-h-11 min-w-11 gap-1 px-2 tabular-nums",
                  index === at && "border-brand bg-surface-2",
                )}
              >
                {index + 1}
                {failed ? (
                  <TriangleAlert aria-hidden className="h-3.5 w-3.5 shrink-0 text-warning" />
                ) : done ? (
                  <Check aria-hidden className="h-3.5 w-3.5 shrink-0 text-brand" />
                ) : null}
              </Button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
