import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { AssessmentQuestion, Diagram } from "@/lib/api/types";
import { diagram, drawnStates } from "./diagram";

const NET: Diagram = {
  nodes: [
    { id: "r1", label: "R1", kind: "router" },
    { id: "fw", label: "FW1", kind: "firewall", detail: "Inside zone on Gi0/1" },
    { id: "sw1", label: "SW1", kind: "switch" },
  ],
  links: [
    { id: "l1", from: "r1", to: "fw", label: "outside" },
    { id: "l2", from: "fw", to: "sw1" },
  ],
};
const BASE: AssessmentQuestion = {
  kind: "diagram",
  text: "Where would you apply the inbound ACL?",
  options: null,
  segments: null,
  blanks: null,
  response: null,
  correct: null,
  answers: null,
  explanation: null,
  diagram: NET,
  select: "any",
};
const input = (value: string[], extra: Partial<AssessmentQuestion> = {}) =>
  renderToStaticMarkup(
    <diagram.Input
      question={{ ...BASE, ...extra }}
      number={1}
      value={value}
      onChange={() => {}}
      disabled={false}
      mode="exam"
      tries={[]}
      labelledBy="q1-legend"
    />,
  );

describe("the diagram kind", () => {
  test("an answer needs an element; ids are sent sorted", () => {
    expect(diagram.isComplete(BASE, [])).toBe(false);
    expect(diagram.toRequest(BASE, ["sw1", "fw", "l1"])).toEqual(["fw", "l1", "sw1"]);
    expect(diagram.fromRecord({ ...BASE, draft: ["l2"] })).toEqual(["l2"]);
    expect(diagram.fromRecord({ ...BASE, draft: [1] })).toBeNull();
    expect(diagram.fromRecord(BASE)).toBeNull();
  });

  test("states: chosen while answering; marked against the answer once final", () => {
    expect([...drawnStates(NET, ["fw"])]).toEqual([["fw", "chosen"]]);
    expect(Object.fromEntries(drawnStates(NET, ["fw", "l2"], ["fw", "l1"]))).toEqual({
      fw: "right",
      l1: "missed",
      l2: "wrong",
    });
  });

  test("the keyboard path: a checkbox per choosable device and link, in a fieldset", () => {
    const html = input(["fw"]);
    expect(html).toContain("<legend");
    expect(html).toContain("Devices and links</legend>");
    expect(html.match(/type="checkbox"/g)).toHaveLength(5);
    expect(html.match(/checked=""/g)).toHaveLength(1);
    expect(html).toContain("FW1 (firewall)");
    expect(html).toContain("Inside zone on Gi0/1");
    expect(html).toContain("R1 to FW1 (outside)");
    expect(input([], { select: "link" }).match(/type="checkbox"/g)).toHaveLength(2);
    expect(input([], { select: "node" }).match(/type="checkbox"/g)).toHaveLength(3);
  });

  test("a result names what was chosen and what was right", () => {
    const final: AssessmentQuestion = {
      ...BASE,
      response: { status: "answered", value: null, answer: ["l2"], earned: 0, possible: 1, at: "2026-11-18T10:00:00Z", marks: null },
      accepted: ["fw", "l1"],
    };
    const html = renderToStaticMarkup(<diagram.Result question={final} number={4} />);
    expect(html).toContain("Not quite.");
    expect(html).toContain("You chose FW1 to SW1.");
    expect(html).toContain("The answer is FW1 (firewall), R1 to FW1 (outside).");
    expect(html).not.toContain("react-flow");
    expect(renderToStaticMarkup(<diagram.Result question={final} number={4} compact />)).toContain("react-flow");
  });

  test("a review draws the topology but leaves the listing to the result list (drawn once there)", () => {
    const final: AssessmentQuestion = {
      ...BASE,
      listing: { title: "show run", content: "hostname R1" },
      response: { status: "answered", value: null, answer: ["fw"], earned: 1, possible: 1, at: "2026-11-18T10:00:00Z", marks: null },
      accepted: ["fw"],
    };
    const html = renderToStaticMarkup(<diagram.Result question={final} number={1} compact />);
    expect(html).toContain("react-flow");
    expect(html).not.toContain('aria-label="show run"');
  });
});
