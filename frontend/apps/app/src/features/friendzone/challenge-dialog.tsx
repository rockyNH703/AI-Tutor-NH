"use client";

import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { type FormEvent, useEffect, useId, useState } from "react";

import { CONTRACT } from "@/lib/api/contract.gen";
import { ApiError } from "@/lib/api/errors";
import { type BankTopic, type Program, getProgramMonth } from "@/lib/api/programs";
import type { Uuid } from "@/lib/api/types";
import { type BattleLength, type Difficulty, createBattle } from "./battle-api";
import { DIFFICULTY, battleFailure, existingBattle } from "./battle-model";
import type { FriendZonePlayer } from "./api";
import { PlayerName } from "./player";

const DIFFICULTIES = CONTRACT.FriendZoneCreateBattleRequest.difficulty.enum.map((value) => ({ value, label: DIFFICULTY[value] }));
// One minute per question (D4): the length is also the exam's time.
const LENGTHS = CONTRACT.FriendZoneCreateBattleRequest.length.enum.map((value) => ({ value: String(value), label: `${value} questions · ${value} min` }));
const SELECT =
  "focus-field h-11 w-full rounded-xl border border-input bg-card px-3 text-base text-foreground disabled:cursor-not-allowed disabled:bg-surface-2 sm:h-10 sm:text-sm";

/** Challenge a friend to a battle in this program: a month, an optional topic, a difficulty and a length. */
export function ChallengeDialog({
  friend,
  program,
  month,
  open,
  onOpenChange,
  onCreated,
  onExists,
}: {
  friend: FriendZonePlayer;
  program: Program;
  /** The month the page shows, picked first. */
  month: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (battleId: Uuid) => void;
  /** 409 `battle_exists`: the pair already has an open battle: its id from the error, or null (the list finds it). */
  onExists: (battleId: Uuid | null) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        {/* Mounted per opening: a fresh requestId each time, so a retry of the same send answers the same battle. */}
        {open && <ChallengeForm friend={friend} program={program} month={month} onClose={() => onOpenChange(false)} onCreated={onCreated} onExists={onExists} />}
      </DialogContent>
    </Dialog>
  );
}

export function ChallengeForm({
  friend,
  program,
  month: firstMonth,
  onClose,
  onCreated,
  onExists,
}: {
  friend: FriendZonePlayer;
  program: Program;
  month: number;
  onClose: () => void;
  onCreated: (battleId: Uuid) => void;
  onExists: (battleId: Uuid | null) => void;
}) {
  const ids = useId();
  const [requestId] = useState(() => crypto.randomUUID());
  const [month, setMonth] = useState(firstMonth);
  const [topicId, setTopicId] = useState("");
  const [topics, setTopics] = useState<BankTopic[] | null>(null);
  const [difficulty, setDifficulty] = useState<Difficulty>("mixed");
  const [length, setLength] = useState<BattleLength>(10);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setTopics(null);
    setTopicId("");
    getProgramMonth(program.slug, month).then(
      (detail) => live && setTopics(unique(detail.topics)),
      () => live && setTopics([]), // no topic list: "Any topic" still works
    );
    return () => {
      live = false;
    };
  }, [program.slug, month]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      const battle = await createBattle({
        opponentId: friend.playerId,
        program: program.slug,
        month,
        ...(topicId ? { topicId } : {}),
        difficulty,
        length,
        requestId,
      });
      onCreated(battle.battleId);
    } catch (error) {
      if (error instanceof ApiError && error.code === "battle_exists") return onExists(existingBattle(error));
      setProblem(battleFailure(error, "create").message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-4">
      <DialogHeader>
        <DialogTitle>Challenge a friend</DialogTitle>
        <DialogDescription>
          An exam battle with <PlayerName name={friend.displayName} /> on {program.title}: the same questions for both of you, one minute
          each on one shared timer. They have 10 minutes to accept.
        </DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-1.5">
        <label htmlFor={`${ids}-month`} className="text-sm font-bold text-foreground">
          Month
        </label>
        <select id={`${ids}-month`} value={month} onChange={(event) => setMonth(Number(event.target.value))} className={SELECT}>
          {program.months.map((item) => (
            <option key={item.month} value={item.month}>
              Month {item.month}: {item.title}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor={`${ids}-topic`} className="text-sm font-bold text-foreground">
          Topic <span className="font-normal text-text-secondary">(optional)</span>
        </label>
        <select
          id={`${ids}-topic`}
          value={topicId}
          disabled={topics === null}
          onChange={(event) => setTopicId(event.target.value)}
          className={SELECT}
        >
          <option value="">{topics === null ? "Loading topics…" : "Any topic this month"}</option>
          {topics?.map((topic) => (
            <option key={topic.topicId} value={topic.topicId}>
              {topic.title}
            </option>
          ))}
        </select>
      </div>
      <Pills legend="Difficulty" value={difficulty} options={DIFFICULTIES} onChange={setDifficulty} />
      <Pills legend="Length" value={String(length)} options={LENGTHS} onChange={(value) => setLength(Number(value) as BattleLength)} />
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      <DialogFooter className="gap-2 sm:gap-2">
        <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" className="min-h-11" disabled={busy}>
          {busy ? "Sending…" : "Send challenge"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function unique(topics: BankTopic[]): BankTopic[] {
  const seen = new Set<string>();
  return topics.filter((topic) => !seen.has(topic.topicId) && seen.add(topic.topicId));
}

/** One choice from a few, as real radio buttons drawn as 44 px pills (arrow keys move between them). */
function Pills<T extends string>({
  legend,
  value,
  options,
  onChange,
}: {
  legend: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  const name = useId();
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-sm font-bold text-foreground">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <label key={option.value} className="relative">
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="peer absolute inset-0 z-10 cursor-pointer opacity-0"
            />
            <span className="flex min-h-11 cursor-pointer items-center rounded-xl border border-border bg-card px-4 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-2 hover:text-foreground peer-checked:border-brand peer-checked:bg-brand-soft peer-checked:text-brand peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2">
              {option.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
