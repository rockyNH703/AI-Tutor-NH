import { describe, expect, mock, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { User } from "@/lib/api/types";

// The chat stores read the app router; outside Next there is none, and nothing here navigates.
mock.module("next/navigation", () => ({
  useRouter: () => ({ push() {}, replace() {}, refresh() {}, back() {}, forward() {}, prefetch() {} }),
  usePathname: () => "/settings/privacy",
  useSearchParams: () => new URLSearchParams(),
}));
const { UserProvider } = await import("@/components/user-context");
const { ChatProvider } = await import("@/features/chat/chat-context");
const { PrivacySection } = await import("./privacy-section");

const USER: User = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "someone@example.com",
  displayName: "Someone",
  emailVerified: true,
  createdAt: "2026-11-01T00:00:00Z",
  profileVersion: 1,
  timezone: null,
  avatar: null,
  passwordChangedAt: null,
};

/** The first render, before any request has answered (effects do not run on the server). */
function firstRender(googleEnabled: boolean) {
  return renderToStaticMarkup(
    <UserProvider user={USER}>
      <ChatProvider initialConversations={null}>
        <PrivacySection googleEnabled={googleEnabled} />
      </ChatProvider>
    </UserProvider>,
  );
}

describe("Privacy & Security, before its data arrives", () => {
  // A layout shift of 0.27 (2026-11-25): one short skeleton stood for three cards, so everything under it jumped.
  test("every card is already drawn, its status loading in place, so nothing below moves when it arrives", () => {
    const html = firstRender(true);
    for (const title of ["Password", "Two-step sign-in", "Google", "Signed-in devices", "Delete account"]) {
      expect(html).toContain(`>${title}</h`);
    }
    expect(html.match(/<span role="status"[^>]*><span aria-hidden="true"/g)).toHaveLength(3); // Password, Two-step, Google
    expect(html).not.toContain("Loading sign-in methods");
  });

  test("the forms start closed: each row offers its action, disabled until the status is known", () => {
    const html = firstRender(false);
    expect(html).not.toContain("Current password");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Change password<\/button>/);
    expect(html).not.toContain(">Google</h");
  });
});
