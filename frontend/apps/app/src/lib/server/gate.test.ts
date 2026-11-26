import { describe, expect, test } from "bun:test";
import { gate } from "./gate";

describe("middleware page gate", () => {
  test("pages need a session cookie", () => {
    expect(gate("/", "", false)).toEqual({ action: "redirect", location: "/login" });
    expect(gate("/settings", "", false)).toEqual({ action: "redirect", location: "/login?redirect=%2Fsettings" });
    expect(gate("/c/abc", "?x=1", false)).toEqual({ action: "redirect", location: "/login?redirect=%2Fc%2Fabc%3Fx%3D1" });
  });

  test("with the cookie, pages pass (the layout checks it is still valid)", () => {
    expect(gate("/", "", true)).toEqual({ action: "next" });
    expect(gate("/c/abc", "", true)).toEqual({ action: "next" });
  });

  test("the sign-in pages are always reachable", () => {
    expect(gate("/login", "", false)).toEqual({ action: "next" });
    expect(gate("/signup", "", false)).toEqual({ action: "next" });
    expect(gate("/login", "", true)).toEqual({ action: "next" });
  });

  test("an email's unsubscribe link works without signing in", () => {
    expect(gate("/unsubscribe", "?token=abc.def", false)).toEqual({ action: "next" });
    expect(gate("/unsubscribe", "?token=abc.def", true)).toEqual({ action: "next" });
    expect(gate("/unsubscribe/x", "", false)).toEqual({ action: "redirect", location: "/login?redirect=%2Funsubscribe%2Fx" });
    // A shared chat needs no account; only a token-shaped single segment is public.
    const token = "A".repeat(32);
    expect(gate(`/share/${token}`, "", false)).toEqual({ action: "next" });
    expect(gate("/share/short", "", false).action).toBe("redirect");
    expect(gate(`/share/${token}/x`, "", false).action).toBe("redirect");
    expect(gate("/share", "", false).action).toBe("redirect");
  });

  test("the API is never redirected — it answers JSON itself", () => {
    expect(gate("/api/v1/me", "", false)).toEqual({ action: "next" });
    expect(gate("/api/session/expired", "", false)).toEqual({ action: "next" });
  });
});
