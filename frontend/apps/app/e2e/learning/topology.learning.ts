import type { Locator, Page } from "@playwright/test";

import { expectNoHorizontalOverflow, replies } from "../support";
import { DRIVERS } from "./drivers";
import { expect, fixture, openFixture, test } from "./support";
import { COPY, at, body, card, expectAccessible22, expectTargets, practiceTraffic, question, shot, transcriptScroll } from "./ui";

/**
 * ```topology and the diagram question (plan §3 "Topology and flows", §7): zoom and fit, Enter on a node
 * shows its detail without scrolling the chat, Space on a node never pages while Space still toggles a
 * "Why" elsewhere, the Tab walk through the diagram stays within nodes + 5 stops, no "press delete" in its
 * description; and a diagram question answered on the canvas and in the list sends the same answer.
 */

type Topology = { nodes: { id: string; label: string; kind: string; detail?: string }[] };
const TOPOLOGY = JSON.parse(/```topology\n(.*)\n```/.exec(fixture("topology").message)?.[1] ?? "{}") as Topology;
const node = (scope: Locator, id: string) => scope.locator(`.react-flow__node[data-id="${id}"]`);
const scale = (diagram: Locator) =>
  diagram.locator(".react-flow__viewport").evaluate((element) => new DOMMatrix(getComputedStyle(element).transform).a);

/** Tab from where focus is until it has entered `scope` and left it again: each stop inside, as its tag, role and name. */
async function tabStopsInside(page: Page, scope: Locator, max = 80): Promise<string[]> {
  const stops: string[] = [];
  for (let press = 0; press < max; press += 1) {
    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    if (await scope.evaluate((root) => root.contains(document.activeElement))) {
      const tag = await focused.evaluate((element) => element.tagName.toLowerCase());
      stops.push(`${tag}: ${(await focused.ariaSnapshot()).split("\n")[0]}`);
    } else if (stops.length) return stops;
  }
  return stops;
}

test("zoom, fit, Enter shows a detail in place, Space never pages, a short Tab walk", { tag: "@demo" }, async ({ page }, testInfo) => {
  const quiz = await openFixture(page, "v1-quiz");
  const quizCard = card(replies(page).first());
  await quizCard.getByRole("radio", { name: /Network/ }).check();
  await quizCard.getByRole("button", { name: COPY.v1Check }).click();
  const { reply } = await openFixture(page, "topology", { conversationId: quiz.conversationId });
  const diagram = card(reply);

  await test.step("every device drawn and named 'label (kind)'", async () => {
    for (const item of TOPOLOGY.nodes) await expect(node(diagram, item.id)).toHaveAttribute("aria-label", `${item.label} (${item.kind})`);
    const description = await diagram.locator("[id^='react-flow__node-desc'], [id^='react-flow__edge-desc']").allTextContents();
    expect(description.join(" "), "no 'press delete' in a viewer").not.toMatch(/delete/i);
    await expectAccessible22(page);
    await shot(page, testInfo, "topology");
  });

  await test.step("zoom in, then fit", async () => {
    const start = await scale(diagram);
    await diagram.getByRole("button", { name: /zoom in/i }).click();
    await expect.poll(() => scale(diagram)).toBeGreaterThan(start);
    await diagram.getByRole("button", { name: /fit to view/i }).click(); // flow.ts ariaLabels
    await expect.poll(() => scale(diagram)).toBeCloseTo(start, 1);
  });

  await test.step("Enter on a node shows its detail; neither Enter nor Space scrolls the chat", async () => {
    const switchNode = TOPOLOGY.nodes.find((item) => item.id === "SW1");
    await node(diagram, "SW1").focus();
    const before = await transcriptScroll(page);
    await page.keyboard.press("Enter");
    await expect(diagram).toContainText(switchNode?.detail ?? "");
    await page.keyboard.press("Space");
    expect(await transcriptScroll(page)).toBe(before);
  });

  await test.step("Space still toggles a 'Why' outside the diagram", async () => {
    const why = quizCard.locator("details").first();
    const open = await why.evaluate((element) => (element as HTMLDetailsElement).open);
    await why.locator("summary").focus();
    await page.keyboard.press("Space");
    await expect.poll(() => why.evaluate((element) => (element as HTMLDetailsElement).open)).toBe(!open);
  });

  await test.step("the Tab walk through the diagram: at most nodes + 5 stops", async () => {
    await quizCard.locator("summary").first().focus();
    const stops = await tabStopsInside(page, diagram);
    await testInfo.attach("tab-stops", { body: stops.join("\n"), contentType: "text/plain" });
    // Every stop is a control (headed run, 2026-11-19): each device, then React Flow's Zoom in, Zoom out and Fit
    // to view, its attribution link (xyflow asks only Pro subscribers to hide it) and the "Devices and links as
    // text" summary. Links and the canvas are never stops, so a runaway (edges, a focusable wrapper) still fails.
    expect(stops.length).toBeGreaterThan(0);
    expect(stops.length, stops.join("\n")).toBeLessThanOrEqual(TOPOLOGY.nodes.length + 5);
  });
});

