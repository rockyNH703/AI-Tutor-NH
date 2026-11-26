import { cookies } from "next/headers";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { SIDEBAR_WIDTH_COOKIE, parseSidebarWidth } from "@/components/sidebar-width";

/**
 * The app: the chat, the learning tools and the help pages, inside the sidebar and header. The sidebar's chosen width
 * comes from its cookie, so the first paint already has it.
 */
export default async function MainLayout({ children }: { children: ReactNode }) {
  const width = parseSidebarWidth((await cookies()).get(SIDEBAR_WIDTH_COOKIE)?.value);
  return <AppShell sidebarWidth={width}>{children}</AppShell>;
}
