// Fixtures for .semgrep/netsec.yml (semgrep --test). Never imported or built.
import { createElement } from "react";

export function Unsafe({ html, code }: { html: string; code: string }) {
  // ruleid: netsec-ts-eval
  eval(code);
  // ruleid: netsec-ts-eval
  const run = new Function("x", code);
  // ruleid: netsec-ts-eval
  window.eval(code);
  // ok: netsec-ts-eval
  const data = JSON.parse(code);
  // ruleid: netsec-react-dangerously-set-inner-html
  const el = createElement("div", { dangerouslySetInnerHTML: { __html: html } });
  // ruleid: netsec-react-dangerously-set-inner-html
  const raw = <div className="x" dangerouslySetInnerHTML={{ __html: html }} />;
  // ok: netsec-react-dangerously-set-inner-html
  const safe = <div className="x">{html}</div>;
  return [run, data, el, raw, safe];
}
