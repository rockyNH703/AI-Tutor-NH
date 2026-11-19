"use client";

import "@xyflow/react/dist/style.css";
import { cn } from "@v1/ui/utils";
import {
  Background,
  Controls,
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  type XYPosition,
} from "@xyflow/react";
import {
  BrickWall,
  Box,
  Cloud,
  EthernetPort,
  Globe,
  Layers3,
  type LucideIcon,
  Monitor,
  Network,
  Router,
  Server,
  Wifi,
} from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { z } from "zod";

import type { Diagram, DiagramLink, DiagramNode, QuestionSelect } from "@/lib/api/types";
import { ReleaseScrollContext } from "../context";
import type { WidgetProps } from "../rich-block";
import { CardFrame } from "../study-card";
import { ariaLabels, FLOW_PROPS, FLOW_THEME, layered, useFlowLook } from "./flow";
import { type ElementState, MARKS, MarkGlyph } from "./test/questions/listing";
import { parseJson } from "./tokens";

// ------------------------------------------------------------------------------------------ the block

const ID = /^[A-Za-z0-9_.:/-]{1,40}$/;
/** Text as the server accepts it (app/assessments/readers.py text_ok): not blank, at most `max`, no NUL. */
const text = (max: number) =>
  z
    .string()
    .max(max)
    .refine((value) => value.trim() !== "" && !value.includes("\0"));
const optional = (max: number) => text(max).nullish();

/**
 * A ```topology block, and a diagram question's topology (the server's parse_topology): 1-30 devices and
 * at most 40 links, ids unique ignoring case across both, every link between two of its devices.
 */
export const TopologySpec = z
  .object({
    title: z.string().max(200).nullish(),
    nodes: z
      .array(z.object({ id: z.string().regex(ID), label: text(40), kind: text(40), detail: optional(300) }))
      .min(1)
      .max(30),
    links: z
      .array(
        z.object({
          id: z.string().regex(ID),
          from: z.string(),
          to: z.string(),
          label: optional(40),
          detail: optional(300),
        }),
      )
      .max(40)
      .nullish()
      .transform((links) => links ?? []),
  })
  .superRefine((spec, context) => {
    const devices = new Set(spec.nodes.map((node) => node.id));
    if (spec.links.some((link) => !devices.has(link.from) || !devices.has(link.to))) {
      context.addIssue({ code: "custom", message: "a link to an unknown device" });
    }
    const ids = [...spec.nodes, ...spec.links].map((item) => item.id.toLowerCase());
    if (new Set(ids).size !== ids.length) context.addIssue({ code: "custom", message: "two ids are the same" });
  });

/** The block's topology, or null when the model wrote something that isn't one. */
export function parseTopology(source: string): (Diagram & { title?: string | null }) | null {
  const parsed = TopologySpec.safeParse(parseJson(source));
  return parsed.success ? parsed.data : null;
}

// ------------------------------------------------------------------------------------------ names

// A Map, not an object: a kind is the model's text, and "constructor" must not find Object's.
const KINDS = new Map<string, { Icon: LucideIcon; name: string }>([
  ["router", { Icon: Router, name: "router" }],
  ["switch", { Icon: EthernetPort, name: "switch" }],
  ["l3switch", { Icon: Layers3, name: "layer 3 switch" }],
  ["firewall", { Icon: BrickWall, name: "firewall" }],
  ["server", { Icon: Server, name: "server" }],
  ["host", { Icon: Monitor, name: "host" }],
  ["cloud", { Icon: Cloud, name: "cloud" }],
  ["ap", { Icon: Wifi, name: "access point" }],
  ["internet", { Icon: Globe, name: "internet" }],
]);

/** A device kind's icon and words; any other kind keeps the author's word and a plain box. */
export function kindLook(kind: string): { Icon: LucideIcon; name: string } {
  return KINDS.get(kind.trim().toLowerCase()) ?? { Icon: Box, name: kind.trim() };
}

/** "R1 (router)" */
export function deviceName(node: Pick<DiagramNode, "label" | "kind">): string {
  return `${node.label} (${kindLook(node.kind).name})`;
}

/** "R1 to SW1 (Gi0/1)" */
export function linkName(diagram: Diagram, link: DiagramLink): string {
  const label = (id: string) => diagram.nodes.find((node) => node.id === id)?.label ?? id;
  return `${label(link.from)} to ${label(link.to)}${link.label ? ` (${link.label})` : ""}`;
}

export function elementName(diagram: Diagram, id: string): string {
  const node = diagram.nodes.find((each) => each.id === id);
  if (node) return deviceName(node);
  const link = diagram.links.find((each) => each.id === id);
  return link ? linkName(diagram, link) : id;
}

