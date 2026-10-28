/**
 * `/api/auth/*`: Better Auth (lib/auth). Only the routes in lib/auth/routes.ts reach it, with a
 * body of at most AUTH_BODY_BYTES (a larger one is refused before anything parses it); every
 * JSON answer leaves without its session token (page code never holds one).
 */

import { AUTH_BODY_BYTES, authRoute, withoutTokens } from "@/lib/auth/routes";
import { getAuth } from "@/lib/auth/server";
import { readBody } from "@/lib/server/proxy-core";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const NOT_FOUND = { error: { code: "not_found" } };
const TOO_LARGE = { error: { code: "payload_too_large" } };

/** The request with its body read once, within AUTH_BODY_BYTES; null when the body is larger. */
async function bounded(request: Request): Promise<Request | null> {
  if (request.method !== "POST") return request;
  const body = await readBody(request, AUTH_BODY_BYTES);
  if (body === "too_large") return null;
  const headers = new Headers(request.headers);
  headers.delete("content-length");
  headers.delete("transfer-encoding");
  return new Request(request.url, { method: request.method, headers, body });
}

async function handle(request: Request): Promise<Response> {
  if (!authRoute(request.method, new URL(request.url).pathname)) {
    return Response.json(NOT_FOUND, { status: 404, headers: { "Cache-Control": "no-store" } });
  }
  const read = await bounded(request);
  if (!read) return Response.json(TOO_LARGE, { status: 413, headers: { "Cache-Control": "no-store" } });
  const response = await getAuth().auth.handler(read);
  if (!(response.headers.get("content-type") ?? "").includes("application/json")) return response;
  const text = await response.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return new Response(text, { status: response.status, headers: response.headers });
  }
  const headers = new Headers(response.headers);
  headers.delete("content-length");
  headers.set("Cache-Control", "no-store");
  return new Response(JSON.stringify(withoutTokens(body)), { status: response.status, headers });
}

export { handle as GET, handle as POST };
