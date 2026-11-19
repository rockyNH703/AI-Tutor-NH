import { lastReply, waitForHydration } from "../support";
import { expect, fixture, sendTurn, test } from "./support";

/**
 * The stack proof: the seeded sign-in holds, a v1 quiz is recorded by the server, answered in its
 * card, and the recorded answer is what a reload shows.
 */
test("seeded sign-in, a recorded v1 quiz answered in its card, the same after a reload", async ({ page, signedIn }, testInfo) => {
  await test.step("the seeded session signs in", async () => {
    const session = await page.request.get("/api/auth/get-session");
    expect(session.status(), "get-session").toBe(200);
    expect(((await session.json()) as { user?: { email?: string } } | null)?.user?.email).toBe(signedIn.email);
    expect((await page.request.get("/api/v1/me")).status(), "/api/v1/me").toBe(200);
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/settings(\/|$)/);
    await page.reload();
    await expect(page).toHaveURL(/\/settings(\/|$)/);
  });

  const { conversationId, done } = await test.step("the quiz is sent and recorded", async () => {
    const turn = await sendTurn(page, fixture("v1-quiz").message);
    expect(turn.done.message.status).toBe("complete");
    expect(turn.done.message.metadata?.assessments, "done.message.metadata.assessments").toHaveLength(1);
    return turn;
  });
  expect(done.message.content).toContain("```quiz");

  const card = lastReply(page);
  await test.step("the card draws the first question", async () => {
    await page.goto(`/c/${conversationId}`);
    await waitForHydration(page);
    await expect(card.getByText("Question 1 of 2")).toBeVisible();
    await expect(card.getByRole("radio")).toHaveCount(4);
    await page.screenshot({ path: testInfo.outputPath("1-first-question.png") });
  });

  await test.step("Check answer records the choice", async () => {
    await card.getByRole("radio", { name: /Network/ }).check();
    await card.getByRole("button", { name: "Check answer" }).click();
    await expect(card.getByText(/Correct! You chose B\./)).toBeVisible();
    await expect(card.getByText("Question 2 of 2 · 1 correct so far")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("2-answered.png") });
  });

  await test.step("a reload shows the recorded state", async () => {
    await page.reload();
    await waitForHydration(page);
    await expect(card.getByText(/Correct! You chose B\./)).toBeVisible();
    await expect(card.getByText("Question 2 of 2 · 1 correct so far")).toBeVisible();
    await expect(card.getByRole("radio", { name: /ARP/ })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("3-after-reload.png") });
  });
});
