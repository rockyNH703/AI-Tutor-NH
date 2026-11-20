"use client";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@v1/ui/table";
import type { ComponentPropsWithoutRef, JSX, ReactNode } from "react";
import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { CodeBlock } from "@/features/markdown/code-block";
import { type HastNode, hastText, languageOf, rehypeMarkBlockCode, rehypeRawAsText } from "@/features/markdown/hast";
import { safeUrl, sanitizeSchema } from "@/features/markdown/sanitize-schema";

type Plugins = NonNullable<ComponentPropsWithoutRef<typeof ReactMarkdown>["remarkPlugins"]>;
type WithNode<T extends keyof JSX.IntrinsicElements> = ComponentPropsWithoutRef<T> & { node?: HastNode };

/**
 * NHprep's imported notes, cheat sheets and answers: Markdown NetSec AI didn't write. The reply renderer's safe
 * base (GFM, raw HTML as text, the sanitizer, http(s)/mailto links only) but none of the tutor's syntax: no study
 * cards, no maths, and no widgets, so a ```video or ```quiz fence in a note stays plain code. A link or image
 * pointing at nhprep.com (or any of its subdomains) is shown as its text: NetSec AI never sends anyone there.
 */
const remarkPlugins = [[remarkGfm, { singleTilde: false }]] as Plugins;
const rehypePlugins = [rehypeRawAsText, [rehypeSanitize, sanitizeSchema], rehypeMarkBlockCode] as Plugins;

/** True for nhprep.com and every host under it (www., videos., …); false for lookalikes (nhprep.com.evil.tld). */
export function isNhprepHost(url: string): boolean {
  try {
    const host = new URL(url).hostname.toLowerCase().replace(/\.$/, "");
    return host === "nhprep.com" || host.endsWith(".nhprep.com");
  } catch {
    return false;
  }
}

function Link({ href, children }: WithNode<"a">) {
  const url = href ? safeUrl(href) : undefined;
  if (!url || isNhprepHost(url)) return <span>{children}</span>;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className="font-medium text-brand underline decoration-brand/40 underline-offset-2 transition-colors hover:decoration-brand"
    >
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

/** Never loaded, never linked: the alt text in brackets. */
function Image({ alt }: WithNode<"img">) {
  return <span className="text-text-secondary">[{alt?.trim() ? `Image: ${alt.trim()}` : "Image"}]</span>;
}

function Code({ node, className, children }: WithNode<"code">) {
  if (node?.properties?.dataBlock) return <code className={className}>{children}</code>;
  return <code className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[0.875em] text-foreground">{children}</code>;
}

function Pre({ node, children }: WithNode<"pre">) {
  const code = node?.children?.find((child) => child.type === "element" && child.tagName === "code");
  return (
    <CodeBlock language={languageOf(code?.properties?.className)} text={hastText(code)}>
      {children}
    </CodeBlock>
  );
}

const heading =
  (Tag: "h4" | "h5" | "h6", className: string) =>
  ({ children }: { children?: ReactNode }) => <Tag className={className}>{children}</Tag>;

// Inside a card inside a reply: headings start at h4.
const components: Components = {
  h1: heading("h4", "mb-2 mt-4 text-lg font-semibold first:mt-0"),
  h2: heading("h4", "mb-2 mt-4 text-lg font-semibold first:mt-0"),
  h3: heading("h5", "mb-1.5 mt-3 text-base font-semibold first:mt-0"),
  h4: heading("h6", "mb-1 mt-3 text-base font-semibold first:mt-0"),
  h5: heading("h6", "mb-1 mt-3 text-base font-semibold first:mt-0"),
  h6: heading("h6", "mb-1 mt-3 text-base font-semibold text-text-secondary first:mt-0"),
  p: ({ children }) => <p className="my-3 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children, className }) => (
    <ul className={`my-3 space-y-1 pl-6 ${className?.includes("contains-task-list") ? "list-none pl-1" : "list-disc"}`}>
      {children}
    </ul>
  ),
  ol: ({ children, start }) => (
    <ol start={start} className="my-3 list-decimal space-y-1 pl-6">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1 marker:text-text-secondary">{children}</li>,
  a: Link,
  img: Image,
  code: Code,
  pre: Pre,
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 border-border pl-4 text-text-secondary">{children}</blockquote>
  ),
  hr: () => <hr className="my-6 border-border" />,
  table: ({ children }) => (
    <div className="my-3">
      <Table label="Table" className="min-w-full text-sm">
        {children}
      </Table>
    </div>
  ),
  thead: ({ children }) => <TableHeader>{children}</TableHeader>,
  tbody: ({ children }) => <TableBody>{children}</TableBody>,
  tr: ({ children }) => <TableRow>{children}</TableRow>,
  th: ({ children, style }) => (
    <TableHead style={style} className="whitespace-nowrap">
      {children}
    </TableHead>
  ),
  td: ({ children, style }) => <TableCell style={style}>{children}</TableCell>,
  input: ({ type, checked }) =>
    type === "checkbox" ? <input type="checkbox" checked={Boolean(checked)} disabled readOnly className="mr-1.5 align-middle" /> : null,
};

const urlTransform = (url: string) => safeUrl(url) ?? "";

export const NhprepMarkdown = memo(function NhprepMarkdown({ text }: { text: string }) {
  return (
    <div className="min-w-0 break-words text-base">
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components} urlTransform={urlTransform}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
