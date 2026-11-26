"use client";

import { cn } from "@v1/ui/utils";
import { type KeyboardEvent, type PointerEvent, type RefObject, useEffect, useRef, useState } from "react";

import { SIDEBAR_WIDTH, SIDEBAR_WIDTH_STEP, clampSidebarWidth } from "./sidebar-width";

/**
 * The desktop sidebar's right edge, to drag (or move with the arrow keys) between 200 and 420px. While dragging, the
 * width is written straight to the rail's `--sidebar-width` once per frame, so nothing re-renders under the pointer;
 * releasing commits it (`onCommit`: state and cookie). A WAI-ARIA window splitter: a focusable separator with its
 * value, arrows step it (Shift: bigger steps), Home and End go to the limits, Enter or a double-click resets it.
 */
export function SidebarResizer({
  target,
  controls,
  width,
  onCommit,
}: {
  /** The element that carries `--sidebar-width` (the desktop aside). */
  target: RefObject<HTMLElement | null>;
  /** The id of what it resizes. */
  controls: string;
  width: number;
  onCommit: (width: number) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const live = useRef(width);
  const left = useRef(0);
  const frame = useRef(0);

  const show = (next: number) => {
    live.current = next;
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => target.current?.style.setProperty("--sidebar-width", `${next}px`));
  };

  // While dragging: the resize cursor everywhere and no text selected under the pointer.
  useEffect(() => {
    if (!dragging) return;
    const { cursor, userSelect } = document.body.style;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      document.body.style.cursor = cursor;
      document.body.style.userSelect = userSelect;
    };
  }, [dragging]);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !target.current) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    left.current = target.current.getBoundingClientRect().left;
    live.current = width;
    setDragging(true);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (dragging) show(clampSidebarWidth(event.clientX - left.current));
  };
  const finish = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    cancelAnimationFrame(frame.current);
    setDragging(false);
    onCommit(live.current);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? SIDEBAR_WIDTH_STEP.large : SIDEBAR_WIDTH_STEP.small;
    const next: Record<string, number> = {
      ArrowLeft: width - step,
      ArrowRight: width + step,
      Home: SIDEBAR_WIDTH.min,
      End: SIDEBAR_WIDTH.max,
      Enter: SIDEBAR_WIDTH.default,
    };
    const value = next[event.key];
    if (value === undefined) return;
    event.preventDefault();
    onCommit(clampSidebarWidth(value));
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      aria-controls={controls}
      aria-valuemin={SIDEBAR_WIDTH.min}
      aria-valuemax={SIDEBAR_WIDTH.max}
      aria-valuenow={width}
      aria-valuetext={`${width} pixels wide`}
      title="Drag to resize. Double-click to reset."
      tabIndex={0}
      data-dragging={dragging ? "" : undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      onLostPointerCapture={finish}
      onDoubleClick={() => onCommit(SIDEBAR_WIDTH.default)}
      onKeyDown={onKeyDown}
      // A 12px grab area across the rail's edge; the line in it shows on hover, focus and while dragging, and keyboard
      // focus also rings the whole strip.
      className="group/resizer focus-ring-inset absolute inset-y-0 -right-1.5 z-20 w-3 cursor-col-resize touch-none rounded-sm"
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-brand opacity-0 transition-opacity duration-fast",
          "group-hover/resizer:opacity-60 group-focus-visible/resizer:w-[3px] group-focus-visible/resizer:opacity-100",
          dragging && "opacity-100",
        )}
      />
    </div>
  );
}
