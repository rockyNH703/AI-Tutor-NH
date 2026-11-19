"use client";

import type { AssessmentScenario } from "@/lib/api/types";
import { InlineMarkdown } from "../../message-content";
import { ListingView } from "./questions/listing";

/**
 * A test's scenario (one per test), shown above its questions: what happened, the symptoms and the
 * evidence (configs, logs, output) as numbered listings. `titleId` names the section; each question's
 * group is described by `titleId` and `summaryId` (the summary only: a screen reader never re-reads
 * every listing on entering a question).
 */
export function ScenarioPanel({
  scenario,
  titleId,
  summaryId,
}: {
  scenario: AssessmentScenario;
  titleId: string;
  summaryId: string;
}) {
  return (
    <section aria-labelledby={titleId} className="mb-3 min-w-0 border-b border-border pb-3 text-sm leading-6">
      <h3 id={titleId} className="font-semibold">
        {scenario.title ?? "Scenario"}
      </h3>
      <p id={summaryId} className="mt-1 break-words">
        <InlineMarkdown text={scenario.summary} />
      </p>
      {scenario.symptoms.length > 0 ? (
        <>
          <p className="mt-2 font-medium">Symptoms</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {scenario.symptoms.map((symptom, index) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: symptoms are positions in the scenario
              <li key={index} className="break-words">
                <InlineMarkdown text={symptom} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {scenario.evidence.map((evidence, index) => (
        <ListingView
          // biome-ignore lint/suspicious/noArrayIndexKey: evidence items are positions in the scenario
          key={index}
          listing={{ ...evidence, title: evidence.title ? `${evidence.label}: ${evidence.title}` : evidence.label }}
        />
      ))}
    </section>
  );
}
