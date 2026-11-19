"use client";

import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { Check, ChevronDown, ChevronsDownUp, ChevronsUpDown, Copy, Download, NotebookText, Printer } from "lucide-react";
import {
  Children,
  Fragment,
  type MouseEvent,
  type ReactElement,
  type ReactNode,
  cloneElement,
  isValidElement,
  useContext,
  useEffect,
  useId,
  useState,
} from "react";
import { toast } from "sonner";

import { maskExamFences } from "./assessment";
import { LiveContext, ReleaseScrollContext, TestContext } from "./context";
import { saveText } from "./download";
import { type HastNode, hastText } from "./hast";
import { CardFrame } from "./study-card";

type Point = { offset?: number };
/** A syntax-tree node and where it came from in its block's text (react-markdown passes it as `node`). */
export type SheetNode = Omit<HastNode, "children"> & { position?: { start: Point; end: Point }; children?: SheetNode[] };

export type SheetPart<T> =
  | { kind: "item"; item: T }
  | { kind: "section"; id: number; heading: T; parts: SheetPart<T>[] };

/**
 * A sheet's content as sections: a heading opens one that runs to the next heading of the same or a higher
 * level, so a `###` folds inside the `##` above it. What comes before the first heading stays outside any
 * section. Ids number the sections in reading order.
 */
export function sectionize<T>(
  items: readonly T[],
  levelOf: (item: T) => number | null,
): { parts: SheetPart<T>[]; count: number } {
  const root: SheetPart<T>[] = [];
  const open: { level: number; parts: SheetPart<T>[] }[] = [{ level: 0, parts: root }];
  const top = () => open[open.length - 1] ?? { level: 0, parts: root };
  let count = 0;
  for (const item of items) {
    const level = levelOf(item);
    if (level === null) {
      top().parts.push({ kind: "item", item });
      continue;
    }
    while (open.length > 1 && top().level >= level) open.pop();
    const parts: SheetPart<T>[] = [];
    top().parts.push({ kind: "section", id: count++, heading: item, parts });
    open.push({ level, parts });
  }
  return { parts: root, count };
}

const isTitle = (node: SheetNode | undefined) => node?.properties?.dataCardTitle === "true";

/**
 * The sheet as Markdown to copy or save: its title as a `#` heading, then its body exactly as the reply
 * wrote it (the block's text sliced by the body's positions), without the `:::cheatsheet` lines.
 */
export function sheetMarkdown(source: string, node: SheetNode | undefined): string {
  const elements = (node?.children ?? []).filter((child) => child.type === "element");
  const title = hastText(elements.find(isTitle)).trim();
  const body = elements.filter((child) => !isTitle(child) && child.position?.start.offset !== undefined);
  const start = body[0]?.position?.start.offset;
  const end = body[body.length - 1]?.position?.end.offset;
  const text = start !== undefined && end !== undefined ? source.slice(start, end) : "";
  return `${title ? `# ${title}\n\n` : ""}${text}\n`;
}

/** `OSPF timers & areas` → `ospf-timers-areas.md`; a sheet with no usable title is `cheat-sheet.md`. */
export function sheetFilename(title: string): string {
  const slug = title
    .normalize("NFKD")
    .replace(/\p{M}/gu, "") // "é" → "e", not "e-"
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60)
    .replace(/-$/, "");
  return `${slug || "cheat-sheet"}.md`;
}

/**
 * Prints one sheet: a copy of what is drawn (no React render), alone on the page and in the light theme,
 * restored on `afterprint`. A clone has none of a canvas's pixels, so each is copied with drawImage.
 * Ctrl+P is untouched: without `data-print` the page prints as it always has (markdown.css).
 */
export function printSheet(sheet: HTMLElement): void {
  const html = document.documentElement;
  const dark = html.classList.contains("dark");
  const scheme = html.style.colorScheme;
  document.getElementById("print-root")?.remove();
  const root = document.createElement("div");
  const restore = () => {
    html.classList.toggle("dark", dark);
    html.style.colorScheme = scheme;
    delete html.dataset.print;
    root.remove();
  };
  try {
    const copy = sheet.cloneNode(true) as HTMLElement;
    const drawn = sheet.querySelectorAll("canvas");
    copy.querySelectorAll("canvas").forEach((canvas, index) => {
      const from = drawn[index];
      try {
        if (from) canvas.getContext("2d")?.drawImage(from, 0, 0);
      } catch {
        // An empty (0×0) canvas cannot be drawn from: it prints blank, as it is on screen.
      }
    });
    // The copy must not answer for the original's ids (aria-controls, labels).
    copy.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
    const content = document.createElement("div");
    content.className = "message-content";
    content.append(copy);
    root.id = "print-root";
    root.append(content);
    document.body.append(root);
    html.dataset.print = "sheet";
    html.classList.remove("dark");
    html.style.colorScheme = "light";
    window.addEventListener("afterprint", restore, { once: true });
    window.print();
  } catch {
    window.removeEventListener("afterprint", restore);
    restore();
    toast.error("Couldn't open printing.");
  }
}

const SECTION_LEVEL: Record<string, number> = { h2: 2, h3: 3 };

