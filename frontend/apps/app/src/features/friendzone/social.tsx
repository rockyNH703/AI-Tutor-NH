"use client";

import { Button } from "@v1/ui/button";
import { ErrorState } from "@v1/ui/empty-state";
import { Input } from "@v1/ui/input";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { cn } from "@v1/ui/utils";
import { ChevronDown, Swords } from "lucide-react";
import { type FormEvent, type ReactNode, type Ref, type RefObject, useCallback, useEffect, useId, useRef, useState } from "react";

import { Field } from "@/features/settings/primitives";
import { ApiError } from "@/lib/api/errors";
import { CONTRACT } from "@/lib/api/contract.gen";
import type { Program } from "@/lib/api/programs";
import type { Uuid } from "@/lib/api/types";
import {
  type FriendZonePlayer,
  type FriendZoneRequests,
  type PlayerStats,
  answerRequest,
  blockPlayer,
  getPlayer,
  listBlocks,
  listFriends,
  listRequests,
  removeFriend,
  searchFriendZone,
  sendRequest,
  unblockPlayer,
} from "./api";
import { type Action, type Failure, failureCopy, foundAction, programsLine } from "./model";
import { LIST, PlayerAvatar, PlayerName, PlayerRow } from "./player";
import { listBattles } from "./battle-api";
import { BattleView } from "./battle-view";
import { BattlesSection } from "./battles-section";
import { ChallengeDialog } from "./challenge-dialog";
import { DailySection } from "./daily-view";
import { ProgressPanel, StatsBlock } from "./progress-view";
import { keepOnFailure, sequence } from "./latest";
import { ReportDialog } from "./report-dialog";
import { SafetyConfirm } from "./safety-confirm";

/**
 * A member's Friend Zone: requests, battles, friends (challenge, remove, block, report), adding a classmate by invoice
 * number, the profile and the blocked list; or one battle, full width, when `?battle=` names it. Lists are read together and read again after every change and whenever the tab is shown again;
 * the API is the only judge of what is current. Removing, blocking and declining are silent toward the other member.
 */

export type Lists = { friends: FriendZonePlayer[]; requests: FriendZoneRequests; blocks: FriendZonePlayer[] };
type ListsLoad = { status: "loading" } | { status: "ready"; lists: Lists } | { status: "error" };

function useLists(onGone: () => void, paused: boolean): { load: ListsLoad; reload: () => void; retry: () => void } {
  const [load, setLoad] = useState<ListsLoad>({ status: "loading" });
  const live = useRef(true);
  const gone = useRef(onGone);
  gone.current = onGone;
  const pausedNow = useRef(paused);
  pausedNow.current = paused;
  const [ticket] = useState(sequence);
  const reload = useCallback(() => {
    const newest = ticket();
    Promise.all([listFriends(), listRequests(), listBlocks()]).then(
      ([friends, requests, blocks]) => live.current && newest() && setLoad({ status: "ready", lists: { friends, requests, blocks } }),
      (error) => {
        if (!live.current || !newest()) return;
        setLoad((shown) => keepOnFailure<ListsLoad>(shown, { status: "error" }));
        if (error instanceof ApiError && error.code === "not_found") gone.current(); // no longer a member: /me says why
      },
    );
  }, [ticket]);
  useEffect(() => {
    live.current = true;
    reload();
    // Not while a battle is open (the lists aren't on screen); closing the battle reads them once.
    const shown = () => document.visibilityState === "visible" && !pausedNow.current && reload();
    document.addEventListener("visibilitychange", shown);
    return () => {
      live.current = false;
      document.removeEventListener("visibilitychange", shown);
    };
  }, [reload]);
  const retry = useCallback(() => {
    setLoad({ status: "loading" });
    reload();
  }, [reload]);
  return { load, reload, retry };
}

/** A section's own polite announcement line, always mounted so what is put in it is read out. */
function Note({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="text-sm text-foreground [&:not(:empty)]:mt-2">
      {children}
    </p>
  );
}

function SectionHeading({ id, children, headingRef }: { id: string; children: ReactNode; headingRef?: Ref<HTMLHeadingElement> }) {
  return (
    <h3 id={id} ref={headingRef} tabIndex={headingRef ? -1 : undefined} className="focus-ring rounded-md text-base font-semibold text-foreground">
      {children}
    </h3>
  );
}

