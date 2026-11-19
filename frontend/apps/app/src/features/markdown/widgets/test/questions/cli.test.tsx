import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { AssessmentAnswer, AssessmentQuestion } from "@/lib/api/types";
import { cli } from "./cli";

const BASE: AssessmentQuestion = {
  kind: "cli",
  text: "Show a brief status of every interface.",
  options: null,
  segments: null,
  blanks: null,
  response: null,
  correct: null,
  answers: null,
  explanation: null,
  prompt: "R1#",
  maxTries: 3,
};
const final = (answer: string, earned: number, status: AssessmentAnswer["status"] = "answered"): AssessmentQuestion => ({
  ...BASE,
  response: { status, value: null, answer, earned, possible: 1, at: "2026-11-18T10:00:00Z", marks: null, tries: ["sh int"] },
  accepted: ["show ip interface brief", "sh ip int br"],
  output: "Interface  IP-Address  OK? Method Status Protocol\nGi0/0      10.0.0.1    YES manual up     up",
  explanation: "`brief` keeps one line per interface.",
});
const input = (extra: object = {}) =>
  renderToStaticMarkup(
    <cli.Input
      question={BASE}
      number={1}
      value="sh ip"
      onChange={() => {}}
      disabled={false}
      mode="practice"
      tries={[]}
      labelledBy="q1-legend"
      {...extra}
    />,
  );

describe("the cli kind", () => {
  test("a command needs more than spaces; it is sent as typed", () => {
    expect(cli.isComplete(BASE, "   ")).toBe(false);
    expect(cli.isComplete(BASE, "sh ip int br")).toBe(true);
    expect(cli.toRequest(BASE, " sh ip int br ")).toBe(" sh ip int br ");
    expect(cli.empty(BASE)).toBe("");
  });

  test("the value held by the server: a draft first, then a final answer", () => {
    expect(cli.fromRecord({ ...BASE, draft: "conf t" })).toBe("conf t");
    expect(cli.fromRecord(final("sh ip int br", 1))).toBe("sh ip int br");
    expect(cli.fromRecord({ ...BASE, draft: [1] })).toBeNull();
    expect(cli.fromRecord(BASE)).toBeNull();
  });

  test("the input: a form, a monospace field named by the question, the prompt hidden but described", () => {
    const html = input();
    expect(html).toStartWith("<form");
    expect(html).toContain('<span aria-hidden="true" class="break-all font-mono text-sm text-text-secondary">R1#</span>');
    const promptId = html.match(/<span id="([^"]+)" class="sr-only">Prompt: R1#<\/span>/)?.[1];
    expect(promptId).toBeTruthy();
    expect(html).toContain(`aria-describedby="${promptId}"`);
    expect(html).toContain('aria-labelledby="q1-legend"');
    for (const attribute of ['autocomplete="off"', 'autocorrect="off"', 'autocapitalize="off"', 'spellcheck="false"', 'maxlength="300"']) {
      expect(html.toLowerCase()).toContain(attribute); // HTML attribute names are case-insensitive
    }
    expect(html).toContain("font-mono");
    expect(html).not.toContain("aria-invalid");
  });

  test("tries and an error are shown and read with the field", () => {
    const html = input({ tries: ["sh int", "show ip"], invalid: "Not quite: 1 try left." });
    expect(html).toContain("Tried:");
    expect(html).toContain(">sh int</code>");
    expect(html).toContain('aria-invalid="true"');
    const described = html.match(/aria-describedby="([^"]+)"/)?.[1]?.split(" ") ?? [];
    expect(described).toHaveLength(3); // the prompt, the tries, the error
  });

  test("a result: what was typed, every accepted form, earlier tries, and the output as a console block", () => {
    const html = renderToStaticMarkup(<cli.Result question={final("sh ip int bri", 0)} number={3} />);
    expect(html).toContain("Q3.");
    expect(html).toContain("Not quite.");
    expect(html).toContain("You typed <code");
    expect(html).toContain("Accepted forms:");
    expect(html).toContain(">sh ip int br</code>");
    expect(html).toContain("Earlier tries:");
    expect(html).toContain('role="region" aria-label="Output"');
    expect(html).toContain("R1# show ip interface brief\nInterface");
  });

  test("a result leaves its listing to the result list (drawn once there), even in a review", () => {
    const question = { ...final("sh ip int br", 1), listing: { title: "show run", content: "hostname R1" } };
    const html = renderToStaticMarkup(<cli.Result question={question} number={1} compact />);
    expect(html).not.toContain('aria-label="show run"');
    expect(html).toContain('aria-label="Output"');
  });

  test("a skipped result names no typed command", () => {
    const html = renderToStaticMarkup(<cli.Result question={final("", 0, "skipped")} number={null} />);
    expect(html).toContain("Skipped.");
    expect(html).not.toContain("You typed");
    expect(html).toContain("Accepted forms:");
  });
});
