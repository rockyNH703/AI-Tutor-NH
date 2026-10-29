"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/errors";
import {
  getCatalog,
  getEngineeringCatalog,
  getEngineeringProfile,
  getLearnerProfile,
  updateEngineeringProfile,
  updateLearnerProfile,
} from "@/lib/api/learning";
import type {
  AcademicCatalog,
  CatalogEntry,
  EngineeringCatalog,
  EngineeringProfile,
  EngineeringProfileField,
  EngineeringProfileValues,
  LearnerProfile,
  LearnerProfileField,
  LearnerProfileValues,
} from "@/lib/api/types";

/**
 * A catalog is the same for everyone and versioned by the API (ETag): it is fetched once per page
 * load and shared by every section. A failed fetch is forgotten, so a later attempt tries again.
 */
function useSharedCatalog<T>(slot: { request: Promise<T> | null }, load: () => Promise<T>) {
  const [catalog, setCatalog] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);

  // One fetch per attempt.
  useEffect(() => {
    let cancelled = false;
    setError(null);
    slot.request ??= load().catch((failure) => {
      slot.request = null; // a later attempt tries again
      throw failure;
    });
    slot.request.then(
      (loaded) => !cancelled && setCatalog(loaded),
      (failure) => !cancelled && setError(failure),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  return { catalog, error, retry: () => setAttempt((count) => count + 1) };
}

const academicCatalog: { request: Promise<AcademicCatalog> | null } = { request: null };
const engineeringCatalog: { request: Promise<EngineeringCatalog> | null } = { request: null };

/** The academic catalog, fetched once per page load and shared by every section. */
export function useCatalog() {
  return useSharedCatalog(academicCatalog, getCatalog);
}

/** The engineering catalog (roles, experience, focus areas, platforms, certifications), shared the same way. */
export function useEngineeringCatalog() {
  return useSharedCatalog(engineeringCatalog, getEngineeringCatalog);
}

/**
 * The options for a catalog field: every offered entry, plus a deprecated one only while it is
 * the current value (the API accepts a deprecated code only when it is kept, not chosen anew).
 */
export function catalogOptions(
  entries: readonly CatalogEntry[],
  current: string | readonly string[] | null | undefined,
): CatalogEntry[] {
  const kept: readonly (string | null | undefined)[] = typeof current === "string" || current == null ? [current] : current;
  return entries.filter((entry) => !entry.deprecated || kept.includes(entry.code));
}

export class ProfileConflict extends Error {
  constructor() {
    super("The profile was changed somewhere else.");
  }
}

/**
 * A versioned settings resource (GET + PATCH `{version, set, reset}`). `save` sends a versioned
 * edit; on a 409 it adopts the resource as it is now and throws ProfileConflict, so the form keeps
 * the user's edit and asks them to check it and save again.
 */
function useVersionedProfile<P extends { version: number }, V, F extends string>(
  load: () => Promise<P>,
  update: (edit: { version: number; set: Partial<V>; reset: F[] }) => Promise<P>,
) {
  const [profile, setProfile] = useState<P | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);

  // One fetch per attempt.
  useEffect(() => {
    let cancelled = false;
    setError(null);
    load().then(
      (loaded) => !cancelled && setProfile(loaded),
      (failure) => !cancelled && setError(failure),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const save = useCallback(
    async (set: Partial<V>, reset: F[]): Promise<P> => {
      if (!profile) throw new Error("The profile is not loaded yet.");
      try {
        const next = await update({ version: profile.version, set, reset });
        setProfile(next);
        return next;
      } catch (failure) {
        if (failure instanceof ApiError && failure.code === "version_conflict" && failure.current) {
          setProfile(failure.current as P);
          throw new ProfileConflict();
        }
        throw failure;
      }
    },
    [profile],
  );

  return { profile, error, save, retry: () => setAttempt((count) => count + 1) };
}

/** The learner profile (GET/PATCH /v1/learning/profile). */
export function useLearnerProfile() {
  return useVersionedProfile<LearnerProfile, LearnerProfileValues, LearnerProfileField>(
    getLearnerProfile,
    updateLearnerProfile,
  );
}

/** The Engineering Profile (GET/PATCH /v1/engineering/profile). */
export function useEngineeringProfile() {
  return useVersionedProfile<EngineeringProfile, EngineeringProfileValues, EngineeringProfileField>(
    getEngineeringProfile,
    updateEngineeringProfile,
  );
}
