"use client";

import { useEffect } from "react";

import type { WidgetProps } from "../rich-block";
import { Preparing, useRecord } from "./test/record";
import { TestCard } from "./test/test-card";

/**
 * ```mcq and ```quiz: a test the server keeps. The card (test/test-card.tsx) draws only what the server
 * recorded for this reply: the questions, the answers given (final, graded there), the waiting question,
 * a mock exam's drafts and the score. Nothing is scored or remembered here; a reload shows the same.
 */
function TestBlock({ source, onError, single }: WidgetProps & { single: boolean }) {
  const { tests, record, waiting } = useRecord(source, onError, "test");
  if (waiting) return <Preparing noun="test" />;
  if (!record || !tests) return null;
  return <TestCard test={record} single={single} tests={tests} onError={onError} />;
}

/** ```mcq: one multiple-choice question. */
export function McqBlock(props: WidgetProps) {
  return <TestBlock {...props} single />;
}

/** ```quiz: several questions, one at a time (or a mock exam in any order), then the score. */
export function QuizBlock(props: WidgetProps) {
  return <TestBlock {...props} single={false} />;
}

/**
 * ```question-bank: the tutor's request for a test from the question bank. The server replaces it
 * with the test before the reply is saved (app/qbank/chat_tests.py), so a saved reply still holding
 * one stopped before the test was set: say so, with the request's source.
 */
export function BankBlock({ onError }: WidgetProps) {
  useEffect(() => onError("The test wasn't set because the reply stopped. Ask for it again."), [onError]);
  return null;
}
