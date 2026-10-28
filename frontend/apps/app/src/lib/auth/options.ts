/**
 * What the auth pages may offer, read on the server per request. Kept apart from config.ts so a
 * page asking "is Google on?" never loads Better Auth or the database driver.
 */
export function googleEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return Boolean(env.GOOGLE_CLIENT_ID?.trim() && env.GOOGLE_CLIENT_SECRET?.trim());
}

/** Turnstile's public site key when the CAPTCHA is configured (both keys), else null. */
export function turnstileSiteKey(env: Record<string, string | undefined> = process.env): string | null {
  const siteKey = env.TURNSTILE_SITE_KEY?.trim();
  return siteKey && env.TURNSTILE_SECRET_KEY?.trim() ? siteKey : null;
}

/**
 * Whether signing in can work at the origin this page was asked for. Better Auth trusts only
 * NEXT_PUBLIC_SITE_URL (config.ts, read at runtime like here), so at any other origin (a Quick
 * Tunnel that came back on a new address) every attempt would fail with a bare 403: the page
 * says so instead. Unset (development), every origin is fine.
 */
export function signInServedAt(origin: string, env: Record<string, string | undefined> = process.env): boolean {
  const site = env.NEXT_PUBLIC_SITE_URL?.trim();
  if (!site) return true;
  try {
    return new URL(site).origin === new URL(origin).origin;
  } catch {
    return false;
  }
}

let warned = false;

/** Logs, once per process, that sign-in is unavailable at `origin` (the two origins only). */
export function warnSignInElsewhere(origin: string, env: Record<string, string | undefined> = process.env): void {
  if (warned) return;
  warned = true;
  console.error(`[auth] sign-in asked for at ${origin}, but NEXT_PUBLIC_SITE_URL is ${env.NEXT_PUBLIC_SITE_URL?.trim() ?? "unset"}`);
}
