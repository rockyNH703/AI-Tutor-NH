"use client";

import "@xyflow/react/dist/style.css";
import { Background, Controls, type Edge, MarkerType, type Node, Position, ReactFlow } from "@xyflow/react";
import { Network } from "lucide-react";
import { useCallback, useContext, useEffect, useMemo, useState } from "react";
import { z } from "zod";

import { ReleaseScrollContext } from "../context";
import type { WidgetProps } from "../rich-block";
import { CardFrame } from "../study-card";
import { ariaLabels, FLOW_PROPS, FLOW_THEME, layered, useFlowLook } from "./flow";
import { parseJson } from "./tokens";

const Spec = z.object({
  title: z.string().max(200).optional(),
  direction: z.enum(["TB", "LR", "BT", "RL"]).optional(),
  nodes: z
    .array(z.object({ id: z.string().min(1).max(40), label: z.string().min(1).max(80), detail: z.string().max(600).optional() }))
    .min(1)
    .max(40),
  edges: z
    .array(z.object({ from: z.string().min(1).max(40), to: z.string().min(1).max(40), label: z.string().max(40).optional() }))
    .max(80)
    .optional(),
});
type DiagramSpec = z.infer<typeof Spec>;

const WIDTH = 150;
const LABELS = ariaLabels("Press Enter to read about this box.");

/** Boxes placed in layers by dagre, arrows between them. */
export function layout(spec: DiagramSpec): { nodes: Node[]; edges: Edge[] } {
  const direction = spec.direction ?? "TB";
  const ids = new Set(spec.nodes.map((node) => node.id));
  const height = (label: string) => Math.min(96, 44 + 18 * Math.floor(label.length / 22));
  const edges = (spec.edges ?? []).filter((edge) => ids.has(edge.from) && ids.has(edge.to));
  const places = layered(
    spec.nodes.map((node) => ({ id: node.id, width: WIDTH, height: height(node.label) })),
    edges,
    direction,
  );
  const across = direction === "LR" || direction === "RL";
  const label = (id: string) => spec.nodes.find((node) => node.id === id)?.label ?? id;
  return {
    nodes: spec.nodes.map((node) => ({
      id: node.id,
      position: places.get(node.id) ?? { x: 0, y: 0 },
      data: { label: node.label },
      ariaLabel: node.label,
      style: { width: WIDTH, fontSize: 13, lineHeight: 1.35 },
      sourcePosition: across ? Position.Right : Position.Bottom,
      targetPosition: across ? Position.Left : Position.Top,
      connectable: false,
    })),
    edges: edges.map((edge, index) => ({
      id: `e${index}`,
      source: edge.from,
      target: edge.to,
      label: edge.label,
      ariaLabel: `${label(edge.from)} to ${label(edge.to)}${edge.label ? ` (${edge.label})` : ""}`,
      markerEnd: { type: MarkerType.ArrowClosed },
    })),
  };
}

/**
 * ```diagram: a concept map to explore. Boxes are laid out by dagre and drawn by React Flow;
 * choosing one (click, or focus and Enter) shows its detail below. On touch screens the diagram
 * never captures the page's scroll: it zooms with its buttons, and the text version is below.
 */
export default function ConceptDiagram({ source, onError }: WidgetProps) {
  const spec = useMemo(() => {
    const parsed = Spec.safeParse(parseJson(source));
    return parsed.success ? parsed.data : null;
  }, [source]);
  const flow = useMemo(() => (spec ? layout(spec) : null), [spec]);
  const [selected, setSelected] = useState<string | null>(null);
  const look = useFlowLook();
  const release = useContext(ReleaseScrollContext);

  useEffect(() => {
    if (!spec) onError();
  }, [spec, onError]);
  const onSelectionChange = useCallback(
    ({ nodes }: { nodes: Node[] }) => {
      if (nodes[0]) release(); // the detail grows the card: don't let "stick to the bottom" scroll it away
      setSelected(nodes[0]?.id ?? null);
    },
    [release],
  );

  if (!spec || !flow) return null;
  const chosen = spec.nodes.find((node) => node.id === selected);
  const label = (id: string) => spec.nodes.find((node) => node.id === id)?.label ?? id;

  return (
    <CardFrame kind="diagram" icon={Network} label="Diagram" title={spec.title}>
      <div className="h-80 w-full min-w-0 overflow-hidden rounded-lg border border-border sm:h-96">
        <ReactFlow
          {...FLOW_PROPS}
          defaultNodes={flow.nodes}
          defaultEdges={flow.edges}
          style={FLOW_THEME}
          elementsSelectable
          panOnDrag={look.fine}
          defaultMarkerColor={look.marker}
          ariaLabelConfig={LABELS}
          onSelectionChange={onSelectionChange}
          colorMode={look.colorMode}
        >
          <Background gap={16} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <div aria-live="polite" className="mt-2 min-h-6 text-sm leading-6">
        {chosen ? (
          <p>
            <span className="font-semibold">{chosen.label}</span>
            {chosen.detail ? <span className="text-text-secondary">: {chosen.detail}</span> : null}
          </p>
        ) : spec.nodes.some((node) => node.detail) ? (
          <p className="text-text-secondary">Choose a box to read more about it.</p>
        ) : null}
      </div>
      <details className="mt-1 text-sm">
        {/* biome-ignore lint/a11y/useKeyWithClickEvents: a summary is a native button; Enter and Space click it */}
        <summary
          onClick={release}
          className="focus-ring w-fit cursor-pointer rounded-md text-text-secondary hover:text-foreground"
        >
          The diagram as text
        </summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 leading-6">
          {spec.nodes.map((node) => (
            <li key={node.id}>
              <span className="font-medium">{node.label}</span>
              {node.detail ? `: ${node.detail}` : ""}
              {(spec.edges ?? [])
                .filter((edge) => edge.from === node.id)
                .map((edge) => ` → ${label(edge.to)}${edge.label ? ` (${edge.label})` : ""}`)
                .join("")}
            </li>
          ))}
        </ul>
      </details>
    </CardFrame>
  );
}
