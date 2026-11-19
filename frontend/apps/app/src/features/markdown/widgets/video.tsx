"use client";

import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { ExternalLink, MonitorPlay, Play, WifiOff } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { z } from "zod";

import type { WidgetProps } from "../rich-block";
import { CardFrame } from "../study-card";
import { parseJson } from "./tokens";

const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const HOSTS = new Set(["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com"]);
/** The only origin framed (next.config.mjs frame-src): YouTube's privacy-enhanced mode. */
const EMBED = "https://www.youtube-nocookie.com/embed/";
const MAX_SECONDS = 86_400; // a day: longer than any video worth jumping into
const Seconds = z.number().int().min(0).max(MAX_SECONDS);

/** The 11-character id in a bare id or a YouTube link (watch, embed, shorts, live, youtu.be, youtube-nocookie), or null. */
export function youtubeId(value: string): string | null {
  if (VIDEO_ID.test(value)) return value;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if ((url.protocol !== "https:" && url.protocol !== "http:") || !HOSTS.has(url.hostname)) return null;
  const [first = "", second] = url.pathname.split("/").filter(Boolean);
  const id =
    url.hostname === "youtu.be"
      ? first
      : first === "watch"
        ? url.searchParams.get("v")
        : ["embed", "shorts", "live"].includes(first)
          ? second
          : null;
  return id && VIDEO_ID.test(id) ? id : null;
}

/** ```video, as the tutor writes it. */
export const VideoBlock = z.object({
  youtube: z.string().trim().max(300).transform(youtubeId).pipe(z.string()),
  title: z.string().trim().min(1).max(200),
  channel: z.string().trim().min(1).max(100).optional(),
  start: Seconds.optional(),
  chapters: z
    .array(z.object({ at: Seconds, title: z.string().trim().min(1).max(120) }))
    .max(50)
    .optional(),
  captions: z
    .string()
    .regex(/^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$/)
    .optional(),
});
export type VideoSpec = z.infer<typeof VideoBlock>;

/** The player's address, built by URL and searchParams only: nothing the author wrote is pasted in raw. */
export function embedUrl(video: Pick<VideoSpec, "youtube" | "captions">, start?: number): string {
  const url = new URL(video.youtube, EMBED);
  url.searchParams.set("autoplay", "1");
  url.searchParams.set("playsinline", "1");
  url.searchParams.set("rel", "0");
  if (start) url.searchParams.set("start", String(start));
  if (video.captions) {
    url.searchParams.set("cc_load_policy", "1");
    url.searchParams.set("cc_lang_pref", video.captions);
  }
  return url.href;
}

export function watchUrl(id: string, start?: number): string {
  const url = new URL("https://www.youtube.com/watch");
  url.searchParams.set("v", id);
  if (start) url.searchParams.set("t", `${start}s`);
  return url.href;
}

const SANDBOX = ["allow-scripts", "allow-same-origin", "allow-presentation", "allow-popups", "allow-popups-to-escape-sandbox"];

/**
 * The frame's sandbox: these tokens, less any the browser doesn't know (WebKit has no allow-presentation and logs
 * an error for it). Leaving a token out only ever takes a permission away.
 */
export function sandboxFor(supports: (token: string) => boolean): string {
  return SANDBOX.filter((token) => supports(token)).join(" ");
}

/** The browser's own answer; a browser that can't say keeps the token (it would be ignored there anyway). */
function browserSupports(token: string): boolean {
  try {
    return document.createElement("iframe").sandbox.supports(token);
  } catch {
    return true;
  }
}

/** 135 → "2:15", 3723 → "1:02:03". */
export function formatTime(seconds: number): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours ? `${hours}:${pad(minutes)}:${pad(seconds % 60)}` : `${minutes}:${pad(seconds % 60)}`;
}

type Phase = "cover" | "offline" | "playing";

/**
 * ```video: a YouTube video, loaded only when Play is pressed (nothing is fetched before: no thumbnail), in
 * privacy-enhanced mode, sandboxed. "Open on YouTube" is always there for a video that will not embed.
 */
