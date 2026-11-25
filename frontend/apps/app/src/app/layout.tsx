import "@v1/ui/globals.css";
import { TooltipProvider } from "@v1/ui/tooltip";
import type { Metadata, Viewport } from "next";
import { ThemeProvider } from "next-themes";
import type { CSSProperties, ReactNode } from "react";
import { Toaster } from "sonner";

import { PREPAINT_SCRIPT } from "@/lib/appearance";
import { lato, notoSans, notoSansDevanagari } from "@/lib/fonts";
import { APP_NAME, BRAND } from "@/lib/brand";
import { siteUrl } from "@/lib/site";

// Noto Sans and Lato are self-hosted (lib/fonts.ts); code uses the system monospace face, which
// reads better at length than a wide display mono.
const fonts = {
  "--font-mono": 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
} as CSSProperties;

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl()),
  title: { default: APP_NAME, template: `%s · ${APP_NAME}` },
  description: BRAND.description,
  applicationName: APP_NAME,
  // The tab icon (lib/brand.ts): the mark on NH's console-ink tile, legible on any tab colour.
  icons: { icon: { url: BRAND.faviconSrc, type: "image/svg+xml" } },
  // A private app: nothing here is for search engines.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#FFFFFF" },
    { media: "(prefers-color-scheme: dark)", color: "#0A0D13" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${notoSans.variable} ${notoSansDevanagari.variable} ${lato.variable}`} suppressHydrationWarning>
      <head>
        {/* Text size, contrast, motion and reading spacing from the last visit, before the
            first paint (lib/appearance.ts); the signed-in app then applies the server's value. */}
        {/* biome-ignore lint/security/noDangerouslySetInnerHtml: a constant script, no user data */}
        <script dangerouslySetInnerHTML={{ __html: PREPAINT_SCRIPT }} />
      </head>
      <body className="font-sans antialiased" style={fonts}>
        {/* WCAG 2.4.1: every page's main region carries id="main". */}
        <a
          href="#main"
          className="sr-only z-50 focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:rounded-xl focus:bg-brand focus:px-4 focus:py-2.5 focus:text-sm focus:font-medium focus:text-brand-foreground focus:outline-none focus:ring-2 focus:ring-brand/40"
        >
          Skip to content
        </a>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <TooltipProvider delayDuration={300}>{children}</TooltipProvider>
          <Toaster richColors position="top-center" />
        </ThemeProvider>
      </body>
    </html>
  );
}
