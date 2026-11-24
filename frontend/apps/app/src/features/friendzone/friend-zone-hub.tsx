"use client";

import { Badge } from "@v1/ui/badge";
import { Button } from "@v1/ui/button";
import { EmptyState, ErrorState } from "@v1/ui/empty-state";
import { Input } from "@v1/ui/input";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { PauseCircle, UsersRound } from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { Field, ToggleRow } from "@/features/settings/primitives";
import { CONTRACT } from "@/lib/api/contract.gen";
import { type FriendZoneMe, type FriendZoneProfile, type ProfileFields, joinFriendZone, updateFriendZoneProfile } from "./api";
import {
  type Failure,
  type MeLoad,
  CONSENT,
  NAME_HINT,
  NEW_PROFILE,
  PHOTO_NOTE,
  PRIVACY,
  changedFields,
  cleanName,
  failureCopy,
  fieldsOf,
  hubState,
  initials,
  nameProblem,
} from "./model";
import { SocialPanel, type SocialProps } from "./social";

/**
 * The Friend Zone side of Quizzes & Games: join, then requests, friends, adding a classmate, your profile and the
 * blocked list (social.tsx); battles are still to come. It renders inside the program page's one scroller. Every state comes from
 * `GET /v1/friend-zone/me` (model.ts `hubState`); a failure that means `/me` changed reads it again and says why.
 */

export type HubProps = {
  load: MeLoad;
  /** Read `/me` again from scratch (after a failed read). */
  retry: () => void;
  /** Read `/me` again, keeping what is shown until it arrives. */
  refresh: () => void;
  /** Replace the known `/me` with what a join or save returned. */
  setMe: (update: (me: FriendZoneMe) => FriendZoneMe) => void;
  onPractice: () => void;
  /** What the member's side needs from the page: its program and month, the poll's pulse, the open battle. */
  social: Omit<SocialProps, "refreshMe" | "justJoined" | "profile" | "playerId">;
};

export function FriendZoneHub({ load, retry, refresh, setMe, onPractice, social }: HubProps) {
  const [notice, setNotice] = useState<string | null>(null);
  const [joined, setJoined] = useState(false);
  const state = hubState(load);
  // A failure that means `/me` changed (already joined, edited elsewhere, no longer open) is said here, above whatever
  // the new read shows, so it outlives the form that failed.
  const report = (failure: Failure) => {
    setNotice(failure.message);
    refresh();
  };
  const setProfile = (profile: FriendZoneProfile) => setMe((me) => ({ ...me, member: true, profile }));

  return (
    <div>
      {/* Always mounted, so screen readers announce what is put in it. */}
      <div role="status">
        {notice && <p className="mb-6 rounded-xl border border-border bg-surface-2 px-4 py-3 text-sm text-foreground">{notice}</p>}
      </div>
      <div className="flex flex-col gap-6">
      {state.kind === "loading" && <HubLoading />}
      {state.kind === "error" && (
        <ErrorState
          title="Friend Zone couldn't be loaded"
          description="Check your connection, then try again."
          action={
            <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={retry}>
              Try again
            </Button>
          }
        />
      )}
      {state.kind === "ineligible" && (
        <EmptyState
          icon={UsersRound}
          title="Friend Zone isn't open to your account"
          description="It opens for accounts whose NetworkersHome program is open. Your practice questions are still here."
          action={<PracticeButton onPractice={onPractice} />}
        />
      )}
      {state.kind === "suspended" && (
        <EmptyState
          icon={PauseCircle}
          title="Friend Zone is paused for your account"
          description="You can keep using Practice in the meantime."
          action={<PracticeButton onPractice={onPractice} />}
        />
      )}
      {state.kind === "join" && (
        <JoinPanel
          onJoined={(profile) => {
            setNotice("You've joined Friend Zone.");
            setJoined(true);
            setProfile(profile);
          }}
          onStale={report}
        />
      )}
      {state.kind === "member" && (
        <SocialPanel
          {...social}
          playerId={state.profile.playerId}
          refreshMe={refresh}
          justJoined={joined}
          profile={
            <MemberPanel
              profile={state.profile}
              onSaved={(profile) => {
                setNotice(null);
                setProfile(profile);
              }}
              onStale={report}
            />
          }
        />
      )}
      </div>
    </div>
  );
}

