"use client";

import { cn } from "@v1/ui/utils";
import { useSearchParams } from "next/navigation";
import { type ReactNode, type Ref, useCallback, useEffect, useRef, useState } from "react";

import { navigationGuard } from "@/features/navigation/navigation-guard";
import { type FriendZoneMe, getFriendZoneMe } from "./api";
import { keepOnFailure, sequence } from "./latest";
import { FriendZoneHub } from "./friend-zone-hub";
import { monthFromParam } from "@/features/programs/program-nav";
import type { Program } from "@/lib/api/programs";
import {
  BATTLE_PARAM,
  type MeLoad,
  type QuizzesView,
  VIEW_PARAM,
  battleFromParam,
  searchForBattle,
  searchForView,
  shownView,
  showsSwitch,
  viewFromParam,
} from "./model";

/**
 * Quizzes & Games' two sides: Practice (the page's own content, untouched) | Friend Zone. The side is kept in
 * `?zone=friends` beside `?month=` (and an open battle in `?battle=`), written with `history.replaceState` as the
 * month is, so a reload or a notification's link shows the same place. The switch exists only once `GET /v1/friend-zone/me` says Friend Zone is on for the account.
 */
export function QuizzesSwitch({ program, practice }: { program: Program; practice: ReactNode }) {
  const params = useSearchParams();
  const requested = viewFromParam(params.get(VIEW_PARAM));
  const battleId = battleFromParam(params.get(BATTLE_PARAM));
  const month = monthFromParam(params.get("month") ?? undefined, program.months.map((item) => item.month));
  const { load, retry, refresh, setMe } = useFriendZoneMe();
  const view = shownView(load, requested);
  const practiceButton = useRef<HTMLButtonElement>(null);
  const [pulse, setPulse] = useState(0);
  // Plan section 10: /me every 15 s while the Friend Zone side is shown and the tab visible; the lists follow it.
  usePulse(view === "friend-zone", () => {
    refresh();
    setPulse((count) => count + 1);
  });
  const replace = (search: string) => window.history.replaceState(null, "", search || window.location.pathname);
  const pick = (next: QuizzesView) => {
    // Practice closes an open battle: while an exam answer isn't saved yet, ask first (the one navigation guard).
    if (next === "practice" && !navigationGuard.confirmLeave()) return false;
    replace(searchForView(window.location.search, next));
    return true;
  };
  const toPractice = () => {
    if (!pick("practice")) return;
    practiceButton.current?.focus(); // the button that was used goes away with the Friend Zone side
  };
  return (
    <>
      {showsSwitch(load, requested) && <ViewSwitch view={view} onPick={pick} practiceRef={practiceButton} />}
      {view === "practice" ? (
        practice
      ) : (
        <FriendZoneHub
          load={load}
          retry={retry}
          refresh={refresh}
          setMe={setMe}
          onPractice={toPractice}
          social={{ program, month, pulse, battleId, onBattle: (id) => replace(searchForBattle(window.location.search, id)) }}
        />
      )}
    </>
  );
}

const VIEWS: readonly { view: QuizzesView; label: string }[] = [
  { view: "practice", label: "Practice" },
  { view: "friend-zone", label: "Friend Zone" },
];

export function ViewSwitch({
  view,
  onPick,
  practiceRef,
}: {
  view: QuizzesView;
  onPick: (view: QuizzesView) => void;
  practiceRef?: Ref<HTMLButtonElement>;
}) {
  return (
    <div role="group" aria-label="Quizzes & Games view" className="mb-6 inline-flex gap-1 rounded-xl border border-border bg-surface-2 p-1">
      {VIEWS.map((item) => {
        const pressed = item.view === view;
        return (
          <button
            key={item.view}
            ref={item.view === "practice" ? practiceRef : undefined}
            type="button"
            aria-pressed={pressed}
            onClick={() => onPick(item.view)}
            className={cn(
              "focus-ring min-h-11 rounded-lg px-4 text-sm transition-colors",
              pressed ? "bg-card font-semibold text-foreground elev-1" : "font-medium text-text-secondary hover:text-foreground",
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

const PULSE_MS = 15_000;

/** Calls `beat` every 15 s while `on` and the tab is visible. */
function usePulse(on: boolean, beat: () => void) {
  const latest = useRef(beat);
  latest.current = beat;
  useEffect(() => {
    if (!on) return;
    const id = setInterval(() => document.visibilityState === "visible" && latest.current(), PULSE_MS);
    return () => clearInterval(id);
  }, [on]);
}

/** `GET /v1/friend-zone/me` with its states. `refresh` keeps what is shown until the new read arrives. */
function useFriendZoneMe() {
  const [load, setLoad] = useState<MeLoad>({ status: "loading" });
  const live = useRef(true);
  const [ticket] = useState(sequence);
  const refresh = useCallback(() => {
    const newest = ticket();
    getFriendZoneMe().then(
      (me) => live.current && newest() && setLoad({ status: "ready", me }),
      // A failed pulse keeps what is shown (a live battle stays mounted); only a first load or a retry shows the error.
      (error) => live.current && newest() && setLoad((shown) => keepOnFailure<MeLoad>(shown, { status: "error", error })),
    );
  }, [ticket]);
  useEffect(() => {
    live.current = true;
    refresh();
    return () => {
      live.current = false;
    };
  }, [refresh]);
  const retry = useCallback(() => {
    setLoad({ status: "loading" });
    refresh();
  }, [refresh]);
  const setMe = useCallback(
    (update: (me: FriendZoneMe) => FriendZoneMe) => {
      ticket(); // a save is newer than any read still in flight: that read's older profile must not come back
      setLoad((current) => (current.status === "ready" ? { status: "ready", me: update(current.me) } : current));
    },
    [ticket],
  );
  return { load, retry, refresh, setMe };
}
