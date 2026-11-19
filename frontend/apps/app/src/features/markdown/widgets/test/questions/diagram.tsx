"use client";

import { cn } from "@v1/ui/utils";
import { useId, useMemo } from "react";

import type { Diagram } from "@/lib/api/types";
import { choices, elementName, TopologyCanvas } from "../../topology";
import type { KindPack } from "../kinds";
import type { InputProps, KindModule, ResultProps } from "../question-props";
import { FieldError, asTexts, ids, toggled } from "./choice";
import { type ElementState, Outcome, stateOf } from "./listing";

/** Each device and link drawn as chosen (no `right`) or with its result mark. */
export function drawnStates(diagram: Diagram, chosen: readonly string[], right?: readonly string[] | null) {
  const states = new Map<string, ElementState>();
  for (const { id } of [...diagram.nodes, ...diagram.links]) {
    const state = stateOf(id, chosen, right);
    if (state) states.set(id, state);
  }
  return states;
}

/** "R1 (router), R1 to SW1 (Gi0/1)" or "nothing". */
function names(diagram: Diagram | null | undefined, picked: readonly string[]): string {
  return diagram && picked.length > 0 ? picked.map((id) => elementName(diagram, id)).join(", ") : "nothing";
}

/**
 * Choose devices and links on the topology. The checkbox list is the keyboard and screen-reader path;
 * a click on the drawing toggles the same answer, which both draw (check and brand border).
 */
function DiagramInput({ question, value, onChange, disabled, describedBy, invalid }: InputProps<string[]>) {
  const errorId = useId();
  const diagram = question.diagram;
  const states = useMemo(() => (diagram ? drawnStates(diagram, value) : undefined), [diagram, value]);
  if (!diagram) return null;
  const toggle = (id: string) => onChange(toggled(value, id));
  return (
    <div className="space-y-2">
      <TopologyCanvas diagram={diagram} states={states} onToggle={disabled ? undefined : toggle} select={question.select} />
      <fieldset aria-describedby={ids(describedBy, invalid && errorId)} className="min-w-0">
        <legend className="mb-1.5 text-sm font-medium">Devices and links</legend>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {choices(diagram, question.select).map((choice) => {
            const checked = value.includes(choice.id);
            return (
              <label
                key={choice.id}
                className={cn(
                  "flex min-h-11 cursor-pointer items-start gap-2.5 rounded-lg border bg-card px-3 py-2 text-sm leading-6 transition-colors hover:border-brand/50",
                  checked ? "border-brand" : "border-border",
                )}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(choice.id)}
                  disabled={disabled}
                  className="mt-1.5 shrink-0 accent-brand"
                />
                <span className="min-w-0 flex-1 break-words">
                  {choice.name}
                  {choice.detail ? <span className="block text-xs text-text-secondary">{choice.detail}</span> : null}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <FieldError id={errorId} text={invalid} />
    </div>
  );
}

/** What was chosen and what was right, by name; in a review, the topology with both marked. */
function DiagramResult({ question, number, open, compact }: ResultProps) {
  const diagram = question.diagram;
  const chosen = asTexts(question.response?.answer) ?? [];
  const right = question.accepted ?? [];
  return (
    <Outcome
      question={question}
      number={number}
      open={open}
      given={`You chose ${names(diagram, chosen)}.`}
      right={`The answer is ${names(diagram, right)}.`}
    >
      {compact && diagram ? <TopologyCanvas diagram={diagram} states={drawnStates(diagram, chosen, right)} /> : null}
    </Outcome>
  );
}

export const diagram: KindModule<string[]> = {
  Input: DiagramInput,
  Result: DiagramResult,
  empty: () => [],
  isComplete: (_question, value) => value.length > 0,
  toRequest: (_question, value) => [...value].sort(),
  fromRecord: (question) => asTexts(question.draft) ?? asTexts(question.response?.answer),
};

const pack: KindPack = { diagram };

export default pack;
