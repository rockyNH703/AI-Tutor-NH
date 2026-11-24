import { describe, expect, test } from "bun:test";
import { Dialog } from "@v1/ui/dialog";
import { renderToStaticMarkup } from "react-dom/server";

import { BattleGone } from "./battle-view";
import { ConfirmBody, alertDialogProps } from "./safety-confirm";

const noop = () => {};
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();

describe("Remove and Block confirmations", () => {
  test("are alertdialogs described by their consequence text", () => {
    expect(alertDialogProps("consequence")).toEqual({ role: "alertdialog", "aria-describedby": "consequence" });
    const html = renderToStaticMarkup(
      <Dialog open>
        <ConfirmBody
          title="Block this member?"
          description="Rehan isn't told. You won't see or find each other in Friend Zone."
          confirmLabel="Block"
          pending={false}
          onConfirm={noop}
          onCancel={noop}
          descriptionId="consequence"
        />
      </Dialog>,
    );
    // The element the dialog's aria-describedby names holds the consequence, word for word.
    const described = /<p[^>]*id="consequence"[^>]*>([\s\S]*?)<\/p>/.exec(html)?.[1] ?? "";
    expect(text(described)).toBe("Rehan isn't told. You won't see or find each other in Friend Zone.");
    // Cancel comes first (it takes focus when the dialog opens), then the destructive action.
    expect([...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((match) => text(match[1] ?? ""))).toEqual(["Cancel", "Block"]);
  });
});

describe("a battle that can't be shown", () => {
  test("is the view's heading, which takes focus, then why; not an alert", () => {
    const html = renderToStaticMarkup(<BattleGone />);
    expect(html).toMatch(/<h3[^>]*tabindex="-1"[^>]*>This battle isn&#x27;t available<\/h3>/i);
    expect(text(html)).toBe("This battle isn't available It may have ended, or it isn't yours.");
    expect(html).not.toContain('role="alert"');
  });
});
