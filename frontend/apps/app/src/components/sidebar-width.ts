/**
 * The desktop sidebar's width, chosen by dragging its edge (components/sidebar-resizer.tsx). A cookie, not
 * localStorage: the server reads it and draws the rail at that width from the first paint, so nothing moves when
 * the page hydrates. UI state only, on this browser; no account data.
 */
export const SIDEBAR_WIDTH = { min: 200, max: 420, default: 260 } as const;
export const SIDEBAR_WIDTH_COOKIE = "netsec_sidebar_width";
/** One arrow-key press, and with Shift. */
export const SIDEBAR_WIDTH_STEP = { small: 16, large: 48 } as const;

export function clampSidebarWidth(value: number): number {
  return Math.round(Math.min(SIDEBAR_WIDTH.max, Math.max(SIDEBAR_WIDTH.min, value)));
}

/** A cookie's value as a width: anything missing or not a number is the default. */
export function parseSidebarWidth(raw: string | undefined | null): number {
  if (!raw || !/^\d{1,4}$/.test(raw)) return SIDEBAR_WIDTH.default;
  return clampSidebarWidth(Number(raw));
}

/** Kept for a year on this browser, for every page (Lax: it is only read on our own pages). */
export function saveSidebarWidth(width: number): void {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${SIDEBAR_WIDTH_COOKIE}=${clampSidebarWidth(width)}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
}
