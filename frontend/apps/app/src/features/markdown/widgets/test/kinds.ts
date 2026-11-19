"use client";

import { useEffect, useState } from "react";

import type { Assessment, QuestionKind } from "@/lib/api/types";
import type { KindModule } from "./question-props";
import blanks from "./questions/blanks";
import choice from "./questions/choice";
import cli from "./questions/cli";
import listing from "./questions/listing";
import numeric from "./questions/numeric";
import written from "./questions/written";

/** The kinds one questions/* file provides (choice: mcq + multi; written: short + long; ...). */
// biome-ignore lint/suspicious/noExplicitAny: one registry for kinds whose values have different types
export type KindPack = Partial<Record<QuestionKind, KindModule<any>>>;

// Light kinds ship with the card. Heavy ones (React Flow, dnd-kit) are their own chunks, loaded only for a
// test that has them, so a plain quiz or fill-in-the-blanks never downloads those libraries.
const LOADED: KindPack = { ...choice, ...blanks, ...numeric, ...written, ...cli, ...listing };
const HEAVY: Partial<Record<QuestionKind, () => Promise<{ default: KindPack }>>> = {
  match: () => import("./questions/match"),
  order: () => import("./questions/order"),
  diagram: () => import("./questions/diagram"),
};
const pending = new Map<QuestionKind, Promise<void>>();

/** The module of a kind already loaded (always, for light kinds), or undefined. */
export function kindModule(kind: QuestionKind) {
  return LOADED[kind];
}

function load(kind: QuestionKind): Promise<void> {
  if (LOADED[kind]) return Promise.resolve();
  const loader = HEAVY[kind];
  if (!loader) return Promise.reject(new Error(`unknown question kind: ${kind}`));
  let found = pending.get(kind);
  if (!found) {
    found = loader().then(
      (module) => {
        Object.assign(LOADED, module.default);
      },
      (error) => {
        pending.delete(kind); // a later card may try again (React never caches this promise)
        throw error;
      },
    );
    pending.set(kind, found);
  }
  return found;
}

/** Load these kinds (each module once); rejects on an unknown kind or a chunk that wouldn't load. */
export async function loadKinds(kinds: readonly QuestionKind[]): Promise<void> {
  await Promise.all(kinds.map(load));
}

/**
 * Every kind a test uses, loaded before the card draws: "loading" (show Preparing), "ready" (read each
 * kind's module synchronously with kindModule), or "failed" (an unknown kind or a chunk that wouldn't
 * load: the card gives up with its source, exam-safe). A record's kinds never change once it is saved.
 */
export function useKinds(record: Assessment | undefined): "loading" | "ready" | "failed" {
  const kinds = record ? [...new Set(record.questions.map((question) => question.kind))] : [];
  const key = kinds.join(",");
  const ready = kinds.every((kind) => LOADED[kind]);
  const [state, setState] = useState<"loading" | "ready" | "failed">(ready ? "ready" : "loading");
  useEffect(() => {
    if (!key) return;
    let alive = true;
    const wanted = key.split(",") as QuestionKind[];
    if (wanted.every((kind) => LOADED[kind])) {
      setState("ready");
      return;
    }
    setState("loading");
    loadKinds(wanted).then(
      () => alive && setState("ready"),
      () => alive && setState("failed"),
    );
    return () => {
      alive = false;
    };
  }, [key]);
  return ready ? "ready" : state;
}
