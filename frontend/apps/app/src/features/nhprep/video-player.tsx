"use client";

import { Badge } from "@v1/ui/badge";
import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { Play, RotateCw } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { formatTime } from "@/features/markdown/widgets/video";
import { describeError } from "@/lib/api/errors";
import { startNhprepPlayback } from "@/lib/api/nhprep";
import type { NhprepSection, NhprepVideoState, Uuid } from "@/lib/api/types";

/** The only origin a video is played from (next.config.mjs media-src). */
export const VIDEO_ORIGIN = "https://videos.nhprep.com";
const VIDEO_PATH = /^\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.mp4$/i;
/** Sections shown before "Show all". */
export const FIRST_SECTIONS = 8;

export const COPY = {
  unavailable: "Video isn't available in NetSec AI yet.",
  noVideo: "This lesson has no video.",
  stopped: "The video stopped or couldn't load. Reload it to try again.",
  starting: "Starting…",
} as const;

/**
 * The link the server signed, checked before the player gets it: exactly https://videos.nhprep.com, no user info,
 * no port, and a path of `/{video id}.mp4` (its query, the signature, is the server's). Anything else is refused.
 */
export function allowedSrc(value: unknown): string | null {
  if (typeof value !== "string") return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.origin !== VIDEO_ORIGIN || url.username || url.password || !VIDEO_PATH.test(url.pathname)) return null;
  return url.href;
}

type Phase =
  | { name: "cover" }
  | { name: "starting" }
  | { name: "playing"; src: string; load: number }
  | { name: "unavailable"; text: string }
  | { name: "failed" } // the player failed twice: the honest line and Reload video
  | { name: "error"; text: string }; // the playback request itself failed

/**
 * A lesson's video and its "Jump to" list. Native <video> (its own controls, keyboard and fullscreen), nothing
 * loaded before Play: the click asks the API for a short-lived link, checks it, and only then sets it. Jump to
 * seeks the same player. A player error (an expired link answers 410) gets one fresh link, resumed where it was;
 * a second error shows the line and Reload video. When the lesson's video can't play here, the line shows from
 * the start and the sections are a plain list.
 */
