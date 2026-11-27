/** The Programs pages' card language (owner's redesign, 2026-11-26), shared by every card list there. */
export const CARD_GRID = "grid max-w-4xl grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),1fr))] gap-3 sm:gap-4";
export const CARD =
  "group focus-ring rounded-2xl border border-border bg-card elev-1 transition-[background-color,border-color,transform] duration-fast ease-out hover:border-foreground/15 hover:bg-surface-2/40 active:bg-surface-2/70 motion-safe:active:scale-[0.99]";
/** A card's place in its sequence (a month, a lesson): "01". */
export const NUMBER =
  "grid h-8 min-w-8 shrink-0 place-items-center rounded-lg bg-surface-2 px-1.5 text-xs font-semibold tabular-nums tracking-wide text-text-secondary";
/** The quiet line at a card's foot: what it holds, or what opening it does. */
export const FOOT = "flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border pt-3 text-xs text-text-secondary";
export const ARROW =
  "h-4 w-4 shrink-0 text-text-secondary transition-[color,transform] duration-fast ease-out group-hover:text-foreground motion-safe:group-hover:translate-x-0.5";

export const twoDigits = (n: number) => String(n).padStart(2, "0");