function PracticeButton({ onPractice }: { onPractice: () => void }) {
  return (
    <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={onPractice}>
      Go to Practice
    </Button>
  );
}

export function HubLoading() {
  return (
    <SkeletonGroup className="flex flex-col gap-3">
      <Skeleton className="h-40 w-full rounded-2xl" />
      <Skeleton className="h-28 w-full rounded-2xl" />
    </SkeletonGroup>
  );
}

const PANEL = "rounded-2xl border border-border bg-card p-4 sm:p-6";

export function JoinPanel({ onJoined, onStale }: { onJoined: (profile: FriendZoneProfile) => void; onStale: (failure: Failure) => void }) {
  const headingId = useId();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const submit = async (fields: ProfileFields) => {
    setBusy(true);
    setFailure(null);
    try {
      onJoined(await joinFriendZone({ ...fields, displayName: cleanName(fields.displayName) }));
    } catch (error) {
      const found = failureCopy(error, "join");
      if (found.reload) onStale(found);
      else setFailure(found);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-labelledby={headingId} className={PANEL}>
      <h3 id={headingId} className="text-lg font-semibold text-foreground">
        Join Friend Zone
      </h3>
      <p className="mt-1 max-w-prose text-sm text-text-secondary">
        Add classmates as friends, challenge them to live quiz battles on your program's topics, and take the daily
        challenge. Choose the name they'll see; everything else stays off until you turn it on.
      </p>
      <ProfileForm
        initial={NEW_PROFILE}
        submitLabel="Join Friend Zone"
        busyLabel="Joining…"
        busy={busy}
        failure={failure}
        onSubmit={submit}
        beforeSubmit={<p className="max-w-prose text-sm text-text-secondary">{CONSENT}</p>}
      />
    </section>
  );
}

export function MemberPanel({
  profile,
  onSaved,
  onStale,
}: {
  profile: FriendZoneProfile;
  onSaved: (profile: FriendZoneProfile) => void;
  onStale: (failure: Failure) => void;
}) {
  const headingId = useId();
  const edit = useRef<HTMLButtonElement>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const wasEditing = useRef(false);

  useEffect(() => {
    if (wasEditing.current && !editing) edit.current?.focus(); // the form closed: back to what opened it
    wasEditing.current = editing;
  }, [editing]);

  const before = fieldsOf(profile);
  const submit = async (fields: ProfileFields) => {
    const set = changedFields(before, fields);
    if (Object.keys(set).length === 0) return setEditing(false);
    setBusy(true);
    setFailure(null);
    try {
      onSaved(await updateFriendZoneProfile(profile.version, set));
      setEditing(false);
    } catch (error) {
      const found = failureCopy(error, "save");
      if (found.reload) onStale(found); // the form stays open and shows the latest profile (keyed by its version)
      else setFailure(found);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby={headingId} className={PANEL}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id={headingId} className="text-lg font-semibold text-foreground">
          Your profile
        </h3>
        {!editing && (
          <Button ref={edit} type="button" size="sm" variant="outline" className="min-h-11" onClick={() => setEditing(true)}>
            Edit profile
          </Button>
        )}
      </div>
      {editing ? (
        <ProfileForm
          key={profile.version}
          initial={before}
          submitLabel="Save changes"
          busyLabel="Saving…"
          busy={busy}
          failure={failure}
          focusName
          onSubmit={submit}
          onCancel={() => {
            setFailure(null);
            setEditing(false);
          }}
        />
      ) : (
        <ProfileSummary profile={profile} />
      )}
    </section>
  );
}

export function ProfileSummary({ profile }: { profile: FriendZoneProfile }) {
  return (
    <>
      <div className="mt-4 flex min-w-0 items-center gap-3">
        <span aria-hidden className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-surface-2 text-base font-semibold text-foreground">
          {initials(profile.displayName)}
        </span>
        <div className="min-w-0">
          <p dir="auto" className="break-words text-lg font-semibold leading-snug text-foreground">
            {profile.displayName}
          </p>
          <p className="text-xs text-text-secondary">Your Friend Zone name</p>
        </div>
        {profile.qa && (
          <Badge variant="info" className="ml-auto" title="This account only meets other test accounts.">
            Test account
          </Badge>
        )}
      </div>
      <dl className="mt-4 divide-y divide-border border-t border-border">
        {PRIVACY.map(({ key, label }) => (
          <div key={key} className="flex items-start justify-between gap-4 py-3">
            <dt className="min-w-0 text-sm text-foreground">{label}</dt>
            <dd className="shrink-0 text-sm font-semibold text-foreground">{profile[key] ? "On" : "Off"}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-text-secondary">{PHOTO_NOTE}</p>
    </>
  );
}

const NAME_INPUT_MAX = CONTRACT.FriendZoneJoinRequest.displayName.maxLength;

export function ProfileForm({
  initial,
  submitLabel,
  busyLabel,
  busy,
  failure,
  focusName = false,
  onSubmit,
  onCancel,
  beforeSubmit,
}: {
  initial: ProfileFields;
  submitLabel: string;
  busyLabel: string;
  busy: boolean;
  failure: Failure | null;
  focusName?: boolean;
  onSubmit: (fields: ProfileFields) => void;
  onCancel?: () => void;
  /** Shown just above the buttons (the join's consent text). */
  beforeSubmit?: ReactNode;
}) {
  const [fields, setFields] = useState(initial);
  const [checked, setChecked] = useState(false);
  const [refused, setRefused] = useState<Failure | null>(null);
  const name = useRef<HTMLInputElement>(null);
  const privacyId = useId();

  useEffect(() => {
    if (focusName) name.current?.focus();
  }, [focusName]);
  // A refusal of the name is kept until the name changes; any other failure belongs to this attempt only.
  useEffect(() => setRefused(failure?.onName ? failure : null), [failure]);

  const nameError = (checked ? nameProblem(fields.displayName) : null) ?? refused?.message ?? null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setChecked(true);
    if (nameProblem(fields.displayName)) return name.current?.focus();
    onSubmit(fields);
  };

  return (
    <form noValidate onSubmit={submit} className="mt-5 flex flex-col gap-5">
      <Field label="Friend Zone name" hint={NAME_HINT} error={nameError}>
        {(props) => (
          <Input
            {...props}
            ref={name}
            dir="auto"
            autoComplete="off"
            spellCheck={false}
            maxLength={NAME_INPUT_MAX}
            value={fields.displayName}
            onChange={(event) => {
              setRefused(null);
              setFields({ ...fields, displayName: event.target.value });
            }}
          />
        )}
      </Field>
      <fieldset aria-describedby={privacyId} className="flex flex-col gap-4">
        <legend className="mb-1 text-sm font-bold text-foreground">Privacy</legend>
        <p id={privacyId} className="text-sm text-text-secondary">
          Each choice is off until you turn it on. {PHOTO_NOTE}
        </p>
        {PRIVACY.map(({ key, label, description }) => (
          <ToggleRow
            key={key}
            label={label}
            description={description}
            checked={fields[key]}
            onCheckedChange={(value) => setFields({ ...fields, [key]: value })}
          />
        ))}
      </fieldset>
      {beforeSubmit}
      {failure && !failure.onName && (
        <p role="alert" className="text-sm text-destructive">
          {failure.message}
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <Button type="submit" className="min-h-11" disabled={busy}>
          {busy ? busyLabel : submitLabel}
        </Button>
        {onCancel && (
          <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
