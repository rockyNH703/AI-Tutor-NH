"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { type Countdown, countdownLeft } from "@/features/friendzone/exam-model";
import { secondsLeft } from "@/features/friendzone/clock";

/** This device's clock, read once a second (the countdown counts on the server's clock through `offset`). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** Seconds left to `endsAt` on the server's clock; never goes up within one exam, however the offset moves. */
export function useSecondsLeft(endsAt: string, offset: number, now: number): number {
  const last = useRef<Countdown | null>(null);
  const left = countdownLeft(last.current, endsAt, secondsLeft(endsAt, offset, now));
  last.current = { endsAt, left };
  return left;
}

function subscribeOnline(listener: () => void) {
  window.addEventListener("online", listener);
  window.addEventListener("offline", listener);
  return () => {
    window.removeEventListener("online", listener);
    window.removeEventListener("offline", listener);
  };
}

/** Whether the browser says it is online (true on the server render). */
export function useOnline(): boolean {
  return useSyncExternalStore(
    subscribeOnline,
    () => navigator.onLine,
    () => true,
  );
}

/** Resolves when the browser is back online (at once if it is). */
export function onceOnline(): Promise<void> {
  if (typeof navigator === "undefined" || navigator.onLine) return Promise.resolve();
  return new Promise((resolve) => window.addEventListener("online", () => resolve(), { once: true }));
}

export const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