test.describe("390px", () => {
  test.use(at(390));
  test("coarse-pointer controls are 44 px, nothing overflows", async ({ page }, testInfo) => {
    const { reply } = await openFixture(page, "topology");
    await expect(node(card(reply), "R1")).toBeVisible();
    await expectTargets(card(reply), 44, ".react-flow__controls-button");
    await expectNoHorizontalOverflow(page);
    await shot(page, testInfo, "topology-390");
  });
});

test("diagram question: the canvas and the list choose the same thing", async ({ page }, testInfo) => {
  const traffic = practiceTraffic(page);
  const right = ["SW1", "FW1", "R1"];
  /** The value of the `count`-th answer sent, sorted. */
  const answered = async (count: number): Promise<string[]> => {
    await traffic.settled("answer", count);
    const sent = traffic.sent.filter((request) => body(request).action === "answer").at(-1);
    return [...((sent ? body(sent).value : []) as string[])].sort();
  };

  const canvas = await openFixture(page, "kind-diagram");
  const onCanvas = question(canvas.reply);
  const list = onCanvas.getByRole("group", { name: COPY.devicesAndLinks });
  const box = (id: string) => list.getByRole("checkbox", { name: new RegExp(`^${id}\\b`) });
  for (const id of right) await node(onCanvas, id).click();
  for (const id of right) await expect(box(id), `the list follows the canvas (${id})`).toBeChecked();
  await node(onCanvas, "SW1").click();
  await expect(box("SW1")).not.toBeChecked();
  await box("SW1").check();
  await shot(page, testInfo, "diagram-chosen");
  await canvas.reply.getByRole("button", { name: COPY.check }).click();
  await expect(card(canvas.reply)).toContainText(COPY.progress(2, 2, 1, 1));
  const viaCanvas = await answered(1);
  // The final answer is read-only: its result is in words (the exam review alone redraws the canvas, review.tsx),
  // and neither the checkbox list nor a clickable canvas is left for it.
  await expect(card(canvas.reply).getByRole("listitem").filter({ hasText: "You chose" })).toContainText("SW1");
  await expect(card(canvas.reply).getByRole("group", { name: COPY.devicesAndLinks })).toHaveCount(0);
  await expect(card(canvas.reply).locator(".react-flow")).toHaveCount(0);

  const typed = await openFixture(page, "kind-diagram");
  await DRIVERS.diagram.right(question(typed.reply), page);
  await typed.reply.getByRole("button", { name: COPY.check }).click();
  await expect(card(typed.reply)).toContainText(COPY.progress(2, 2, 1, 1));
  const viaList = await answered(2);

  expect(viaCanvas).toEqual([...right].sort());
  expect(viaList).toEqual(viaCanvas);
});