/** Run one change, then say how it went in the section's note and read the lists again. */
function useChange(reload: () => void) {
  const [note, setNote] = useState<ReactNode>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (key: string, action: Action, change: () => Promise<ReactNode>): Promise<boolean> => {
    setBusy(key);
    setNote(null);
    try {
      setNote(await change());
      reload();
      return true;
    } catch (error) {
      const failure: Failure = failureCopy(error, action);
      setNote(<span className="text-destructive">{failure.message}</span>);
      if (failure.reload) reload();
      return false;
    } finally {
      setBusy(null);
    }
  };
  return { note, setNote, busy, run };
}

export type SocialProps = {
  refreshMe: () => void;
  justJoined: boolean;
  profile: ReactNode;
  /** The program page this is on: challenges are in it. */
  program: Program;
  /** The month the page shows (`?month=`), picked first in a challenge. */
  month: number;
  /** Goes up on the hub's 15-second poll: read the lists again. */
  pulse: number;
  /** The battle open on screen (`?battle=`), or null. */
  battleId: Uuid | null;
  /** Your own public id, for your progress (Phase 2). */
  playerId: Uuid;
  onBattle: (battleId: Uuid | null) => void;
};

export function SocialPanel({ refreshMe, justJoined, profile, program, month, pulse, battleId, playerId, onBattle }: SocialProps) {
  const { load, reload, retry } = useLists(refreshMe, battleId !== null);
  const battlesHeading = useRef<HTMLHeadingElement>(null);
  const [back, setBack] = useState(false);
  const inBattle = battleId !== null;
  useEffect(() => {
    if (pulse > 0 && !inBattle) reload();
  }, [pulse, reload, inBattle]);
  useEffect(() => {
    if (back && !battleId) {
      battlesHeading.current?.focus(); // the battle closed: back where battles are listed
      setBack(false);
    }
  }, [back, battleId]);
  if (battleId)
    return (
      <BattleView
        key={battleId}
        battleId={battleId}
        onClose={() => {
          setBack(true);
          onBattle(null);
          reload(); // paused while the battle was open
        }}
        onOpen={onBattle}
      />
    );
  return (
    <>
      {load.status === "loading" && (
        <SkeletonGroup className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full rounded-xl" />
          <Skeleton className="h-24 w-full rounded-xl" />
        </SkeletonGroup>
      )}
      {load.status === "error" && (
        <ErrorState
          title="Your friends couldn't be loaded"
          description="Check your connection, then try again."
          action={
            <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={retry}>
              Try again
            </Button>
          }
        />
      )}
      {load.status === "ready" && <RequestsSection requests={load.lists.requests} reload={reload} />}
      <DailySection program={program} pulse={pulse} />
      <BattlesSection pulse={pulse} onOpen={onBattle} headingRef={battlesHeading} />
      {load.status === "ready" && (
        <FriendsSection friends={load.lists.friends} reload={reload} program={program} month={month} onBattle={onBattle} />
      )}
      <AddFriend focusOnMount={justJoined} reload={reload} />
      <ProgressPanel playerId={playerId} pulse={pulse} />
      {profile}
      {load.status === "ready" && <BlockedSection blocked={load.lists.blocks} reload={reload} />}
    </>
  );
}

