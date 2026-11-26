"use client";

import { Badge } from "@v1/ui/badge";
import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { Input } from "@v1/ui/input";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { Laptop, MonitorSmartphone, RotateCcw } from "lucide-react";
import { type FormEvent, type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { InlineAction } from "@/components/inline-action";
import { authErrorMessage } from "@/components/auth-form";
import { QrCode } from "@/components/qr-code";
import { useUser } from "@/components/user-context";
import { useStores } from "@/features/chat/chat-context";
import { deleteAccount, getMe, revokeOtherSessions } from "@/lib/api/auth";
import { ApiError, describeError } from "@/lib/api/errors";
import { listSessions, revokeSession } from "@/lib/api/identity";
import { PASSWORD_MAX, PASSWORD_MIN, type SessionInfo } from "@/lib/api/types";
import { cacheAttributes } from "@/lib/appearance";
import { authClient } from "@/lib/auth/client";
import { relativeTime, shortDate } from "@/lib/time";
import { Disclosure, Field, SectionHeading, SettingsCard } from "./primitives";
import type { SectionProps } from "./section-view";
import { useUnsavedChanges } from "./use-unsaved-changes";

function wrongPassword(error: unknown): boolean {
  return error instanceof ApiError && error.code === "invalid_credentials";
}

function needsFreshSignIn(error: unknown): boolean {
  return error instanceof ApiError && error.code === "reauth_required";
}

const SIGN_IN_AGAIN = "For your security, sign out and sign in again, then try within 15 minutes.";

/**
 * The account's ways to sign in (Better Auth's accounts): a password, Google (its account row id),
 * and whether two-step sign-in is on (Better Auth's session user).
 */
type Methods = { password: boolean; google: string | null; twoFactor: boolean } | null;

function useSignInMethods(): [Methods, () => Promise<void>] {
  const [methods, setMethods] = useState<Methods>(null);
  const load = useCallback(async () => {
    const [{ data }, session] = await Promise.all([authClient.listAccounts(), authClient.getSession()]);
    const accounts = data ?? [];
    setMethods({
      password: accounts.some((account) => account.providerId === "credential"),
      google: accounts.find((account) => account.providerId === "google")?.id ?? null,
      twoFactor: (session.data?.user as { twoFactorEnabled?: boolean } | undefined)?.twoFactorEnabled === true,
    });
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  return [methods, load];
}

/**
 * A status line still loading: one line of text's height, so the card does not grow when it arrives. A span (it sits
 * in the card's description paragraph), announced as loading.
 */
function LoadingLine() {
  return (
    <span role="status" className="block h-5">
      <span aria-hidden className="block h-4 w-40 max-w-full translate-y-0.5 animate-soft-pulse rounded-md bg-surface-2" />
      <span className="sr-only">Loading</span>
    </span>
  );
}

/** The password: a row with its status, whose button opens the form in place. */
function PasswordCard({ hasPassword, onSet }: { hasPassword: boolean | null; onSet: () => Promise<void> }) {
  const { user, setUser } = useUser();
  const [open, setOpen] = useState(false);
  const first = useRef<HTMLInputElement>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useUnsavedChanges(Boolean(current || next || confirm));

  // Opened from its button: the first field takes focus (the button that opened it is gone). Closed again: focus goes
  // back to that button, since the form that had it is gone.
  const opener = useRef<HTMLButtonElement>(null);
  const returning = useRef(false);
  useEffect(() => {
    if (open) first.current?.focus();
    else if (returning.current) {
      returning.current = false;
      opener.current?.focus();
    }
  }, [open]);

  const close = () => {
    returning.current = true;
    setOpen(false);
    setCurrent("");
    setNext("");
    setConfirm("");
    setError(null);
  };

  if (hasPassword === null) {
    return (
      <SettingsCard
        title="Password"
        description={<LoadingLine />}
        footer={
          <Button type="button" size="sm" variant="outline" disabled>
            Change password
          </Button>
        }
      />
    );
  }

  if (!open) {
    const status = hasPassword
      ? user.passwordChangedAt
        ? `Last changed ${relativeTime(user.passwordChangedAt)} (${shortDate(user.passwordChangedAt)}).`
        : "Not changed since you joined."
      : "None yet: you sign in with Google or an email code.";
    return (
      <SettingsCard
        title="Password"
        description={status}
        footer={
          <Button ref={opener} type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
            {hasPassword ? "Change password" : "Set a password"}
          </Button>
        }
      />
    );
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (next.length < PASSWORD_MIN || next.length > PASSWORD_MAX) {
      setError(`Use ${PASSWORD_MIN}–${PASSWORD_MAX} characters.`);
      return;
    }
    if (next !== confirm) {
      setError("Those passwords don't match.");
      return;
    }
    setPending(true);
    try {
      if (hasPassword) {
        const { error: failed } = await authClient.changePassword({
          currentPassword: current,
          newPassword: next,
          revokeOtherSessions: true,
        });
        if (failed) {
          setError(
            failed.code === "INVALID_PASSWORD"
              ? "Your current password isn't right."
              : failed.code === "SESSION_NOT_FRESH"
                ? SIGN_IN_AGAIN
                : authErrorMessage(failed),
          );
          return;
        }
        toast.success("Password changed. Your other devices have been signed out.");
      } else {
        const response = await fetch("/api/account/password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ newPassword: next }),
        });
        if (response.status === 409) {
          const { error: body } = (await response.json().catch(() => ({}))) as { error?: { code?: string } };
          setError(body?.code === "reauth_required" ? SIGN_IN_AGAIN : "This account already has a password.");
          return;
        }
        if (!response.ok) {
          setError(
            response.status === 400
              ? authErrorMessage({ code: "PASSWORD_COMPROMISED" })
              : response.status === 503
                ? authErrorMessage({ code: "PASSWORD_CHECK_UNAVAILABLE" })
                : "Something went wrong. Please try again.",
          );
          return;
        }
        toast.success("Password set. Your other devices have been signed out.");
        await onSet();
      }
      close();
      setUser(await getMe()); // for the new "last changed" date
    } finally {
      setPending(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <SettingsCard
        title="Password"
        description={
          hasPassword
            ? "You'll stay signed in here; other devices will be signed out."
            : "Sign in with a password too. It needs a sign-in in the last 15 minutes."
        }
        footer={
          <>
            <Button type="button" size="sm" variant="outline" onClick={close} disabled={pending}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={pending}>
              {pending ? "Saving…" : hasPassword ? "Change password" : "Set password"}
            </Button>
          </>
        }
      >
        {hasPassword && (
          <Field label="Current password">
            {(props) => (
              <Input
                {...props}
                ref={first}
                className="w-full sm:max-w-[20rem]"
                type="password"
                autoComplete="current-password"
                required
                maxLength={PASSWORD_MAX}
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            )}
          </Field>
        )}
        <Field label="New password" hint={`${PASSWORD_MIN}–${PASSWORD_MAX} characters.`}>
          {(props) => (
            <Input
              {...props}
              ref={hasPassword ? undefined : first}
              className="w-full sm:max-w-[20rem]"
              type="password"
              autoComplete="new-password"
              required
              minLength={PASSWORD_MIN}
              maxLength={PASSWORD_MAX}
              value={next}
              onChange={(event) => setNext(event.target.value)}
            />
          )}
        </Field>
        <Field label="Confirm new password">
          {(props) => (
            <Input
              {...props}
              className="w-full sm:max-w-[20rem]"
              type="password"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
            />
          )}
        </Field>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </SettingsCard>
    </form>
  );
}

function DevicesCard() {
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setSessions((await listSessions()).items);
    } catch (caught) {
      setLoadError(describeError(caught).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const signOut = async (session: SessionInfo) => {
    setBusy(session.id);
    try {
      await revokeSession(session.id);
      setSessions((list) => list?.filter((item) => item.id !== session.id) ?? null);
      toast.success(`Signed out ${session.device}.`);
    } catch (caught) {
      // Already gone (signed out elsewhere, or expired): the list was simply out of date.
      if (caught instanceof ApiError && caught.code === "not_found") await load();
      else toast.error(describeError(caught).message);
    } finally {
      setBusy(null);
    }
  };

  const signOutOthers = async () => {
    setBusy("others");
    try {
      await revokeOtherSessions();
      toast.success("Signed out of all other devices.");
      await load();
    } catch (caught) {
      toast.error(describeError(caught).message);
    } finally {
      setBusy(null);
    }
  };

  const current = sessions?.find((session) => session.current) ?? null;
  const others = sessions?.filter((session) => !session.current) ?? [];

  const row = (session: SessionInfo) => (
    <li key={session.id} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
      <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-surface-2 text-text-secondary">
        <Laptop className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-sm font-bold text-foreground">
          {session.device}
          {session.current && <Badge variant="brand">This device</Badge>}
        </p>
        <p className="text-xs text-text-secondary">
          {session.current ? "Active now" : `Active ${relativeTime(session.lastSeenAt)}`}
          {session.ipMasked && ` · ${session.ipMasked}`}
        </p>
      </div>
      {!session.current && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => signOut(session)}
          disabled={busy !== null}
          aria-label={`Sign out ${session.device}, active ${relativeTime(session.lastSeenAt)}`}
        >
          {busy === session.id ? "Signing out…" : "Sign out"}
        </Button>
      )}
    </li>
  );

  // The same height loading and loaded, however many devices there are (no layout shift): this device's row, then
  // one line for the others, which open on request.
  return (
    <SettingsCard
      title="Signed-in devices"
      description="Only the network is shown, never your full address."
      footer={
        <Button type="button" size="sm" variant="outline" onClick={signOutOthers} disabled={busy !== null || others.length === 0}>
          {busy === "others" ? "Signing out…" : "Sign out other devices"}
        </Button>
      }
    >
      {loadError ? (
        <div className="text-sm text-text-secondary">
          <p>{loadError}</p>
          <InlineAction icon={RotateCcw} onClick={() => void load()} className="-ml-2 mt-1">
            Try again
          </InlineAction>
        </div>
      ) : sessions === null ? (
        <SkeletonGroup label="Loading devices" className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <Skeleton className="h-9 w-9 shrink-0 rounded-lg" />
            <div className="flex flex-1 flex-col gap-1.5">
              <Skeleton className="h-4 w-32 rounded-md" />
              <Skeleton className="h-3 w-24 rounded-md" />
            </div>
          </div>
          <Skeleton className="h-11 w-full rounded-xl" />
        </SkeletonGroup>
      ) : (
        <div className="flex flex-col gap-3">
          {current ? (
            <ul className="divide-y divide-border">{row(current)}</ul>
          ) : (
            <p className="flex min-h-9 items-center text-sm text-text-secondary">This device isn't in the list.</p>
          )}
          {others.length === 0 ? (
            <p className="flex min-h-11 items-center px-3.5 text-sm text-text-secondary">No other devices.</p>
          ) : (
            <Disclosure
              icon={MonitorSmartphone}
              label={`Show ${others.length} other ${others.length === 1 ? "device" : "devices"}`}
            >
              <ul aria-label="Other devices" className="divide-y divide-border">
                {others.map(row)}
              </ul>
            </Disclosure>
          )}
        </div>
      )}
    </SettingsCard>
  );
}

function GoogleCard({ accountId, onChange }: { accountId: string | null | undefined; onChange: () => Promise<void> }) {
  const connected = accountId != null;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(() => {
    // Google sends the student back here; a link it refused arrives as ?error=.
    if (typeof window === "undefined") return null;
    const code = new URLSearchParams(window.location.search).get("error");
    if (!code) return null;
    return code.toLowerCase().includes("email")
      ? "That Google account uses a different email address. Connect the Google account for this email."
      : "Google didn't connect. Please try again.";
  });

  const connect = async () => {
    setError(null);
    setPending(true);
    const { error: failed } = await authClient.linkSocial({
      provider: "google",
      callbackURL: "/settings/privacy",
      errorCallbackURL: "/settings/privacy",
    });
    if (failed) {
      setPending(false);
      setError(failed.code === "SESSION_NOT_FRESH" ? SIGN_IN_AGAIN : "Google didn't connect. Please try again.");
    }
  };

  const disconnect = async () => {
    setError(null);
    setPending(true);
    try {
      if (!accountId) return;
      const { error: failed } = await authClient.unlinkAccount({ accountId });
      if (failed) {
        setError(
          failed.code === "FAILED_TO_UNLINK_LAST_ACCOUNT"
            ? "Google is how you sign in. Set a password first, then disconnect it."
            : failed.code === "SESSION_NOT_FRESH" || failed.status === 403
              ? SIGN_IN_AGAIN
              : "Something went wrong. Please try again.",
        );
        return;
      }
      toast.success("Google disconnected.");
      await onChange();
    } finally {
      setPending(false);
    }
  };

  return (
    <SettingsCard
      title="Google"
      description={
        accountId === undefined ? (
          <LoadingLine />
        ) : connected ? (
          "Connected: you can sign in with Google."
        ) : (
          "Not connected. It must use this account's email address."
        )
      }
      footer={
        accountId === undefined ? (
          <Button type="button" size="sm" variant="outline" disabled>
            Connect Google
          </Button>
        ) : connected ? (
          <Button type="button" size="sm" variant="outline" onClick={() => void disconnect()} disabled={pending}>
            {pending ? "Disconnecting…" : "Disconnect Google"}
          </Button>
        ) : (
          <Button type="button" size="sm" variant="outline" onClick={() => void connect()} disabled={pending}>
            {pending ? "Opening Google…" : "Connect Google"}
          </Button>
        )
      }
    >
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </SettingsCard>
  );
}

type TwoFactorStage =
  | { step: "idle" }
  | { step: "password"; action: "enable" | "disable" | "codes" }
  | { step: "scan"; uri: string; secret: string; codes: string[] }
  | { step: "codes"; codes: string[] };

/** Better Auth's two-step answers in this app's words. */
function twoFactorError(failure: { code?: string; status?: number }): string {
  if (failure.code === "INVALID_PASSWORD") return "That password isn't right.";
  if (failure.code === "SESSION_NOT_FRESH") return SIGN_IN_AGAIN;
  return authErrorMessage(failure);
}

function RecoveryCodes({ codes }: { codes: string[] }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm text-text-secondary">
        Save these recovery codes somewhere safe (not on the phone with your authenticator). Each one signs you
        in once if you lose your phone. They are shown only now.
      </p>
      <ul aria-label="Recovery codes" className="grid grid-cols-2 gap-1.5 rounded-xl border border-border bg-surface-2 p-3 font-mono text-sm">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            void navigator.clipboard
              .writeText(codes.join("\n"))
              .then(() => toast.success("Recovery codes copied."))
              .catch(() => toast.error("Couldn't copy. Write them down instead."))
          }
        >
          Copy codes
        </Button>
      </div>
    </div>
  );
}

function TwoFactorCard({
  enabled,
  hasPassword,
  onChange,
}: {
  /** null while the sign-in methods load. */
  enabled: boolean | null;
  hasPassword: boolean | null;
  onChange: () => Promise<void>;
}) {
  const [stage, setStage] = useState<TwoFactorStage>({ step: "idle" });
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const passwordId = useId();
  const codeId = useId();
  useUnsavedChanges(stage.step === "scan");

  const reset = (next: TwoFactorStage = { step: "idle" }) => {
    setStage(next);
    setPassword("");
    setCode("");
    setError(null);
  };

  const withPending = async (action: () => Promise<void>) => {
    setError(null);
    setPending(true);
    try {
      await action();
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setPending(false);
    }
  };

  const confirmPassword = (event: FormEvent) => {
    event.preventDefault();
    if (stage.step !== "password") return;
    void withPending(async () => {
      if (stage.action === "enable") {
        const { data, error: failed } = await authClient.twoFactor.enable({ password });
        if (failed || !data || !("totpURI" in data) || !data.totpURI) {
          setError(failed ? twoFactorError(failed) : "Something went wrong. Please try again.");
          return;
        }
        const secret = new URL(data.totpURI).searchParams.get("secret") ?? "";
        reset({ step: "scan", uri: data.totpURI, secret, codes: data.backupCodes ?? [] });
      } else if (stage.action === "disable") {
        const { error: failed } = await authClient.twoFactor.disable({ password });
        if (failed) {
          setError(twoFactorError(failed));
          return;
        }
        toast.success("Two-step sign-in is off.");
        reset();
        await onChange();
      } else {
        const { data, error: failed } = await authClient.twoFactor.generateBackupCodes({ password });
        if (failed || !data) {
          setError(failed ? twoFactorError(failed) : "Something went wrong. Please try again.");
          return;
        }
        reset({ step: "codes", codes: data.backupCodes });
      }
    });
  };

  const confirmCode = (event: FormEvent) => {
    event.preventDefault();
    void withPending(async () => {
      const { error: failed } = await authClient.twoFactor.verifyTotp({ code: code.trim() });
      if (failed) {
        setError(twoFactorError(failed));
        return;
      }
      toast.success("Two-step sign-in is on.");
      reset();
      await onChange();
    });
  };

  const description =
    enabled === null ? (
      <LoadingLine />
    ) : enabled ? (
      "On: signing in asks for a code from your authenticator app."
    ) : hasPassword ? (
      "Off. Adds a code from an authenticator app to every sign-in."
    ) : (
      "Off. Set a password first: turning it on needs one."
    );

  let footer: ReactNode = null;
  if (enabled === null) {
    footer = (
      <Button type="button" size="sm" variant="outline" disabled>
        Turn on two-step sign-in
      </Button>
    );
  } else if (stage.step === "idle" && enabled) {
    footer = (
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => reset({ step: "password", action: "codes" })}>
          New recovery codes
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => reset({ step: "password", action: "disable" })}>
          Turn off
        </Button>
      </div>
    );
  } else if (stage.step === "idle" && hasPassword) {
    footer = (
      <Button type="button" size="sm" variant="outline" onClick={() => reset({ step: "password", action: "enable" })}>
        Turn on two-step sign-in
      </Button>
    );
  }

  return (
    <SettingsCard title="Two-step sign-in" description={description} footer={footer}>
      {stage.step === "password" && (
        <form onSubmit={confirmPassword} className="flex flex-col gap-3">
          <label htmlFor={passwordId} className="text-sm font-bold text-foreground">
            Your password
          </label>
          <Input
            id={passwordId}
            className="w-full sm:max-w-[20rem]"
            type="password"
            autoComplete="current-password"
            required
            maxLength={PASSWORD_MAX}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={pending || !password}>
              {pending
                ? "Please wait…"
                : stage.action === "enable"
                  ? "Continue"
                  : stage.action === "disable"
                    ? "Turn off two-step sign-in"
                    : "Make new codes"}
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => reset()} disabled={pending}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      {stage.step === "scan" && (
        <form onSubmit={confirmCode} className="flex flex-col gap-4">
          <p className="text-sm text-text-secondary">
            1. Scan this code with your authenticator app, or enter the setup key by hand.
          </p>
          <QrCode value={stage.uri} label="QR code for your authenticator app" />
          <p className="text-sm">
            Setup key: <span className="break-all font-mono">{stage.secret.replace(/(.{4})/g, "$1 ").trim()}</span>
          </p>
          <p className="text-sm text-text-secondary">2. Save your recovery codes.</p>
          <RecoveryCodes codes={stage.codes} />
          <label htmlFor={codeId} className="text-sm font-bold text-foreground">
            3. Enter the 6-digit code the app shows
          </label>
          <Input
            id={codeId}
            className="w-full font-mono tracking-[0.3em] sm:max-w-[12rem]"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern={"\\d{6}"}
            maxLength={6}
            required
            value={code}
            onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={pending || code.length !== 6}>
              {pending ? "Checking…" : "Turn on"}
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => reset()} disabled={pending}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      {stage.step === "codes" && (
        <div className="flex flex-col gap-3">
          <RecoveryCodes codes={stage.codes} />
          <p className="text-sm text-text-secondary">Your old recovery codes no longer work.</p>
          <div>
            <Button type="button" size="sm" onClick={() => reset()}>
              Done
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </SettingsCard>
  );
}

function DeleteAccountCard({ hasPassword }: { hasPassword: boolean }) {
  const { chat } = useStores();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fieldId = useId();

  const confirmDelete = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      await deleteAccount(hasPassword ? { password } : {});
      chat.reset();
      cacheAttributes(null);
      window.location.assign("/login?reason=deleted");
    } catch (caught) {
      setPending(false);
      setError(
        wrongPassword(caught)
          ? "That password isn't right."
          : needsFreshSignIn(caught)
            ? SIGN_IN_AGAIN
            : describeError(caught).message,
      );
    }
  };

  return (
    <SettingsCard
      title="Delete account"
      titleAs="h4"
      tone="danger"
      description="Deletes your account and everything in it: chats, settings, progress and photo. It can't be undone."
      footer={
        <>
          <Button type="button" size="sm" variant="destructive" onClick={() => setOpen(true)}>
            Delete account
          </Button>
          <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) {
            setPassword("");
            setError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <form onSubmit={confirmDelete}>
            <DialogHeader>
              <DialogTitle>Delete your account?</DialogTitle>
              <DialogDescription>
                {hasPassword
                  ? "Enter your password to confirm. Everything in your account will be deleted."
                  : "Everything in your account will be deleted. You must have signed in within the last 15 minutes."}
              </DialogDescription>
            </DialogHeader>
            <div className="my-4">
              {hasPassword && (
                <>
                  <label htmlFor={fieldId} className="text-sm font-bold text-foreground">
                    Password
                  </label>
                  <Input
                    id={fieldId}
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                </>
              )}
              {error && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                  {error}
                </p>
              )}
            </div>
            <DialogFooter className="gap-2 sm:gap-2">
              <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={pending}>
                Cancel
              </Button>
              <Button type="submit" variant="destructive" disabled={pending || (hasPassword && !password)}>
                {pending ? "Deleting…" : "Delete account"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
          </Dialog>
        </>
      }
    />
  );
}

export function PrivacySection({ googleEnabled }: SectionProps) {
  const [methods, reload] = useSignInMethods();
  return (
    <>
      <SectionHeading title="Privacy & Security" description="How you sign in, where you're signed in, and deleting your account." />
      {/* Every card is drawn at once, its status loading in place: nothing below moves when it arrives. */}
      <div className="flex flex-col gap-3">
        <PasswordCard hasPassword={methods ? methods.password : null} onSet={reload} />
        <TwoFactorCard enabled={methods ? methods.twoFactor : null} hasPassword={methods ? methods.password : null} onChange={reload} />
        {(googleEnabled || methods?.google) && <GoogleCard accountId={methods ? methods.google : undefined} onChange={reload} />}
        <DevicesCard />
        <h3 className="mt-3 font-display text-base font-bold tracking-tight text-destructive">Danger zone</h3>
        <DeleteAccountCard hasPassword={methods?.password ?? true} />
      </div>
    </>
  );
}
