import { lastReply, waitForHydration } from "../support";
import { expect, sendTurn, test } from "./support";

/** The stack proof: a seeded student signs in, a turn goes through the BFF to this worktree's API, and the chat draws it. */
test("seeded student signs in, a turn is answered, the chat shows it", async ({ page, signedIn }, testInfo) => {
  const session = await page.request.get("/api/auth/get-session");
  expect(session.status(), "get-session").toBe(200);
  expect(((await session.json()) as { user?: { email?: string } } | null)?.user?.email).toBe(signedIn.email);
  expect((await page.request.get("/api/v1/me")).status(), "/api/v1/me").toBe(200);

  const { conversationId, done } = await sendTurn(page, "nhprep stack check");
  expect(done.message.status).toBe("complete");
  expect(done.message.content).toContain("nhprep stack check");

  await page.goto(`/c/${conversationId}`);
  await waitForHydration(page);
  await expect(lastReply(page)).toContainText("nhprep stack check");
  await page.screenshot({ path: testInfo.outputPath("chat.png") });
});