/** What a diagram question lets you choose, devices first, in the author's order. */
export function choices(
  diagram: Diagram,
  select: QuestionSelect | null | undefined,
): { id: string; name: string; detail: string | null }[] {
  return [
    ...(select === "link" ? [] : diagram.nodes.map((node) => ({ id: node.id, name: deviceName(node), detail: node.detail ?? null }))),
    ...(select === "node"
      ? []
      : diagram.links.map((link) => ({ id: link.id, name: linkName(diagram, link), detail: link.detail ?? null }))),
  ];
}

// ------------------------------------------------------------------------------------------ the canvas

type DeviceData = { label: string; kind: string };
const NONE: ReadonlyMap<string, ElementState> = new Map();
const DrawnContext = createContext<{ states: ReadonlyMap<string, ElementState>; clickable: boolean }>({
  states: NONE,
  clickable: false,
});

/** One device: its kind's icon, its label and kind; chosen = brand border + check, a result = its mark. */
function DeviceNode({ id, data, selected }: NodeProps<Node<DeviceData>>) {
  const { states, clickable } = useContext(DrawnContext);
  const state = states.get(id);
  const { Icon, name } = kindLook(data.kind);
  return (
    <div
      className={cn(
        "relative flex w-44 items-center gap-2 rounded-lg border bg-card px-2.5 py-2 text-left text-foreground",
        selected || (state && state !== "missed") ? "border-brand" : "border-border",
        state === "missed" && "border-dashed",
        clickable && "cursor-pointer",
      )}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} className="invisible" />
      <Icon aria-hidden className="h-5 w-5 shrink-0 text-brand" />
      <span className="min-w-0 flex-1">
        <span className="block break-words text-xs font-medium leading-snug">{data.label}</span>
        <span className="block text-2xs text-text-secondary">{name}</span>
      </span>
      {state ? (
        <span className="absolute -right-2 -top-2 rounded-full bg-card">
          <MarkGlyph state={state} />
          <span className="sr-only">, {MARKS[state].word}</span>
        </span>
      ) : null}
      <Handle type="source" position={Position.Bottom} isConnectable={false} className="invisible" />
    </div>
  );
}

const NODE_TYPES = { device: DeviceNode };
const WIDTH = 176; // w-44
const GLYPH: Record<ElementState, string> = { chosen: "✓", right: "✓", wrong: "✗", missed: "◌" };
const VIEW_LABELS = ariaLabels("Press Enter to read about this device.");
const QUIET_LABELS = ariaLabels("");

/** Devices placed top to bottom by dagre; the viewer's are tab stops named "R1 (router)". */
export function deviceNodes(diagram: Diagram, focusable: boolean): Node<DeviceData>[] {
  const height = (label: string) => 44 + 16 * Math.ceil(label.length / 15);
  const places = layered(
    diagram.nodes.map((node) => ({ id: node.id, width: WIDTH, height: height(node.label) })),
    diagram.links,
    "TB",
    { nodesep: 28, ranksep: 64 },
  );
  return diagram.nodes.map((node) => ({
    id: node.id,
    type: "device",
    position: places.get(node.id) ?? { x: 0, y: 0 },
    data: { label: node.label, kind: node.kind },
    // Only a tab stop (role group) may carry a name; the others are read by their text.
    ariaLabel: focusable ? deviceName(node) : undefined,
    connectable: false,
  }));
}

/**
 * Links as lines with their labels; a chosen or marked one is drawn in brand with its glyph. A link has
 * no direction, so it is drawn from the upper device down (dagre turns one link of a ring upwards).
 */
export function linkEdges(
  diagram: Diagram,
  states: ReadonlyMap<string, ElementState>,
  places?: ReadonlyMap<string, XYPosition>,
): Edge[] {
  return diagram.links.map((link) => {
    const state = states.get(link.id);
    const down = (places?.get(link.from)?.y ?? 0) <= (places?.get(link.to)?.y ?? 0);
    return {
      id: link.id,
      source: down ? link.from : link.to,
      target: down ? link.to : link.from,
      type: "straight",
      label: [state ? GLYPH[state] : "", link.label ?? ""].filter(Boolean).join(" ") || undefined,
      ariaLabel: `Link ${linkName(diagram, link)}${state ? `, ${MARKS[state].word}` : ""}`,
      selectable: false,
      focusable: false,
      style:
        state === "missed"
          ? { strokeDasharray: "6 4", strokeWidth: 2 }
          : state
            ? { stroke: "hsl(var(--brand))", strokeWidth: 3 }
            : undefined,
    };
  });
}

type CanvasProps = {
  diagram: Diagram;
  /** The viewer: a device was chosen (Enter or a click) to read its detail; null when none is. */
  onShow?: (id: string | null) => void;
  /** A question: the answer (or the result's marks) drawn on the devices and links. */
  states?: ReadonlyMap<string, ElementState>;
  /** A question: a click on a device or link of the kind it asks for adds or removes it. */
  onToggle?: (id: string) => void;
  select?: QuestionSelect | null;
};

