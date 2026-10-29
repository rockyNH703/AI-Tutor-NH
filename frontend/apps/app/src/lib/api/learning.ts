/** The learning side of Settings: the engineering and academic catalogs, the Engineering Profile,
 * the learner profile, subjects, answer preferences, and the preview of what the assistant is told. */

import { callRoute } from "./http";
import type {
  AcademicCatalog,
  EngineeringCatalog,
  EngineeringProfile,
  LearnerProfile,
  LearningPreferenceField,
  LearningPreferenceValues,
  LearningPreferences,
  SubjectItem,
  SubjectList,
  TutorPreview,
  TutorPreviewRequest,
  UpdateEngineeringProfileRequest,
  UpdateLearnerProfileRequest,
  VersionedEdit,
} from "./types";

/** Public and the same for everyone; codes are permanent, `deprecated` ones may only be kept. */
export function getCatalog(): Promise<AcademicCatalog> {
  return callRoute("GET /v1/catalog/academic");
}

/** The same for everyone, behind a session; codes are permanent, `deprecated` ones may only be kept. */
export function getEngineeringCatalog(): Promise<EngineeringCatalog> {
  return callRoute("GET /v1/catalog/engineering");
}

/** What the assistant is told about the user's work. Version 0 and empty values until first saved. */
export function getEngineeringProfile(): Promise<EngineeringProfile> {
  return callRoute("GET /v1/engineering/profile");
}

/** A versioned edit; a stale version is 409 `version_conflict` with the profile as it is now. */
export function updateEngineeringProfile(edit: UpdateEngineeringProfileRequest): Promise<EngineeringProfile> {
  return callRoute("PATCH /v1/engineering/profile", {
    body: { version: edit.version, set: edit.set ?? {}, reset: edit.reset ?? [] },
  });
}

export function getLearnerProfile(): Promise<LearnerProfile> {
  return callRoute("GET /v1/learning/profile");
}

export function updateLearnerProfile(edit: UpdateLearnerProfileRequest): Promise<LearnerProfile> {
  return callRoute("PATCH /v1/learning/profile", {
    body: { version: edit.version, set: edit.set ?? {}, reset: edit.reset ?? [] },
  });
}

export function getSubjects(): Promise<SubjectList> {
  return callRoute("GET /v1/learning/subjects");
}

/** Replaces the whole list; its order is the order shown. */
export function replaceSubjects(version: number, items: SubjectItem[]): Promise<SubjectList> {
  return callRoute("PUT /v1/learning/subjects", {
    body: {
      version,
      items: items.map((item) => ({
        code: item.code,
        isPriority: item.isPriority,
        needsImprovement: item.needsImprovement,
      })),
    },
  });
}

export function getLearningPreferences(): Promise<LearningPreferences> {
  return callRoute("GET /v1/learning/preferences");
}

export function updateLearningPreferences(
  edit: VersionedEdit<LearningPreferenceValues, LearningPreferenceField>,
): Promise<LearningPreferences> {
  return callRoute("PATCH /v1/learning/preferences", {
    body: { version: edit.version, set: edit.set ?? {}, reset: edit.reset ?? [] },
  });
}

/** What the assistant would be told with these (unsaved) edits over what is stored. Saves nothing. */
export function previewTutor(draft: TutorPreviewRequest = {}, signal?: AbortSignal): Promise<TutorPreview> {
  return callRoute("POST /v1/learning/tutor-preview", { body: draft, signal });
}
