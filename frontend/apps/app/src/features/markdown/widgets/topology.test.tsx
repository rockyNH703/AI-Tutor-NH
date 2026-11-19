import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { Diagram } from "@/lib/api/types";
import { layout } from "./concept-diagram";
import { ariaLabels, FLOW_PROPS, keepNodeSpace, layered } from "./flow";
import TopologyBlock, {
  choices,
  deviceName,
  deviceNodes,
  elementName,
  kindLook,
  linkEdges,
  linkName,
  parseTopology,
  TopologyText,
} from "./topology";

const NET: Diagram = {
  nodes: [
    { id: "r1", label: "R1", kind: "router", detail: "Edge router, NAT outside on Gi0/0" },
    { id: "sw1", label: "SW1", kind: "l3switch" },
    { id: "pc", label: "PC-A", kind: "host" },
  ],
  links: [
    { id: "l1", from: "r1", to: "sw1", label: "Gi0/1" },
    { id: "l2", from: "sw1", to: "pc", detail: "Access VLAN 10" },
  ],
};
const source = (value: unknown) => JSON.stringify(value);
const nodes = (count: number) => Array.from({ length: count }, (_, index) => ({ id: `n${index}`, label: `N${index}`, kind: "host" }));

describe("the ```topology validator", () => {
  test("a valid topology parses; absent links become none", () => {
    expect(parseTopology(source({ title: "Branch", ...NET }))?.nodes).toHaveLength(3);
    expect(parseTopology(source({ nodes: [{ id: "a", label: "A", kind: "router" }] }))?.links).toEqual([]);
  });

  test("ids are unique ignoring case, across devices and links", () => {
    expect(parseTopology(source({ ...NET, nodes: [...NET.nodes, { id: "R1", label: "R1 again", kind: "router" }] }))).toBeNull();
    expect(parseTopology(source({ ...NET, links: [...NET.links, { id: "SW1", from: "r1", to: "pc" }] }))).toBeNull();
  });

  test("a link to an unknown device is refused (ids match exactly)", () => {
    expect(parseTopology(source({ ...NET, links: [{ id: "x", from: "r1", to: "nope" }] }))).toBeNull();
    expect(parseTopology(source({ ...NET, links: [{ id: "x", from: "R1", to: "sw1" }] }))).toBeNull();
  });

  test("the caps: 30 devices, 40 links, labels 40, details 300", () => {
    expect(parseTopology(source({ nodes: nodes(30) }))).not.toBeNull();
    expect(parseTopology(source({ nodes: nodes(31) }))).toBeNull();
    const links = (count: number) => Array.from({ length: count }, (_, index) => ({ id: `l${index}`, from: "n0", to: "n1" }));
    expect(parseTopology(source({ nodes: nodes(2), links: links(40) }))).not.toBeNull();
    expect(parseTopology(source({ nodes: nodes(2), links: links(41) }))).toBeNull();
    const one = (node: object) => source({ nodes: [{ id: "a", label: "A", kind: "router", ...node }] });
    expect(parseTopology(one({ label: "x".repeat(40) }))).not.toBeNull();
    expect(parseTopology(one({ label: "x".repeat(41) }))).toBeNull();
    expect(parseTopology(one({ detail: "x".repeat(300) }))).not.toBeNull();
    expect(parseTopology(one({ detail: "x".repeat(301) }))).toBeNull();
    expect(parseTopology(one({ kind: "x".repeat(41) }))).toBeNull();
  });

  test("blank text, a bad id, no devices and non-JSON are refused", () => {
    const one = (node: object) => source({ nodes: [{ id: "a", label: "A", kind: "router", ...node }] });
    expect(parseTopology(one({ label: "   " }))).toBeNull();
    expect(parseTopology(one({ kind: "" }))).toBeNull();
    expect(parseTopology(one({ id: "has space" }))).toBeNull();
    expect(parseTopology(one({ id: "x".repeat(41) }))).toBeNull();
    expect(parseTopology(one({ id: "Gi0/1.10:a_b-c" }))).not.toBeNull();
    expect(parseTopology(source({ nodes: [] }))).toBeNull();
    expect(parseTopology("{nodes: ")).toBeNull();
  });
});

describe("names", () => {
  test("known kinds read as words, others keep the author's word (never Object's own keys)", () => {
    expect(deviceName({ label: "SW1", kind: "l3switch" })).toBe("SW1 (layer 3 switch)");
    expect(deviceName({ label: "AP", kind: " AP " })).toBe("AP (access point)");
    expect(kindLook("constructor").name).toBe("constructor");
    expect(kindLook("load balancer").name).toBe("load balancer");
  });

  test("links, elements and what a question lets you choose", () => {
    expect(linkName(NET, { id: "l1", from: "r1", to: "sw1", label: "Gi0/1" })).toBe("R1 to SW1 (Gi0/1)");
    expect(elementName(NET, "l2")).toBe("SW1 to PC-A");
    expect(elementName(NET, "r1")).toBe("R1 (router)");
    expect(choices(NET, "node").map((choice) => choice.id)).toEqual(["r1", "sw1", "pc"]);
    expect(choices(NET, "link").map((choice) => choice.id)).toEqual(["l1", "l2"]);
    expect(choices(NET, "any")).toHaveLength(5);
    expect(choices(NET, null)[4]).toEqual({ id: "l2", name: "SW1 to PC-A", detail: "Access VLAN 10" });
  });
});