export function RequestsSection({ requests, reload }: { requests: FriendZoneRequests; reload: () => void }) {
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const change = useChange(reload);
  const { note, busy, run } = change;
  const safety = useSafety(change, reload, heading);
  const { incoming, outgoing } = requests;
  return (
    <section aria-labelledby={headingId}>
      <SectionHeading id={headingId} headingRef={heading}>
        Requests
      </SectionHeading>
      <Note>{note}</Note>
      {incoming.length === 0 && outgoing.length === 0 && <p className="mt-1 text-sm text-text-secondary">No requests right now.</p>}
      {incoming.length > 0 && (
        <>
          <h4 className="mb-2 mt-3 text-sm font-semibold text-foreground">Received ({incoming.length})</h4>
          <ul className={LIST}>
            {incoming.map(({ requestId, player }) => (
              <li key={requestId}>
                <PlayerRow player={player} line="Wants to be friends">
                  <Button
                    type="button"
                    size="sm"
                    className="min-h-11"
                    disabled={busy !== null}
                    onClick={() =>
                      run(requestId, "answer", async () => {
                        await answerRequest(requestId, "accept");
                        return (
                          <>
                            You're now friends with <PlayerName name={player.displayName} />.
                          </>
                        );
                      })
                    }
                  >
                    Accept
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="min-h-11"
                    disabled={busy !== null}
                    onClick={() =>
                      run(requestId, "answer", async () => {
                        await answerRequest(requestId, "decline");
                        return "Request declined. They aren't told.";
                      })
                    }
                  >
                    Decline
                  </Button>
                  <SafetyButtons player={player} busy={busy !== null} ask={safety.ask} />
                </PlayerRow>
              </li>
            ))}
          </ul>
        </>
      )}
      {outgoing.length > 0 && (
        <>
          <h4 className="mb-2 mt-3 text-sm font-semibold text-foreground">Sent ({outgoing.length})</h4>
          <ul className={LIST}>
            {outgoing.map(({ requestId, player }) => (
              <li key={requestId}>
                <PlayerRow player={player} line="Waiting for an answer">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="min-h-11"
                    disabled={busy !== null}
                    onClick={() =>
                      run(requestId, "answer", async () => {
                        await answerRequest(requestId, "cancel");
                        return "Request cancelled.";
                      })
                    }
                  >
                    Cancel request
                  </Button>
                </PlayerRow>
              </li>
            ))}
          </ul>
        </>
      )}
      {safety.dialogs}
    </section>
  );
}

type Pending = { kind: "remove" | "block" | "report"; player: FriendZonePlayer } | null;
type SafetyKind = NonNullable<Pending>["kind"];
type Change = ReturnType<typeof useChange>;

/**
 * Remove, block and report, with their dialogs: Remove and Block are confirmed first, and every one is silent toward
 * the other member. Once a confirmed change removes the row, focus goes to the section's heading.
 */
function useSafety({ busy, run, setNote }: Change, reload: () => void, heading: RefObject<HTMLHeadingElement | null>) {
  const [pending, setPending] = useState<Pending>(null);
  const removed = useRef(false);
  const closeFocus = (event: Event) => {
    if (!removed.current) return;
    event.preventDefault();
    removed.current = false;
    heading.current?.focus();
  };
  const confirm = async () => {
    if (!pending || pending.kind === "report") return;
    const { kind, player } = pending;
    removed.current = await run(player.playerId, kind, async () => {
      if (kind === "remove") {
        await removeFriend(player.playerId);
        return "Removed from your friends. They aren't told.";
      }
      await blockPlayer(player.playerId);
      return "Blocked. They aren't told, and you won't see or find each other in Friend Zone.";
    });
    setPending(null);
  };
  const dialogs = (
    <>
      <SafetyConfirm
        open={pending?.kind === "remove" || pending?.kind === "block"}
        onOpenChange={(next) => !next && busy === null && setPending(null)}
        title={pending?.kind === "block" ? "Block this member?" : "Remove this friend?"}
        description={
          pending && pending.kind !== "report" ? (
            pending.kind === "block" ? (
              <>
                <PlayerName name={pending.player.displayName} /> isn't told. Your friendship, any open request and any open battle end, and
                you won't see or find each other in Friend Zone. You can unblock later, but the friendship doesn't come back.
              </>
            ) : (
              <>
                <PlayerName name={pending.player.displayName} /> isn't told. You can send a new request later if they can still be found.
              </>
            )
          ) : undefined
        }
        confirmLabel={pending?.kind === "block" ? "Block" : "Remove friend"}
        pending={busy !== null}
        onConfirm={confirm}
        onCloseAutoFocus={closeFocus}
      />
      {pending?.kind === "report" && (
        <ReportDialog
          player={pending.player}
          open
          onOpenChange={(next) => !next && setPending(null)}
          onSent={() => {
            setPending(null);
            setNote("Report sent. Thank you for telling us.");
          }}
          onStale={(failure) => {
            setPending(null);
            setNote(failure.message);
            reload();
          }}
        />
      )}
    </>
  );
  return { ask: (kind: SafetyKind, player: FriendZonePlayer) => setPending({ kind, player }), dialogs };
}

