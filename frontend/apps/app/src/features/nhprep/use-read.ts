"use client";

import { useEffect, useState } from "react";

import { peekRead } from "@/lib/api/nhprep";

export type Read<T> = { data: T | null; error: unknown; retry: () => void };

/**
 * One cached NHprep read (lib/api/nhprep.ts), keyed by its cache key: data already in this tab draws at once, a
 * new key reads again, `null` reads nothing. Effects depend on the key string, never on a card object (a chat
 * reload replaces those).
 */
export function useRead<T>(key: string | null, load: () => Promise<T>): Read<T> {
  const [state, setState] = useState<{ key: string | null; data: T | null; error: unknown }>({ key: null, data: null, error: null });
  const [attempt, setAttempt] = useState(0);

  // `load` is a new closure each render: the key names what it reads.
  useEffect(() => {
    if (key === null) return;
    let live = true;
    load().then(
      (data) => live && setState({ key, data, error: null }),
      (error: unknown) => live && setState({ key, data: null, error }),
    );
    return () => {
      live = false;
    };
  }, [key, attempt]);

  const current = state.key === key ? state : { data: key === null ? null : (peekRead<T>(key) ?? null), error: null };
  return {
    data: current.data,
    error: current.error,
    retry: () => {
      setState({ key: null, data: null, error: null });
      setAttempt((count) => count + 1);
    },
  };
}