function nodeOf(item: ReactNode): SheetNode | undefined {
  return isValidElement(item) ? (item.props as { node?: SheetNode }).node : undefined;
}

const ACTION = "print-control min-w-11 px-2.5 text-text-secondary hover:text-foreground";

/**
 * A `:::cheatsheet[Title]` (remark-tutor.ts): its `##`/`###` sections fold under their headings, all open
 * at first, while the reply streams and after. Folded bodies stay mounted (hidden by a class), so a widget
 * inside keeps its state and nothing is drawn again. The header copies or saves the sheet as Markdown (practice keys masked, as Copy reply
 * does), prints it on its own, and folds or opens every section. `source` is the block's text the node's
 * positions point into (message-content.tsx).
 */
export function Cheatsheet({ node, source, children }: { node?: SheetNode; source: string; children?: ReactNode }) {
  const live = useContext(LiveContext);
  const tests = useContext(TestContext);
  const release = useContext(ReleaseScrollContext);
  const id = useId();
  const [closed, setClosed] = useState<ReadonlySet<number>>(() => new Set());
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const items = Children.toArray(children);
  const titleItem = items.find((item) => isTitle(nodeOf(item)));
  const title = isValidElement<{ children?: ReactNode }>(titleItem) ? titleItem.props.children : null;
  const { parts, count } = sectionize(
    items.filter((item) => item !== titleItem),
    (item) => SECTION_LEVEL[nodeOf(item)?.tagName ?? ""] ?? null,
  );
  const allClosed = count > 0 && closed.size >= count;

  const toggle = (section: number) => {
    if (closed.has(section)) release();
    setClosed((now) => {
      const next = new Set(now);
      if (!next.delete(section)) next.add(section);
      return next;
    });
  };
  const toggleAll = () => {
    if (allClosed) release();
    setClosed(allClosed ? new Set() : new Set(Array.from({ length: count }, (_, index) => index)));
  };

  const markdown = () => maskExamFences(sheetMarkdown(source, node), tests?.assessments, tests ? tests.streaming : live);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(markdown());
      setCopied(true);
    } catch {
      toast.error("Couldn't copy to the clipboard.");
    }
  };
  const download = () => saveText(sheetFilename(hastText(node?.children?.find(isTitle))), markdown());
  const print = (event: MouseEvent<HTMLButtonElement>) => {
    const sheet = event.currentTarget.closest<HTMLElement>('[data-card="cheatsheet"]');
    if (sheet) printSheet(sheet);
  };

  const draw = (list: SheetPart<ReactNode>[]): ReactNode[] =>
    list.map((part) => {
      if (part.kind === "item") return part.item;
      const open = !closed.has(part.id);
      const body = `${id}-section-${part.id}`;
      const heading = part.heading as ReactElement<{ children?: ReactNode }>;
      return (
        <Fragment key={`section-${part.id}`}>
          {cloneElement(heading, {
            children: (
              <button
                type="button"
                aria-expanded={open}
                aria-controls={body}
                onClick={() => toggle(part.id)}
                className="focus-ring -mx-2 flex min-h-11 w-[calc(100%+1rem)] items-center gap-2 rounded-lg px-2 text-left transition-colors hover:bg-surface-2 sm:min-h-10"
              >
                <span className="min-w-0 flex-1 break-words">{heading.props.children}</span>
                <ChevronDown
                  aria-hidden
                  className={cn("print-control h-4 w-4 shrink-0 text-text-secondary motion-safe:transition-transform", !open && "-rotate-90")}
                />
              </button>
            ),
          })}
          <div id={body} className={open ? undefined : "cheatsheet-closed"}>
            {draw(part.parts)}
          </div>
        </Fragment>
      );
    });

  const actions = (
    <>
      <Button type="button" variant="ghost" className={ACTION} onClick={toggleAll}>
        {allClosed ? <ChevronsUpDown aria-hidden className="h-4 w-4" /> : <ChevronsDownUp aria-hidden className="h-4 w-4" />}
        {allClosed ? "Expand all" : "Collapse all"}
      </Button>
      <Button type="button" variant="ghost" className={ACTION} aria-label="Copy as Markdown" onClick={() => void copy()}>
        {copied ? <Check aria-hidden className="h-4 w-4" /> : <Copy aria-hidden className="h-4 w-4" />}
        <span className="hidden sm:inline">Copy</span>
      </Button>
      <Button type="button" variant="ghost" className={ACTION} aria-label="Download .md" onClick={download}>
        <Download aria-hidden className="h-4 w-4" />
        <span className="hidden sm:inline">Download</span>
      </Button>
      <Button type="button" variant="ghost" className={ACTION} aria-label="Print cheat sheet" onClick={print}>
        <Printer aria-hidden className="h-4 w-4" />
        <span className="hidden sm:inline">Print</span>
      </Button>
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied" : ""}
      </span>
    </>
  );

  return (
    <CardFrame kind="cheatsheet" icon={NotebookText} label="Cheat sheet" title={title} actions={actions}>
      {draw(parts)}
    </CardFrame>
  );
}