function SafetyButtons({
  player,
  busy,
  ask,
  remove = false,
}: {
  player: FriendZonePlayer;
  busy: boolean;
  ask: (kind: SafetyKind, player: FriendZonePlayer) => void;
  remove?: boolean;
}) {
  const button = (kind: SafetyKind, label: string) => (
    <Button type="button" size="sm" variant="outline" className="min-h-11" disabled={busy} onClick={() => ask(kind, player)}>
      {label}
    </Button>
  );
  return (
    <>
      {remove && button("remove", "Remove friend")}
      {button("block", "Block")}
      {button("report", "Report")}
    </>
  );
}

export function FriendsSection({
  friends,
  reload,
  program,
  month,
  onBattle,
}: {
  friends: FriendZonePlayer[];
  reload: () => void;
  program: Program;
  month: number;
  onBattle: (battleId: Uuid) => void;
}) {
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const change = useChange(reload);
  const { note, setNote, busy } = change;
  const safety = useSafety(change, reload, heading);
  const [open, setOpen] = useState<string | null>(null);
  const [challenging, setChallenging] = useState<FriendZonePlayer | null>(null);

  // 409 battle_exists: the pair's open battle, by the id the error carries; else found in the open list (one per pair).
  const openExisting = async (friend: FriendZonePlayer, battleId: Uuid | null) => {
    setChallenging(null);
    if (battleId) return onBattle(battleId);
    try {
      const found = (await listBattles("open")).items.find((battle) => battle.opponent.playerId === friend.playerId);
      if (found) return onBattle(found.battleId);
    } catch {
      // the note below still says where it is
    }
    setNote("You already have a battle with this friend. It's in your Battles list.");
  };

  return (
    <section aria-labelledby={headingId}>
      <SectionHeading id={headingId} headingRef={heading}>
        Friends{friends.length > 0 ? ` (${friends.length})` : ""}
      </SectionHeading>
      <Note>{note}</Note>
      {friends.length === 0 ? (
        <p className="mt-1 text-sm text-text-secondary">No friends yet. Add a classmate with their invoice number below.</p>
      ) : (
        <ul className={cn(LIST, "mt-3")}>
          {friends.map((friend) => {
            const expanded = open === friend.playerId;
            const panelId = `${headingId}-${friend.playerId}`;
            return (
              <li key={friend.playerId}>
                <button
                  type="button"
                  aria-expanded={expanded}
                  aria-controls={panelId}
                  onClick={() => setOpen(expanded ? null : friend.playerId)}
                  className="focus-ring-inset flex min-h-11 w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2"
                >
                  <PlayerAvatar player={friend} />
                  <span className="min-w-0 flex-1 break-words text-sm text-foreground">
                    <PlayerName name={friend.displayName} />
                  </span>
                  <ChevronDown aria-hidden className={cn("h-4 w-4 shrink-0 text-text-secondary transition-transform", expanded && "rotate-180")} />
                </button>
                {expanded && (
                  <div id={panelId} className="flex flex-col gap-3 px-4 pb-4">
                    <p className="text-sm text-text-secondary">{programsLine(friend.programs)}</p>
                    <FriendStats playerId={friend.playerId} />
                    <div className="flex flex-wrap gap-2">
                      <Button type="button" size="sm" className="min-h-11" disabled={busy !== null} onClick={() => setChallenging(friend)}>
                        <Swords aria-hidden className="h-4 w-4" />
                        Challenge
                      </Button>
                      <SafetyButtons player={friend} busy={busy !== null} ask={safety.ask} remove />
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {safety.dialogs}
      {challenging && (
        <ChallengeDialog
          friend={challenging}
          program={program}
          month={month}
          open
          onOpenChange={(next) => !next && setChallenging(null)}
          onCreated={(battleId) => {
            setChallenging(null);
            onBattle(battleId);
          }}
          onExists={(battleId) => openExisting(challenging, battleId)}
        />
      )}
    </section>
  );
}

const INVOICE_MAX = CONTRACT.FriendZoneSearchRequest.invoiceNumber.maxLength;

export function AddFriend({ focusOnMount = false, reload }: { focusOnMount?: boolean; reload: () => void }) {
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [invoice, setInvoice] = useState("");
  const [searching, setSearching] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [found, setFound] = useState<FriendZonePlayer | null>(null);
  const { note, busy, run } = useChange(reload);

  useEffect(() => {
    if (focusOnMount) heading.current?.focus(); // just joined: the next step
  }, [focusOnMount]);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!invoice.trim()) {
      setProblem("Enter an invoice number.");
      return input.current?.focus();
    }
    setSearching(true);
    setProblem(null);
    setFound(null);
    try {
      setFound(await searchFriendZone(invoice.trim()));
    } catch (error) {
      setProblem(failureCopy(error, "search").message);
      input.current?.focus();
    } finally {
      setSearching(false);
    }
  };

  const ask = (player: FriendZonePlayer) =>
    run(player.playerId, "request", async () => {
      const answer = await sendRequest(player.playerId);
      setFound(answer);
      setInvoice(""); // done with the number: it isn't kept on screen
      return answer.relation === "friend" ? (
        <>
          You're now friends with <PlayerName name={answer.displayName} />.
        </>
      ) : (
        <>
          Request sent to <PlayerName name={answer.displayName} />.
        </>
      );
    });

  const action = found ? foundAction(found.relation) : null;
  return (
    <section aria-labelledby={headingId}>
      <SectionHeading id={headingId} headingRef={heading}>
        Add a friend
      </SectionHeading>
      <p className="mt-1 max-w-prose text-sm text-text-secondary">
        Ask a classmate for their invoice number. They need to have turned on "Let classmates find me".
      </p>
      <form noValidate onSubmit={search} className="mt-3 flex flex-col gap-3">
        <Field label="Classmate's invoice number" hint="Used only to find them. It's never shown to anyone." error={problem}>
          {(props) => (
            <Input
              {...props}
              ref={input}
              autoComplete="off"
              spellCheck={false}
              maxLength={INVOICE_MAX}
              value={invoice}
              onChange={(event) => setInvoice(event.target.value)}
            />
          )}
        </Field>
        <Button type="submit" className="min-h-11 self-start" disabled={searching}>
          {searching ? "Finding…" : "Find"}
        </Button>
      </form>
      <Note>{note}</Note>
      {found && action && (
        <div className={cn(LIST, "mt-3")}>
          <PlayerRow player={found} line={action.kind === "note" ? action.text : undefined}>
            {action.kind !== "note" && (
              <Button type="button" size="sm" className="min-h-11" disabled={busy !== null} onClick={() => ask(found)}>
                {action.label}
              </Button>
            )}
          </PlayerRow>
        </div>
      )}
    </section>
  );
}

export function BlockedSection({ blocked, reload }: { blocked: FriendZonePlayer[]; reload: () => void }) {
  const headingId = useId();
  const { note, busy, run } = useChange(reload);
  return (
    <section aria-labelledby={headingId}>
      <SectionHeading id={headingId}>Blocked</SectionHeading>
      <p className="mt-1 text-sm text-text-secondary">Blocked members can't find or see you in Friend Zone, and you don't see them.</p>
      <Note>{note}</Note>
      {blocked.length === 0 ? (
        <p className="mt-1 text-sm text-text-secondary">You haven't blocked anyone.</p>
      ) : (
        <ul className={cn(LIST, "mt-3")}>
          {blocked.map((player) => (
            <li key={player.playerId}>
              <PlayerRow player={player}>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="min-h-11"
                  disabled={busy !== null}
                  onClick={() =>
                    run(player.playerId, "unblock", async () => {
                      await unblockPlayer(player.playerId);
                      return "Unblocked. A friendship that ended with the block doesn't come back.";
                    })
                  }
                >
                  Unblock
                </Button>
              </PlayerRow>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** A friend's record and badges, read when their panel opens; nothing at all when they keep their stats hidden. */
function FriendStats({ playerId }: { playerId: Uuid }) {
  const [stats, setStats] = useState<PlayerStats | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    getPlayer(playerId).then(
      (player) => live && setStats(player.stats ?? null),
      () => live && setStats(null), // unreadable now: shown as if hidden, never as zeros
    );
    return () => {
      live = false;
    };
  }, [playerId]);
  if (stats === undefined) return <Skeleton aria-busy="true" className="h-16 w-full rounded-xl" />;
  return stats ? <StatsBlock stats={stats} own={false} /> : null;
}
