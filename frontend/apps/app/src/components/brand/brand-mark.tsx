import { cn } from "@v1/ui/utils";

import { BRAND } from "@/lib/brand";

/**
 * The logo mark, from the one brand asset (see lib/brand.ts). Decorative: every
 * place it appears names the product in text as well, so it carries no alt text.
 */
export function BrandMark({ size = 40, className }: { size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      aria-hidden
      width={size}
      height={size}
      style={{ width: size, height: size }}
      className={cn("shrink-0 select-none text-foreground", className)}
    >
      <g fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M16 2.8 29.1 26H2.9L16 2.8Z" />
        <path d="m16 2.8-4.35 15.3L2.9 26" />
        <path d="m16 2.8 4.35 15.3L29.1 26" />
        <path d="m11.65 18.1 8.7 0L16 26l-4.35-7.9Z" />
      </g>
    </svg>
  );
}

/** The NetSec AI wordmark in one crisp neutral treatment. */
export function BrandWordmark({ className }: { className?: string }) {
  return (
    <span className={cn("font-display font-extrabold tracking-[-0.035em] text-foreground", className)}>
      {BRAND.wordmark.lead}
      <span>{BRAND.wordmark.accent}</span>
    </span>
  );
}

/** Mark and wordmark used in the sidebar, Settings panel, and authentication pages. */
export function BrandLockup({ compact = false, className }: { compact?: boolean; className?: string }) {
  return (
    <span className={cn("flex min-w-0 items-center gap-2.5", className)}>
      <BrandMark size={36} />
      <BrandWordmark className={compact ? "text-xl leading-none" : "text-2xl leading-none"} />
    </span>
  );
}
