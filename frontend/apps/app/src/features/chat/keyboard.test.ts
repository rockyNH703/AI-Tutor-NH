import { describe, expect, test } from "bun:test";
import { shouldSubmit } from "./keyboard";
import { plainText } from "./plain-text";

describe("shouldSubmit", () => {
  test("Enter sends", () => {
    expect(shouldSubmit({ key: "Enter", shiftKey: false })).toBe(true);
  });

  test("Shift+Enter is a new line", () => {
    expect(shouldSubmit({ key: "Enter", shiftKey: true })).toBe(false);
  });

  test("Enter confirming an IME composition does not send", () => {
    expect(shouldSubmit({ key: "Enter", shiftKey: false, nativeEvent: { isComposing: true } })).toBe(false);
    expect(shouldSubmit({ key: "Enter", shiftKey: false, isComposing: true })).toBe(false);
    // Safari reports composition only through the legacy keyCode.
    expect(shouldSubmit({ key: "Enter", shiftKey: false, keyCode: 229 })).toBe(false);
  });

  test("other keys never send", () => {
    expect(shouldSubmit({ key: "a", shiftKey: false })).toBe(false);
  });
});

describe("plainText (screen-reader announcement)", () => {
  test("strips Markdown syntax", () => {
    expect(plainText("# Title\n\n**Bold** and `code` with a [link](https://x.y).\n\n- item")).toBe(
      "Title Bold and code with a link. item",
    );
  });

  test("summarises code blocks instead of reading them", () => {
    expect(plainText("Here:\n```js\nconst x = 1;\n```\nDone.")).toBe("Here: (code) Done.");
  });

  test("names drawn blocks instead of reading them, and never reads a test's answers", () => {
    const exam = '```quiz\n{"mode": "exam", "questions": [{"question": "Q?", "options": ["a", "b"], "answer": 1}]}\n```';
    expect(plainText(`Try:\n${exam}\nGo.`)).toBe("Try: (practice test) Go.");
    expect(plainText("~~~mcq\n{\"answer\": 1}\n~~~")).toBe("(practice test)");
    expect(plainText("```flashcards\n[]\n```")).toBe("(flashcards)");
    expect(plainText('```video\n{"youtube": "abc"}\n```')).toBe("(video)");
    expect(plainText("```topology\n{}\n```")).toBe("(network diagram)");
    expect(plainText("```diagram\n{}\n```\n```mermaid\ngraph TD\n```")).toBe("(diagram) (diagram)");
    expect(plainText("```chart\n{}\n```\n```graph\n{}\n```")).toBe("(chart) (chart)");
    // In a list item or a quote, and still unclosed: the same.
    expect(plainText("- ```quiz\n  {\"answer\": 1}")).toBe("(practice test)");
    expect(plainText("> ~~~toString\n> x\n> ~~~")).toBe("(code)");
  });

  test("caps the length", () => {
    expect(plainText("word ".repeat(500), 20)).toBe("word word word word…");
  });
});
