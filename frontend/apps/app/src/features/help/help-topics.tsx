"use client";

import { Children, type ReactNode, useEffect, useState } from "react";

import { TabPanel, ViewTabs, useTabIds } from "@/components/view-tabs";

/**
 * Help's topics as tabs: one topic's questions at a time instead of one long page. The answers are rendered on the
 * server (the panels are this component's children); this only switches between them. A `#faq-N` link opens topic N.
 */
export function HelpTopics({
  topics,
  children,
}: {
  topics: readonly { label: string; count: number }[];
  children: ReactNode;
}) {
  const ids = useTabIds();
  const [open, setOpen] = useState("0");
  useEffect(() => {
    const asked = /^#faq-(\d+)$/.exec(window.location.hash)?.[1];
    if (asked && Number(asked) < topics.length) setOpen(asked);
  }, [topics.length]);
  return (
    <div className="flex flex-col gap-4">
      <ViewTabs
        label="Help topics"
        tabs={topics.map((topic, index) => ({ value: String(index), label: topic.label, count: topic.count }))}
        value={open}
        onChange={setOpen}
        ids={ids}
      />
      {Children.toArray(children).map((panel, index) => (
        <TabPanel key={topics[index]?.label ?? index} ids={ids} value={String(index)} hidden={String(index) !== open}>
          {panel}
        </TabPanel>
      ))}
    </div>
  );
}
