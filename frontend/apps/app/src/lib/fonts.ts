import localFont from "next/font/local";

/**
 * Noto Sans, the product's one typeface (SIL Open Font License, src/fonts/noto-sans/OFL.txt),
 * as variable fonts (every weight in one file). Served from this app, not a font CDN: nothing to
 * fetch at build time or from the browser, and nothing for the CSP to allow.
 *
 * Latin is preloaded. Devanagari (Hindi replies, a Hindi medium) is the same family's companion,
 * downloaded only when a page shows Devanagari (its unicode-range); both sit in one font stack
 * (packages/ui tailwind.config.ts), so a Hinglish line mixes them seamlessly. (Next's font loader
 * takes literals only, so each call spells out its options.)
 */
export const notoSans = localFont({
  src: [{ path: "../fonts/noto-sans/noto-sans-latin-wght.woff2", weight: "100 900", style: "normal" }],
  variable: "--font-noto-sans",
  display: "swap",
  fallback: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
});

/**
 * Lato Black, Networkers Home's own face (SIL Open Font License, src/fonts/lato/LICENSE), for the
 * display voice only: the greeting, page titles and the wordmark (font-display). One weight, Latin
 * only (22 KB, Google Fonts' lato v25, SHA-256 08664859…afbd92), preloaded: the greeting is the
 * first paint's largest text.
 */
export const lato = localFont({
  src: [{ path: "../fonts/lato/lato-latin-900.woff2", weight: "900", style: "normal" }],
  variable: "--font-lato",
  display: "swap",
  fallback: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
});

export const notoSansDevanagari = localFont({
  src: [{ path: "../fonts/noto-sans/noto-sans-devanagari-wght.woff2", weight: "100 900", style: "normal" }],
  variable: "--font-noto-sans-devanagari",
  display: "swap",
  preload: false,
  fallback: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
  declarations: [
    {
      prop: "unicode-range",
      value:
        "U+0900-097F, U+1CD0-1CF9, U+200C-200D, U+20A8, U+20B9, U+20F0, U+25CC, U+A830-A839, U+A8E0-A8FF, U+11B00-11B09",
    },
  ],
});
