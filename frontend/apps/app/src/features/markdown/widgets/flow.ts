"use client";

import dagre from "@dagrejs/dagre";
import type { AriaLabelConfig, ReactFlowProps, XYPosition } from "@xyflow/react";
import { useTheme } from "next-themes";
import { type CSSProperties, type KeyboardEvent, useEffect, useState } from "react";

import { token } from "./tokens";

/**
 * What every React Flow drawing in a reply shares (```diagram, ```topology and diagram questions): the
 * design system's colours, a dagre layout, and props that keep the transcript's keys its own.
 */

/** React Flow's own colours, from the design system (light, dark and high contrast alike). Set on
 * its root element: its themes define these same variables there, and an inline style wins. */
export const FLOW_THEME = {
  "--xy-node-background-color-default": "hsl(var(--card))",
  "--xy-node-border-default": "1px solid hsl(var(--border))",
  "--xy-node-color-default": "hsl(var(--foreground))",
  "--xy-node-boxshadow-selected-default": "0 0 0 2px hsl(var(--brand))",
  "--xy-edge-stroke-default": "hsl(var(--text-secondary))",
  "--xy-edge-label-background-color-default": "hsl(var(--card))",
  "--xy-edge-label-color-default": "hsl(var(--foreground))",
  "--xy-background-color-default": "hsl(var(--surface))",
  "--xy-background-pattern-dots-color-default": "hsl(var(--border))",
  "--xy-controls-button-background-color-default": "hsl(var(--card))",
  "--xy-controls-button-background-color-hover-default": "hsl(var(--surface-2))",
  "--xy-controls-button-color-default": "hsl(var(--foreground))",
  "--xy-controls-button-border-color-default": "hsl(var(--border))",
} as CSSProperties;

/** Space on a focused node selects it (React Flow) and must not also page the transcript. */
export function keepNodeSpace(event: Pick<KeyboardEvent<Element>, "key" | "target" | "preventDefault">): void {
  const target = event.target as { closest?: (selector: string) => unknown } | null;
  if (event.key === " " && target?.closest?.(".react-flow__node")) event.preventDefault();
}

/**
 * Props for every flow. React Flow's keys listen on the whole document: Backspace would delete the
 * chosen node, and Space or Shift would start a pan or a selection while the user types elsewhere, so
 * all of them are off. Nothing is dragged, the wheel scrolls the page (the buttons zoom), and links are
 * never tab stops (the text version lists them).
 */
export const FLOW_PROPS = {
  deleteKeyCode: null,
  panActivationKeyCode: null,
  selectionKeyCode: null,
  zoomActivationKeyCode: null,
  nodesDraggable: false,
  nodesConnectable: false,
  edgesFocusable: false,
  zoomOnScroll: false,
  zoomOnDoubleClick: false,
  panOnScroll: false,
  preventScrolling: false,
  fitView: true,
  fitViewOptions: { padding: 0.12, maxZoom: 1.25 },
  minZoom: 0.2,
  maxZoom: 2,
  onKeyDown: keepNodeSpace,
} satisfies Partial<ReactFlowProps>;

/** React Flow's screen-reader text in the product's words; nothing in a reply can be deleted or moved. */
export function ariaLabels(nodeHelp: string): Partial<AriaLabelConfig> {
  return {
    "node.a11yDescription.default": nodeHelp,
    "node.a11yDescription.keyboardDisabled": nodeHelp,
    "edge.a11yDescription.default": "",
    "controls.ariaLabel": "Diagram controls",
    "controls.zoomIn.ariaLabel": "Zoom in",
    "controls.zoomOut.ariaLabel": "Zoom out",
    "controls.fitView.ariaLabel": "Fit to view",
  };
}

export type Box = { id: string; width: number; height: number };
export type Direction = "TB" | "LR" | "BT" | "RL";

/** Top-left positions of boxes placed in layers by dagre; a link to an unknown id is left out. */
export function layered(
  boxes: readonly Box[],
  links: readonly { from: string; to: string }[],
  direction: Direction = "TB",
  spacing = { nodesep: 18, ranksep: 48 },
): Map<string, XYPosition> {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: direction, ...spacing, marginx: 8, marginy: 8 });
  graph.setDefaultEdgeLabel(() => ({}));
  const ids = new Set(boxes.map((box) => box.id));
  for (const box of boxes) graph.setNode(box.id, { width: box.width, height: box.height });
  for (const link of links) if (ids.has(link.from) && ids.has(link.to)) graph.setEdge(link.from, link.to);
  dagre.layout(graph);
  return new Map(
    boxes.map((box) => {
      const place = graph.node(box.id) as { x: number; y: number };
      return [box.id, { x: place.x - box.width / 2, y: place.y - box.height / 2 }];
    }),
  );
}

/**
 * The page's colour mode, whether the pointer is fine (a mouse drag pans; a finger scrolls the page),
 * and the arrowheads' colour: SVG markers take a concrete colour, read from the tokens at draw time.
 */
export function useFlowLook() {
  const { resolvedTheme } = useTheme();
  const [fine, setFine] = useState(false);
  const [marker, setMarker] = useState<string | undefined>(undefined);
  useEffect(() => setFine(window.matchMedia("(pointer: fine)").matches), []);
  useEffect(() => {
    // A frame later: the theme provider swaps the page's class in its own effect, after this one.
    const frame = requestAnimationFrame(() => setMarker(resolvedTheme ? token("text-secondary") : undefined));
    return () => cancelAnimationFrame(frame);
  }, [resolvedTheme]);
  return { fine, marker, colorMode: resolvedTheme === "dark" ? ("dark" as const) : ("light" as const) };
}
