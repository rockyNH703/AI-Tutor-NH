import { cn } from "@v1/ui/utils";
import type { LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes } from "react";

/**
 * A quiet action inside a card or a sentence ("Try again", "Dismiss", "Show as table"): plain foreground text with a soft
 * fill on hover and press, never a blue underlined link (those are kept for real destinations). `inlineAction` is the
 * same look for a <Link> that is an in-card next step ("See your Lab Activity").
 */
export const inlineAction =
  "focus-ring inline-flex min-h-8 items-center gap-1.5 rounded-lg px-2 text-sm font-semibold text-foreground transition-colors duration-fast hover:bg-surface-2 active:bg-border/70 disabled:pointer-events-none disabled:opacity-50 [@media(pointer:coarse)]:min-h-11";

export function InlineAction({
  icon: Icon,
  className,
  children,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type"> & { icon?: LucideIcon }) {
  return (
    <button type="button" {...props} className={cn(inlineAction, className)}>
      {Icon && <Icon aria-hidden className="h-4 w-4 shrink-0 text-text-secondary" />}
      {children}
    </button>
  );
}