describe("the canvas", () => {
  test("devices are laid out top to bottom; only the viewer's carry a name", () => {
    const drawn = deviceNodes(NET, true);
    const y = (id: string) => drawn.find((node) => node.id === id)?.position.y ?? Number.NaN;
    expect(y("r1")).toBeLessThan(y("sw1"));
    expect(y("sw1")).toBeLessThan(y("pc"));
    expect(drawn[0]?.ariaLabel).toBe("R1 (router)");
    expect(deviceNodes(NET, false)[0]?.ariaLabel).toBeUndefined();
  });

  test("links are never tab stops; a chosen one is drawn in brand with a check and named so", () => {
    const [plain, chosen] = linkEdges(NET, new Map([["l2", "chosen" as const]]));
    expect(plain).toMatchObject({ selectable: false, focusable: false, label: "Gi0/1", ariaLabel: "Link R1 to SW1 (Gi0/1)" });
    expect(plain?.style).toBeUndefined();
    expect(chosen).toMatchObject({ label: "✓", ariaLabel: "Link SW1 to PC-A, chosen" });
    expect(chosen?.style).toMatchObject({ stroke: "hsl(var(--brand))" });
    const [missed] = linkEdges(NET, new Map([["l1", "missed" as const]]));
    expect(missed).toMatchObject({ label: "◌ Gi0/1", ariaLabel: "Link R1 to SW1 (Gi0/1), in the answer, not chosen" });
  });

  test("a ring's links are all drawn from the upper device down", () => {
    const ring: Diagram = {
      nodes: [
        { id: "a", label: "A", kind: "router" },
        { id: "b", label: "B", kind: "router" },
        { id: "c", label: "C", kind: "router" },
      ],
      links: [
        { id: "ab", from: "a", to: "b" },
        { id: "bc", from: "b", to: "c" },
        { id: "ca", from: "c", to: "a" },
      ],
    };
    const drawn = deviceNodes(ring, false);
    const places = new Map(drawn.map((node) => [node.id, node.position]));
    for (const edge of linkEdges(ring, new Map(), places)) {
      expect(places.get(edge.source)?.y ?? 0).toBeLessThanOrEqual(places.get(edge.target)?.y ?? 0);
    }
  });

  test("the viewer draws its text version and hint", () => {
    // (React Flow applies ariaLabelConfig in an effect, so a server render still shows its defaults.)
    const html = renderToStaticMarkup(<TopologyBlock source={source({ title: "Branch", ...NET })} onError={() => {}} />);
    expect(html).toContain("Network diagram");
    expect(html).toContain("Choose a device to read more about it.");
    expect(html).toContain("Devices and links as text");
    const text = renderToStaticMarkup(<TopologyText diagram={NET} />);
    expect(text).toContain("R1 (router): Edge router, NAT outside on Gi0/0");
    expect(text).toContain("SW1 to PC-A: Access VLAN 10");
  });
});

describe("flow.ts", () => {
  test("every flow turns off React Flow's document keys, dragging and wheel zoom", () => {
    expect(FLOW_PROPS).toMatchObject({
      deleteKeyCode: null,
      panActivationKeyCode: null,
      selectionKeyCode: null,
      nodesDraggable: false,
      edgesFocusable: false,
      zoomOnScroll: false,
    });
  });

  test("its screen-reader text never mentions deleting", () => {
    expect(Object.values(ariaLabels("Press Enter to read about this device.")).join(" ").toLowerCase()).not.toContain("delete");
  });

  test("Space is stopped on a node only", () => {
    const press = (key: string, onNode: boolean) => {
      let prevented = false;
      keepNodeSpace({
        key,
        target: { closest: (selector: string) => (onNode && selector === ".react-flow__node" ? {} : null) } as never,
        preventDefault: () => {
          prevented = true;
        },
      });
      return prevented;
    };
    expect(press(" ", true)).toBe(true);
    expect(press(" ", false)).toBe(false);
    expect(press("Enter", true)).toBe(false);
  });

  test("layered places every box and leaves out links to unknown ids", () => {
    const places = layered(
      [
        { id: "a", width: 100, height: 40 },
        { id: "b", width: 100, height: 40 },
      ],
      [
        { from: "a", to: "b" },
        { from: "a", to: "ghost" },
      ],
      "LR",
    );
    expect(places.size).toBe(2);
    expect(places.get("a")?.x ?? 0).toBeLessThan(places.get("b")?.x ?? 0);
  });

  test("the concept diagram keeps its layout, drops dangling edges and names them", () => {
    const flow = layout({
      nodes: [
        { id: "a", label: "Alpha" },
        { id: "b", label: "Beta" },
      ],
      edges: [
        { from: "a", to: "b", label: "feeds" },
        { from: "a", to: "zz" },
      ],
    });
    expect(flow.nodes.map((node) => node.ariaLabel)).toEqual(["Alpha", "Beta"]);
    expect(flow.edges).toHaveLength(1);
    expect(flow.edges[0]?.ariaLabel).toBe("Alpha to Beta (feeds)");
  });
});