export function VideoSection({
  lessonId,
  title,
  video,
  sections,
  matchedId,
}: {
  lessonId: Uuid;
  title: string;
  video: NhprepVideoState;
  sections: NhprepSection[];
  matchedId: Uuid | null;
}) {
  const [phase, setPhase] = useState<Phase>({ name: "cover" });
  const [all, setAll] = useState(false);
  const player = useRef<HTMLVideoElement>(null);
  const action = useRef<HTMLButtonElement>(null); // Play, Try again or Reload video: whichever is shown
  const moveFocus = useRef(false);
  const seekTo = useRef<number | undefined>(undefined); // applied once the new link's metadata is in
  const retried = useRef(false);
  const loads = useRef(0);
  const titleId = useId();
  const lineId = useId();
  const jumpId = useId();

  useEffect(() => {
    if (!moveFocus.current || phase.name === "starting") return;
    moveFocus.current = false;
    const target = phase.name === "playing" ? player.current : action.current;
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: "nearest" });
  }, [phase]);

  const playable = video === "ready";
  const timed = sections.filter((section): section is NhprepSection & { startSeconds: number } => section.startSeconds !== null);
  const matched = timed.find((section) => section.id === matchedId);

  const start = async (at: number | undefined, focus: boolean) => {
    moveFocus.current = focus;
    seekTo.current = at;
    setPhase({ name: "starting" });
    try {
      const answer = await startNhprepPlayback(lessonId);
      if (answer.status !== "ready") {
        setPhase({ name: "unavailable", text: answer.reason === "no_video" ? COPY.noVideo : COPY.unavailable });
        return;
      }
      const src = allowedSrc(answer.src);
      loads.current += 1;
      setPhase(src ? { name: "playing", src, load: loads.current } : { name: "unavailable", text: COPY.unavailable });
    } catch (error) {
      setPhase({ name: "error", text: describeError(error).message });
    }
  };

  const seek = (at: number) => {
    const element = player.current;
    if (phase.name === "playing" && element) {
      element.currentTime = at;
      void element.play().catch(() => {}); // a refused play() leaves the native controls to start it
      return;
    }
    if (phase.name !== "starting") void start(at, false);
  };

  const failed = () => {
    if (phase.name !== "playing") return;
    if (retried.current) {
      moveFocus.current = document.activeElement === player.current;
      setPhase({ name: "failed" });
      return;
    }
    retried.current = true;
    const at = player.current?.currentTime || seekTo.current;
    void start(at, document.activeElement === player.current);
  };

  const box = "relative aspect-video w-full rounded-lg border border-border bg-surface-2";
  const line = (text: string) => (
    <div className={cn(box, "flex flex-col items-center justify-center gap-2 px-4 py-6 text-center")}>
      <p id={lineId} role="status" className="max-w-sm text-sm">
        {text}
      </p>
    </div>
  );

  let shown: ReactNode;
  if (!playable) shown = line(video === "no_video" ? COPY.noVideo : COPY.unavailable);
  else if (phase.name === "playing") {
    shown = (
      <div className={cn(box, "overflow-hidden bg-black")}>
        {/* biome-ignore lint/a11y/useMediaCaption: NHprep publishes no caption tracks yet (a known, documented gap) */}
        <video
          key={phase.load}
          ref={player}
          src={phase.src}
          controls
          autoPlay
          preload="none"
          playsInline
          controlsList="nodownload"
          aria-label={`Video: ${title}`}
          onLoadedMetadata={(event) => {
            const at = seekTo.current;
            seekTo.current = undefined;
            if (at) event.currentTarget.currentTime = at;
          }}
          onPlaying={() => {
            retried.current = false; // a link that plays again earns its own retry later
          }}
          onError={failed}
          className="focus-ring absolute inset-0 h-full w-full"
        />
      </div>
    );
  } else if (phase.name === "unavailable") shown = line(phase.text);
  else {
    const retrying = phase.name === "failed" || phase.name === "error";
    shown = (
      // No overflow clip: at phone widths the cover grows past 16:9 to fit its text.
      <div className={cn(box, "flex flex-col items-center justify-center gap-2 px-4 py-6 text-center")}>
        {retrying ? (
          <p id={lineId} role="status" className="max-w-sm text-sm">
            {phase.name === "failed" ? COPY.stopped : phase.text}
          </p>
        ) : null}
        <Button
          ref={action}
          type="button"
          variant={retrying ? "outline" : "default"}
          aria-describedby={retrying ? lineId : titleId}
          aria-disabled={phase.name === "starting" || undefined}
          onClick={() => {
            if (phase.name === "starting") return;
            if (retrying) retried.current = false;
            void start(retrying ? undefined : matched?.startSeconds, true);
          }}
        >
          {retrying ? <RotateCw aria-hidden className="h-4 w-4" /> : <Play aria-hidden className="h-4 w-4" />}
          {phase.name === "starting"
            ? COPY.starting
            : phase.name === "failed"
              ? "Reload video"
              : phase.name === "error"
                ? "Try again"
                : matched
                  ? `Play from ${formatTime(matched.startSeconds)}`
                  : "Play"}
        </Button>
        {phase.name === "cover" ? (
          <p className="max-w-sm text-xs text-text-secondary">Loads the video when you press Play</p>
        ) : null}
        <span id={titleId} className="sr-only">
          {title}
        </span>
      </div>
    );
  }

  const seekable = playable && phase.name !== "unavailable";
  const listed = all ? sections : sections.slice(0, FIRST_SECTIONS);
  return (
    <div className="mt-3">
      {shown}
      {sections.length ? (
        <div className="mt-3">
          <p id={jumpId} className="mb-1 text-xs font-semibold text-text-secondary">
            {seekable ? "Jump to" : "In this video"}
          </p>
          <ul aria-labelledby={jumpId} className="flex flex-col gap-0.5">
            {listed.map((section) => {
              const time = section.startSeconds === null ? null : formatTime(section.startSeconds);
              const here = section.id === matchedId;
              const content = (
                <>
                  {time ? <span className="shrink-0 tabular-nums text-text-secondary">{time}</span> : null}
                  <span className="min-w-0 break-words">{section.title}</span>
                  {here ? <Badge variant="brand">Matches your question</Badge> : null}
                </>
              );
              return (
                <li key={section.id}>
                  {seekable && section.startSeconds !== null ? (
                    <Button
                      type="button"
                      variant="ghost"
                      aria-current={here || undefined}
                      className="h-auto min-h-11 w-full justify-start whitespace-normal px-2 py-1.5 text-left font-normal sm:min-h-10"
                      onClick={() => seek(section.startSeconds as number)}
                    >
                      {content}
                    </Button>
                  ) : (
                    <p className="flex min-h-11 items-center gap-2 px-2 py-1.5 text-sm sm:min-h-10">{content}</p>
                  )}
                </li>
              );
            })}
          </ul>
          {sections.length > FIRST_SECTIONS ? (
            <Button type="button" variant="ghost" size="sm" className="mt-1 min-h-11 sm:min-h-10" aria-expanded={all} onClick={() => setAll(!all)}>
              {all ? "Show fewer" : `Show all ${sections.length}`}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