/**
 * The topology drawn by React Flow. The viewer's devices are tab stops (Enter shows the detail); a
 * question's are not (its checkbox list is the keyboard path; a click here toggles the same answer); a
 * result's are only drawn. Links are never tab stops. On touch screens the page keeps its scroll: the
 * buttons zoom.
 */
export function TopologyCanvas({ diagram, onShow, states = NONE, onToggle, select }: CanvasProps) {
  const look = useFlowLook();
  const viewer = onShow !== undefined;
  const nodes = useMemo(() => deviceNodes(diagram, viewer), [diagram, viewer]);
  const edges = useMemo(
    () => linkEdges(diagram, states, new Map(nodes.map((node) => [node.id, node.position]))),
    [diagram, states, nodes],
  );
  const nodeToggle = onToggle && select !== "link" ? onToggle : undefined;
  const linkToggle = onToggle && select !== "node" ? onToggle : undefined;
  const drawn = useMemo(() => ({ states, clickable: Boolean(nodeToggle) }), [states, nodeToggle]);
  const showSelected = useCallback(({ nodes: chosen }: { nodes: Node[] }) => onShow?.(chosen[0]?.id ?? null), [onShow]);

  return (
    <DrawnContext.Provider value={drawn}>
      <div className="h-80 w-full min-w-0 overflow-hidden rounded-lg border border-border sm:h-96">
        <ReactFlow
          {...FLOW_PROPS}
          defaultNodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          style={FLOW_THEME}
          colorMode={look.colorMode}
          panOnDrag={look.fine}
          elementsSelectable={viewer}
          nodesFocusable={viewer}
          ariaLabelConfig={viewer ? VIEW_LABELS : QUIET_LABELS}
          onSelectionChange={viewer ? showSelected : undefined}
          onNodeClick={nodeToggle && ((_, node) => nodeToggle(node.id))}
          onEdgeClick={linkToggle && ((_, edge) => linkToggle(edge.id))}
        >
          <Background gap={16} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </DrawnContext.Provider>
  );
}

/** Every device and link in words, details included (the diagram's text version). */
export function TopologyText({ diagram }: { diagram: Diagram }) {
  return (
    <div className="mt-1 space-y-1 leading-6">
      <p className="font-medium">Devices</p>
      <ul className="list-disc pl-5">
        {diagram.nodes.map((node) => (
          <li key={node.id} className="break-words">
            {deviceName(node)}
            {node.detail ? `: ${node.detail}` : ""}
          </li>
        ))}
      </ul>
      {diagram.links.length > 0 ? (
        <>
          <p className="font-medium">Links</p>
          <ul className="list-disc pl-5">
            {diagram.links.map((link) => (
              <li key={link.id} className="break-words">
                {linkName(diagram, link)}
                {link.detail ? `: ${link.detail}` : ""}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

/**
 * ```topology: a network drawn from devices and links. Choosing a device (a click, or focus and Enter)
 * shows its detail below; the text version lists every device and link with its detail.
 */
export default function TopologyBlock({ source, onError }: WidgetProps) {
  const spec = useMemo(() => parseTopology(source), [source]);
  const release = useContext(ReleaseScrollContext);
  const [shown, setShown] = useState<string | null>(null);
  const show = useCallback(
    (id: string | null) => {
      if (id) release(); // the detail grows the card: don't let "stick to the bottom" scroll it away
      setShown(id);
    },
    [release],
  );

  useEffect(() => {
    if (!spec) onError();
  }, [spec, onError]);
  if (!spec) return null;
  const device = spec.nodes.find((node) => node.id === shown);

  return (
    <CardFrame kind="topology" icon={Network} label="Network diagram" title={spec.title ?? undefined}>
      <TopologyCanvas diagram={spec} onShow={show} />
      <div aria-live="polite" className="mt-2 min-h-6 text-sm leading-6">
        {device ? (
          <p className="break-words">
            <span className="font-semibold">{deviceName(device)}</span>
            {device.detail ? <span className="text-text-secondary">: {device.detail}</span> : null}
          </p>
        ) : spec.nodes.some((node) => node.detail) ? (
          <p className="text-text-secondary">Choose a device to read more about it.</p>
        ) : null}
      </div>
      <details className="mt-1 text-sm">
        {/* biome-ignore lint/a11y/useKeyWithClickEvents: a summary is a native button; Enter and Space click it */}
        <summary
          onClick={release}
          className="focus-ring w-fit cursor-pointer rounded-md text-text-secondary hover:text-foreground"
        >
          Devices and links as text
        </summary>
        <TopologyText diagram={spec} />
      </details>
    </CardFrame>
  );
}
