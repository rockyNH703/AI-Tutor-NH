/**
 * A reply the provider stopped before it was finished (the API's `finishReason`). It is kept and
 * shown, with this note, so a cut configuration or runbook is never read as complete.
 */
export const CONTINUE_PROMPT = "Continue exactly where your last reply stopped.";

export function cutOffNote(finishReason: string | undefined): string | null {
  if (finishReason === "length") return "This reply was cut off at the length limit.";
  if (finishReason === "content_filter") return "The provider's content filter stopped this reply.";
  return null;
}