export default function Video({ source, onError }: WidgetProps) {
  const video = useMemo(() => {
    const parsed = VideoBlock.safeParse(parseJson(source));
    return parsed.success ? parsed.data : null;
  }, [source]);
  const [phase, setPhase] = useState<Phase>("cover");
  const [start, setStart] = useState<number | undefined>(undefined);
  const [loads, setLoads] = useState(0); // a new player each time, even at the same start
  const [loaded, setLoaded] = useState(false);
  // Set by the control that is about to disappear (Play, Retry); focus then goes to what replaced it.
  const moveFocus = useRef(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const play = useRef<HTMLButtonElement>(null);
  const retry = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const offlineId = useId();
  const jumpId = useId();

  useEffect(() => {
    if (!video) onError();
  }, [video, onError]);
  useEffect(() => {
    if (!moveFocus.current) return;
    moveFocus.current = false;
    const target = phase === "playing" ? frame.current : phase === "offline" ? retry.current : play.current;
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: "nearest" });
  }, [phase]);

  if (!video) return null;

  const begin = (at: number | undefined, focus: boolean) => {
    moveFocus.current = focus;
    if (!navigator.onLine) {
      setPhase("offline");
      return;
    }
    setStart(at);
    setLoads((count) => count + 1);
    setLoaded(false);
    setPhase("playing");
  };
  const box = "relative aspect-video w-full rounded-lg border border-border bg-surface-2";

  return (
    <CardFrame
      kind="video"
      icon={MonitorPlay}
      label="Video"
      title={<span id={titleId}>{video.title}</span>}
      actions={
        <a
          href={watchUrl(video.youtube, start ?? video.start)}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="focus-ring inline-flex min-h-11 items-center gap-1 rounded-md px-1.5 text-xs text-text-secondary underline-offset-4 hover:text-foreground hover:underline sm:min-h-10"
        >
          Open on YouTube
          <ExternalLink aria-hidden className="h-3.5 w-3.5" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      }
    >
      {phase === "playing" ? (
        <div className={cn(box, "overflow-hidden")}>
          <iframe
            key={loads}
            ref={frame}
            src={embedUrl(video, start)}
            title={video.title}
            allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
            sandbox={sandboxFor(browserSupports)}
            onLoad={() => setLoaded(true)}
            className="absolute inset-0 h-full w-full border-0"
          />
          {loaded ? null : (
            <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-text-secondary">
              Loading the video…
            </p>
          )}
        </div>
      ) : (
        // No overflow clip: at phone widths the cover grows past 16:9 to fit its text.
        <div className={cn(box, "flex flex-col items-center justify-center gap-2 px-4 py-6 text-center")}>
          {phase === "offline" ? (
            <>
              <WifiOff aria-hidden className="h-5 w-5 text-text-secondary" />
              <p id={offlineId} role="status" className="text-sm">
                You're offline. The video needs an internet connection.
              </p>
              <Button
                ref={retry}
                type="button"
                variant="outline"
                aria-describedby={offlineId}
                onClick={() => {
                  moveFocus.current = true;
                  setPhase("cover");
                }}
              >
                Retry
              </Button>
            </>
          ) : (
            <>
              <Button ref={play} type="button" aria-describedby={titleId} onClick={() => begin(video.start, true)}>
                <Play aria-hidden className="h-4 w-4" />
                Play
              </Button>
              {video.channel ? <p className="text-sm text-foreground">{video.channel}</p> : null}
              <p className="max-w-sm text-xs text-text-secondary">Loads YouTube in privacy-enhanced mode when you press Play</p>
            </>
          )}
        </div>
      )}
      {video.chapters?.length ? (
        <div className="mt-3">
          <p id={jumpId} className="mb-1 text-xs font-semibold text-text-secondary">
            Jump to
          </p>
          <ul aria-labelledby={jumpId} className="flex flex-col gap-0.5">
            {video.chapters.map((chapter, index) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: entries are fixed positions in the author's list
              <li key={index}>
                <Button
                  type="button"
                  variant="ghost"
                  className="h-auto min-h-11 w-full justify-start whitespace-normal px-2 py-1.5 text-left font-normal sm:min-h-10"
                  onClick={() => begin(chapter.at, false)}
                >
                  <span className="shrink-0 tabular-nums text-text-secondary">{formatTime(chapter.at)}</span>
                  <span className="min-w-0 break-words">{chapter.title}</span>
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </CardFrame>
  );
}
