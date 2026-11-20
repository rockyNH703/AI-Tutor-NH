import { describe, expect, test } from "bun:test";

import { quizMeAction } from "./quiz-me";

describe("quizMeAction (the composer's Quiz me button)", () => {
  test("in a chat with messages and an empty box, it asks about this chat at once", () => {
    expect(quizMeAction({ draft: "", hasMessages: true, hasFiles: false })).toEqual({
      kind: "send",
      text: "Quiz me on this",
    });
  });

  test("in a new chat it fills the box for the student to name a topic", () => {
    expect(quizMeAction({ draft: "", hasMessages: false, hasFiles: false })).toEqual({
      kind: "fill",
      text: "Quiz me on ",
    });
  });

  test("what the student typed becomes the topic, and nothing is sent until they press Send", () => {
    expect(quizMeAction({ draft: "  ospf areas ", hasMessages: true, hasFiles: false })).toEqual({
      kind: "fill",
      text: "Quiz me on ospf areas",
    });
  });

  test("a box that already asks for a quiz is left as it is", () => {
    expect(quizMeAction({ draft: "Quiz me on vlans", hasMessages: true, hasFiles: false })).toEqual({
      kind: "fill",
      text: "Quiz me on vlans",
    });
  });

  test("with files attached it never sends: the bank answers no message that carries files", () => {
    expect(quizMeAction({ draft: "", hasMessages: true, hasFiles: true })).toEqual({
      kind: "fill",
      text: "Quiz me on ",
    });
  });
});
