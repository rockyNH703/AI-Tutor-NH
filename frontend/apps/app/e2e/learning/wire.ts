import { createHmac } from "node:crypto";

/**
 * The session cookie's value as Better Auth signs it, the form src/lib/server/cookies.ts verifies:
 * encodeURIComponent(token + "." + base64(HMAC-SHA256(secret, token))).
 */
export function signedCookieValue(token: string, secret: string): string {
  return encodeURIComponent(`${token}.${createHmac("sha256", secret).update(token).digest("base64")}`);
}

export type SseEvent = { event: string; data: unknown };

/** The events of a whole chat stream body: one `event:` and one `data:` line per frame; keep-alives skipped. */
export function sseEvents(body: string): SseEvent[] {
  return body
    .replace(/\r\n?/g, "\n")
    .split("\n\n")
    .flatMap((frame) => {
      const event = /^event: (.+)$/m.exec(frame)?.[1];
      const data = /^data: (.+)$/m.exec(frame)?.[1];
      return event && data !== undefined ? [{ event, data: JSON.parse(data) as unknown }] : [];
    });
}
