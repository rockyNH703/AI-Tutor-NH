"use client";

import type { ChatPlace } from "@/lib/api/types";
import { placeText } from "./program-nav";
import { usePrograms } from "./use-programs";

/**
 * Under a new program chat's greeting: the one line on what this tutor answers from. Nothing else (owner,
 * 2026-11-26: the greeting only, no suggested questions); the student starts with their own words.
 */
export function ProgramWelcome({ place }: { place: ChatPlace }) {
  const { data } = usePrograms();
  const text = placeText(place, data?.programs.find((item) => item.slug === place.program) ?? null);
  return <p className="mt-3 max-w-prose text-balance text-center text-sm text-text-secondary sm:text-base">{text.line}</p>;
}
