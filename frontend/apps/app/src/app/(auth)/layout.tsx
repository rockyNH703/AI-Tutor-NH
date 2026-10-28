import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { getCurrentUser } from "@/lib/server/session";

/**
 * Sign-in and sign-up. A visitor whose session is still valid goes straight to
 * the app — decided here, where the session can actually be checked, rather than
 * in middleware (which only sees that a cookie exists, and bouncing on a stale
 * cookie is how redirect loops happen).
 *
 * The page is the form's own full screen (components/auth-form.tsx: its header,
 * its ground, its theme control), so nothing is drawn around it here.
 */
export default async function AuthLayout({ children }: { children: ReactNode }) {
  const session = await getCurrentUser();
  if (session.status === "ok") redirect("/");

  return <>{children}</>;
}
