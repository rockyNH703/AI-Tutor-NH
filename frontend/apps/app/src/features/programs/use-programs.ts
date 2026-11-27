"use client";

import { useEffect, useState } from "react";

import { type ProgramList, listPrograms } from "@/lib/api/programs";

/**
 * The account's NH programs and claims (GET /v1/programs), read once per page load and shared by My Programs and
 * every program page. `refreshPrograms()` reads them again (after a claim). While loading or on an error nothing
 * program-related is shown: the API is the boundary, the pages only follow it.
 */
const EVENT = "netsec:programs";
let shared: Promise<ProgramList> | null = null;

function load(): Promise<ProgramList> {
  if (!shared) {
    shared = listPrograms();
    shared.catch(() => {
      shared = null; // a failed read is tried again by the next reader
    });
  }
  return shared;
}

export function refreshPrograms(): void {
  shared = null;
  window.dispatchEvent(new Event(EVENT));
}

export type ProgramsState = { status: "loading" | "ready" | "error"; data: ProgramList | null };

export function usePrograms(): ProgramsState {
  const [state, setState] = useState<ProgramsState>({ status: "loading", data: null });
  useEffect(() => {
    let live = true;
    const read = () =>
      load().then(
        (data) => live && setState({ status: "ready", data }),
        () => live && setState((previous) => ({ status: previous.data ? "ready" : "error", data: previous.data })),
      );
    void read();
    window.addEventListener(EVENT, read);
    return () => {
      live = false;
      window.removeEventListener(EVENT, read);
    };
  }, []);
  return state;
}
