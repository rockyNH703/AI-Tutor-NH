"use client";

import { cn } from "@v1/ui/utils";
import { ArrowRight, ChevronLeft, Eye, EyeOff, Loader2, Lock, ShieldCheck, User } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { CODE_LENGTH, DISPLAY_NAME_MAX, PASSWORD_MAX, PASSWORD_MIN } from "@/lib/api/types";
import { authClient } from "@/lib/auth/client";
import { CODE_REJECTED, ERRORS, authErrorMessage, oauthErrorMessage } from "@/lib/auth/messages";
import { BrandMark } from "./brand/brand-mark";
import { GoogleMark } from "./google-mark";
import { ThemeMenu } from "./theme-menu";
import { Turnstile } from "./turnstile";

export { authErrorMessage, oauthErrorMessage } from "@/lib/auth/messages";

/**
 * Signing in and up, email codes and password reset, through Better Auth (lib/auth), one quiet
 * step at a time. The session cookie is set by the server; nothing here ever holds a token. After
 * signing in the student goes back to `redirectTo`, which the page has already passed through
 * `safeRedirect`.
 *
 * Steps:
 *   email           Sign in / Sign up, then the address (or Google, while it is still empty)
 *   password        sign in with it, or "Forgot password?" / "Email me a code"
 *   name            signing up: the name the account will carry
 *   code            the 6-digit code: confirms a new account, signs in, or starts a reset
 *   createPassword  the new account's password (can be skipped: codes and Google still sign in)
 *   newPassword     a reset's new password, sent with its code; then the student is signed in
 *   twoFactor       the second step, when the account has two-step sign-in: an authenticator
 *                   code or a recovery code. Every first step can lead here: password, email
 *                   code, a reset (which signs in), and Google (back at /login?step=two-factor).
 *
 * Nothing is asked of the server before the password or the code, so the address alone never
 * says whether an account exists. When the server asks for a CAPTCHA (CAPTCHA_REQUIRED: an
 * address or a network is being hammered), Cloudflare Turnstile appears and the next attempt
 * carries its single-use token.
 *
 * Motion: one step is on screen at a time. The leaving step fades and drifts away, the next one
 * rises in, both on one decisive ease-out; opacity and transform only. With reduced motion (the
 * system's or the app's own setting) every change is immediate.
 */
type Tab = "signIn" | "signUp";
type Step = "email" | "password" | "name" | "code" | "createPassword" | "newPassword" | "twoFactor";
type CodeFor = "signUp" | "signIn" | "reset";

/** The steps whose requests may be asked for a CAPTCHA (lib/auth/config.ts). */
const GUARDED: ReadonlySet<Step> = new Set(["password", "name", "code", "newPassword"]);

const RESEND_SECONDS = 30;
/** How long the leaving step takes before the next one arrives. */
const LEAVE_MS = 350;
const EASE = "ease-[cubic-bezier(0.23,1,0.32,1)]";

const label = "text-sm font-medium uppercase tracking-widest text-slate-500 dark:text-white/70";
// The hairline under the field is its edge (WCAG 1.4.11: at least 3:1 against the ground, about
// 3.4:1 on white and 3.7:1 on the dark ground), and turns blue while it has focus.
const bigInput =
  "w-full border-b border-black/45 bg-transparent pb-2 text-center text-xl font-semibold tracking-tight text-slate-900 caret-blue-500 outline-none transition-colors placeholder:text-slate-300 focus:border-blue-500 disabled:opacity-50 sm:text-2xl dark:border-white/40 dark:text-white dark:placeholder:text-white/20 dark:focus:border-blue-400";
const primaryPill = cn(
  "auth-glow group flex items-center gap-3 rounded-full bg-slate-900 px-6 py-3 font-semibold text-white transition-all duration-300 hover:scale-105 active:scale-95 disabled:opacity-80 disabled:hover:scale-100",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
  "dark:bg-white dark:text-black",
  EASE,
);
const quietLink =
  "rounded text-sm text-slate-500 transition-colors hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 disabled:opacity-50 dark:text-white/70 dark:hover:text-white";

/** A first step's answer when the account has two-step sign-in. */
function needsSecondStep(data: unknown): boolean {
  return (data as { twoFactorRedirect?: unknown } | null | undefined)?.twoFactorRedirect === true;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return (
    document.documentElement.dataset.motion === "reduce" || window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** A pill that swaps in and out without moving the layout; the hidden one is out of reach. */
function Swap({ shown, children, className }: { shown: boolean; children: ReactNode; className?: string }) {
  return (
    <div
      inert={!shown || undefined}
      aria-hidden={!shown || undefined}
      className={cn(
        "absolute flex flex-col items-center transition-all duration-500",
        EASE,
        shown ? "pointer-events-auto translate-y-0 scale-100 opacity-100" : "pointer-events-none translate-y-4 scale-95 opacity-0",
        className,
      )}
    >
      {children}
    </div>
  );
}

function Arrow({ pending }: { pending: boolean }) {
  return pending ? (
    <Loader2 aria-hidden className="h-5 w-5 animate-spin" />
  ) : (
    <ArrowRight aria-hidden className="h-4 w-4 transition-transform group-hover:translate-x-1" />
  );
}

export function AuthForm({
  initialMode,
  redirectTo,
  notice,
  googleEnabled = false,
  turnstileSiteKey = null,
  oauthError,
  available = true,
}: {
  initialMode: "signIn" | "signUp" | "twoFactor";
  redirectTo: string;
  notice?: string | null;
  googleEnabled?: boolean;
  turnstileSiteKey?: string | null;
  oauthError?: string | null;
  /** False when this page was asked for at an origin sign-in does not trust (options.ts). */
  available?: boolean;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>(initialMode === "signUp" ? "signUp" : "signIn");
  const [step, setStep] = useState<Step>(initialMode === "twoFactor" ? "twoFactor" : "email");
  const [leaving, setLeaving] = useState(false);
  const [back, setBack] = useState(false); // the direction of the last move
  const [codeFor, setCodeFor] = useState<CodeFor>("signIn");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false); // the second step with a recovery code
  const [pending, setPending] = useState(false);
  const busy = useRef(false); // one request at a time, whatever the clicks and Enter keys do
  const [error, setError] = useState<string | null>(oauthErrorMessage(oauthError));
  const [shaking, setShaking] = useState(false);
  const [info, setInfo] = useState<string | null>(notice ?? null);
  const [cooldown, setCooldown] = useState(0);
  // `round` remounts the widget: each token is single-use.
  const [captcha, setCaptcha] = useState<{ needed: boolean; token: string | null; round: number }>({
    needed: false,
    token: null,
    round: 0,
  });
  const input = useRef<HTMLInputElement>(null);
  const shakeTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((value) => value - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  // Each step arrives with its field ready to type in.
  useEffect(() => {
    if (!leaving && available) input.current?.focus({ preventScroll: true });
  }, [step, leaving, available]);

  useEffect(() => () => window.clearTimeout(shakeTimer.current), []);

  const fail = (message: string) => {
    setError(message);
    setShaking(false);
    window.requestAnimationFrame(() => setShaking(true));
    window.clearTimeout(shakeTimer.current);
    shakeTimer.current = window.setTimeout(() => setShaking(false), 400);
  };

  /** Move to a step: the current one leaves first, then the next one arrives. */
  const go = (next: Step, options: { back?: boolean; info?: string | null; error?: string | null } = {}) => {
    const arrive = () => {
      setStep(next);
      setError(options.error ?? null);
      setInfo(options.info ?? null);
      setShowPassword(false);
      setRecovery(false);
      if (next !== "password") setPassword("");
      if (next !== "newPassword") setCode("");
      setLeaving(false);
    };
    setBack(options.back === true);
    if (prefersReducedMotion()) {
      arrive();
      return;
    }
    setLeaving(true);
    window.setTimeout(arrive, LEAVE_MS);
  };

  /** The CAPTCHA token for this attempt, if there is one; spent once sent. */
  const captchaOptions = () => {
    if (!captcha.token) return undefined;
    const token = captcha.token;
    setCaptcha((current) => ({ ...current, token: null, round: current.round + 1 }));
    return { headers: { "x-captcha-response": token } };
  };

  const failedWith = (failure: { code?: string; status?: number }) => {
    if ((failure.code === "CAPTCHA_REQUIRED" || failure.code === "CAPTCHA_INVALID") && turnstileSiteKey) {
      setCaptcha((current) => (current.needed ? current : { needed: true, token: null, round: current.round + 1 }));
    }
    fail(authErrorMessage(failure));
  };

  const signedIn = () => {
    router.replace(redirectTo);
    router.refresh();
  };

  const run = (action: () => Promise<void>) => async (event?: FormEvent) => {
    event?.preventDefault();
    if (busy.current || leaving) return;
    if (captcha.needed && !captcha.token && GUARDED.has(step)) {
      fail("Please confirm you're not a robot first.");
      return;
    }
    busy.current = true;
    setError(null);
    setPending(true);
    try {
      await action();
    } catch {
      fail("Something went wrong. Please try again.");
    } finally {
      busy.current = false;
      setPending(false);
    }
  };

  const sendCode = async (purpose: "sign-in" | "forget-password"): Promise<boolean> => {
    const options = captchaOptions();
    const { error: failed } =
      purpose === "sign-in"
        ? await authClient.emailOtp.sendVerificationOtp({ email: email.trim(), type: "sign-in" }, options)
        : await authClient.emailOtp.requestPasswordReset({ email: email.trim() }, options);
    if (failed) {
      failedWith(failed);
      return false;
    }
    setCooldown(RESEND_SECONDS);
    return true;
  };

  const submitEmail = (event: FormEvent) => {
    event.preventDefault();
    if (busy.current || leaving) return;
    if (!/^[^\s@]+@[^\s@]+$/.test(email.trim())) {
      fail(ERRORS.INVALID_EMAIL!);
      return;
    }
    go(tab === "signUp" ? "name" : "password");
  };

  const submitSignIn = run(async () => {
    if (!password) {
      fail("Enter your password.");
      return;
    }
    const { data, error: failed } = await authClient.signIn.email({ email: email.trim(), password }, captchaOptions());
    if (failed) failedWith(failed);
    else if (needsSecondStep(data)) go("twoFactor");
    else signedIn();
  });

  const emailMeACode = run(async () => {
    if (!(await sendCode("sign-in"))) return;
    setCodeFor("signIn");
    go("code", { info: `If ${email.trim()} can be used, a ${CODE_LENGTH}-digit code is on its way.` });
  });

  const forgotPassword = run(async () => {
    if (!(await sendCode("forget-password"))) return;
    setCodeFor("reset");
    go("code", { info: `If ${email.trim()} has an account, a reset code is on its way.` });
  });

  const submitName = run(async () => {
    if (!name.trim()) {
      fail("Enter your name.");
      return;
    }
    if (!(await sendCode("sign-in"))) return;
    setCodeFor("signUp");
    go("code", { info: `If ${email.trim()} can be used, a ${CODE_LENGTH}-digit code is on its way.` });
  });

  const submitCode = run(async () => {
    if (code.trim().length !== CODE_LENGTH) {
      fail(`Enter the ${CODE_LENGTH}-digit code.`);
      return;
    }
    // A reset's code is checked with the new password, in one request.
    if (codeFor === "reset") {
      go("newPassword");
      return;
    }
    const creating = codeFor === "signUp";
    const { data, error: failed } = await authClient.signIn.emailOtp(
      {
        email: email.trim(),
        otp: code.trim(),
        ...(creating && name.trim() ? { name: name.trim() } : {}),
      },
      captchaOptions(),
    );
    if (failed) failedWith(failed);
    else if (needsSecondStep(data)) go("twoFactor");
    else if (creating) go("createPassword");
    else signedIn();
  });

  const submitFirstPassword = run(async () => {
    if (password.length < PASSWORD_MIN) {
      fail(ERRORS.PASSWORD_TOO_SHORT!);
      return;
    }
    const response = await fetch("/api/account/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ newPassword: password }),
    });
    // 409 password_already_set: the address already had an account with a password; the code
    // proved it is theirs, so they are simply signed in.
    if (response.ok || response.status === 409) signedIn();
    else if (response.status === 422) fail(ERRORS.PASSWORD_TOO_SHORT!);
    else if (response.status === 400) fail(ERRORS.PASSWORD_COMPROMISED!);
    else if (response.status === 503) fail(ERRORS.PASSWORD_CHECK_UNAVAILABLE!);
    else fail("Something went wrong. Please try again.");
  });

  const submitReset = run(async () => {
    if (password.length < PASSWORD_MIN) {
      fail(ERRORS.PASSWORD_TOO_SHORT!);
      return;
    }
    const { error: failed } = await authClient.emailOtp.resetPassword(
      { email: email.trim(), otp: code.trim(), password },
      captchaOptions(),
    );
    if (failed) {
      // A wrong, expired or used-up code: back to the code, with why.
      if (failed.code && CODE_REJECTED.has(failed.code)) go("code", { back: true, error: authErrorMessage(failed) });
      else failedWith(failed);
      return;
    }
    // Every device was signed out; this one signs in with the new password.
    const signed = await authClient.signIn.email({ email: email.trim(), password });
    if (signed.error) go("password", { info: "Password changed. Sign in with your new password." });
    else if (needsSecondStep(signed.data)) go("twoFactor", { info: "Password changed. Now the second step." });
    else signedIn();
  });

  const submitSecondStep = run(async () => {
    if (!code.trim()) {
      fail(recovery ? "Enter a recovery code." : "Enter the 6-digit code.");
      return;
    }
    const { error: failed } = recovery
      ? await authClient.twoFactor.verifyBackupCode({ code: code.trim() })
      : await authClient.twoFactor.verifyTotp({ code: code.trim() });
    if (!failed) {
      signedIn();
      return;
    }
    if (failed.code === "INVALID_TWO_FACTOR_COOKIE" || failed.code === "TOO_MANY_ATTEMPTS_REQUEST_NEW_CODE") {
      go("email", { back: true, error: authErrorMessage(failed) });
      return;
    }
    fail(authErrorMessage(failed));
  });

  const withGoogle = run(async () => {
    const { error: failed } = await authClient.signIn.social({
      provider: "google",
      callbackURL: redirectTo,
      newUserCallbackURL: redirectTo,
      errorCallbackURL: "/login",
    });
    // On success the browser is already on its way to Google.
    if (failed) fail(oauthErrorMessage(failed.code ?? "failed") ?? "Google sign-in didn't work.");
  });

  const resend = async () => {
    if (cooldown > 0 || busy.current || !email) return;
    busy.current = true;
    setError(null);
    setPending(true);
    try {
      if (await sendCode(codeFor === "reset" ? "forget-password" : "sign-in")) setInfo(`New code sent to ${email.trim()}.`);
    } catch {
      fail("Something went wrong. Please try again.");
    } finally {
      busy.current = false;
      setPending(false);
    }
  };

  /** Where "back" goes from each step; none from the address, or once an account exists. */
  const previous: Step | null =
    step === "password" || step === "name" || step === "twoFactor"
      ? "email"
      : step === "code"
        ? codeFor === "signUp"
          ? "name"
          : "password"
        : step === "newPassword"
          ? "code"
          : null;
  const goBack = () => {
    if (previous && !busy.current && !leaving) go(previous, { back: true });
  };

  const showCaptcha = captcha.needed && turnstileSiteKey !== null && GUARDED.has(step);

  /** Under every field: what went wrong, what just happened, or a hint. One live region each. */
  const status = (hint?: string) => (
    <div className="mt-6 flex min-h-[2.75rem] w-full max-w-md flex-col items-center gap-3 text-center">
      {showCaptcha && (
        <div className="w-full animate-in fade-in-0 duration-500">
          <Turnstile
            key={captcha.round}
            siteKey={turnstileSiteKey}
            onToken={(token) => setCaptcha((current) => ({ ...current, token }))}
          />
        </div>
      )}
      <div aria-live="polite" className="w-full">
        {info && !error && (
          <p role="status" className="text-sm text-slate-500 dark:text-white/70">
            {info}
          </p>
        )}
      </div>
      {error ? (
        <p role="alert" className="text-sm font-medium text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : (
        !info && hint && <p className="text-sm text-slate-400 dark:text-white/30">{hint}</p>
      )}
    </div>
  );

  const field = (props: {
    id: string;
    value: string;
    onChange: (value: string) => void;
    type?: string;
    placeholder: string;
    autoComplete: string;
    ariaLabel?: string;
    inputMode?: "email" | "numeric" | "text";
    maxLength?: number;
    mono?: boolean;
    withEye?: boolean;
    describedBy?: string;
  }) => (
    <div className={cn("relative mx-auto w-full max-w-2xl", shaking && "animate-auth-shake")}>
      <input
        ref={input}
        id={props.id}
        type={props.withEye ? (showPassword ? "text" : "password") : (props.type ?? "text")}
        value={props.value}
        onChange={(event) => props.onChange(event.target.value)}
        // Enter always submits, even while the button is still hidden, so a half-typed field
        // says what is missing instead of doing nothing.
        onKeyDown={(event) => {
          if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
          event.preventDefault();
          event.currentTarget.form?.requestSubmit();
        }}
        placeholder={props.placeholder}
        autoComplete={props.autoComplete}
        aria-label={props.ariaLabel}
        aria-describedby={props.describedBy}
        aria-invalid={error ? true : undefined}
        inputMode={props.inputMode}
        maxLength={props.maxLength}
        autoCapitalize="none"
        spellCheck={false}
        disabled={pending}
        className={cn(
          bigInput,
          props.mono && "font-mono font-medium tracking-[0.2em] sm:tracking-[0.4em]",
          props.withEye && "px-12",
          shaking && "text-red-500 dark:text-red-400",
        )}
      />
      {props.withEye && (
        <button
          type="button"
          onClick={() => setShowPassword((shown) => !shown)}
          aria-label={showPassword ? "Hide password" : "Show password"}
          aria-pressed={showPassword}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-2 text-slate-400 transition-colors hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 sm:right-6 dark:text-white/20 dark:hover:text-white/60"
        >
          {showPassword ? <EyeOff aria-hidden className="h-5 w-5" /> : <Eye aria-hidden className="h-5 w-5" />}
        </button>
      )}
    </div>
  );

  const cta = (text: string, shown: boolean) => (
    <button type="submit" disabled={pending || !shown} className={primaryPill}>
      {text}
      <Arrow pending={pending} />
    </button>
  );

  let panel: ReactNode;
  let title: string;
  switch (step) {
    case "email": {
      title = tab === "signIn" ? "Sign in" : "Create your account";
      const typed = email.length > 0;
      panel = (
        <form noValidate onSubmit={submitEmail} className="flex w-full flex-col items-center">
          <h1 className="sr-only">{title}</h1>
          <div
            role="tablist"
            aria-label="Sign in or sign up"
            className="mb-6 flex rounded-full border border-slate-900/10 bg-slate-900/5 p-1 backdrop-blur-md dark:border-white/10 dark:bg-white/5"
          >
            {(["signIn", "signUp"] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={tab === value}
                onClick={() => {
                  setTab(value);
                  setError(null);
                  input.current?.focus();
                }}
                className={cn(
                  "rounded-full px-5 py-2 text-sm font-medium transition-all duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60",
                  tab === value
                    ? "bg-white text-slate-900 shadow-sm dark:bg-white dark:text-black"
                    : "text-slate-600 hover:text-slate-900 dark:text-white/70 dark:hover:text-white",
                )}
              >
                {value === "signIn" ? "Sign In" : "Sign Up"}
              </button>
            ))}
          </div>
          <label htmlFor="auth-email" className={cn(label, "mb-6")}>
            {tab === "signIn" ? "Enter your email to sign in" : "Enter your email to join"}
          </label>
          {field({
            id: "auth-email",
            value: email,
            onChange: setEmail,
            type: "email",
            placeholder: "hello@example.com",
            autoComplete: "email",
            inputMode: "email",
          })}
          <div className="relative mt-12 flex h-16 w-full justify-center">
            <Swap shown={typed}>{cta("Continue with Email", typed)}</Swap>
            {googleEnabled && (
              <Swap shown={!typed}>
                <button
                  type="button"
                  onClick={() => void withGoogle()}
                  disabled={pending}
                  className={cn(
                    "group flex items-center gap-3 rounded-full border border-slate-900/10 bg-slate-900/5 px-6 py-3 font-medium text-slate-700 transition-all duration-300 hover:scale-105 hover:border-slate-900/20 hover:bg-slate-900/10 hover:text-slate-900 active:scale-95 disabled:opacity-50 disabled:hover:scale-100",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60",
                    "dark:border-white/10 dark:bg-white/5 dark:text-white/80 dark:hover:border-white/30 dark:hover:bg-white/10 dark:hover:text-white",
                    EASE,
                  )}
                >
                  {pending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <GoogleMark />}
                  {tab === "signIn" ? "Sign in with Google" : "Sign up with Google"}
                </button>
              </Swap>
            )}
          </div>
          {status()}
        </form>
      );
      break;
    }
    case "password": {
      title = "Welcome back";
      const typed = password.length > 0;
      panel = (
        <form noValidate onSubmit={submitSignIn} className="flex w-full flex-col items-center">
          <div className="mb-6 flex items-center gap-3 text-slate-500 dark:text-white/70">
            <Lock aria-hidden className="h-5 w-5" />
            <h1 className={label}>{title}</h1>
          </div>
          {field({
            id: "auth-password",
            value: password,
            onChange: setPassword,
            placeholder: "Enter password",
            autoComplete: "current-password",
            ariaLabel: "Password",
            maxLength: PASSWORD_MAX,
            withEye: true,
          })}
          <div className="relative mt-12 flex h-16 w-full items-center justify-center">
            <Swap shown={typed}>{cta("Sign In", typed)}</Swap>
            <Swap shown={!typed} className="flex-row gap-4">
              <button type="button" onClick={() => void forgotPassword()} disabled={pending} className={quietLink}>
                Forgot password?
              </button>
              <span aria-hidden className="text-slate-300 dark:text-white/10">
                |
              </span>
              <button type="button" onClick={() => void emailMeACode()} disabled={pending} className={quietLink}>
                Email me a code
              </button>
            </Swap>
          </div>
          {status()}
        </form>
      );
      break;
    }
    case "name": {
      title = "New account";
      const typed = name.trim().length > 0;
      panel = (
        <form noValidate onSubmit={submitName} className="flex w-full flex-col items-center">
          <h1 className={cn(label, "mb-6")}>{title}</h1>
          {field({
            id: "auth-name",
            value: name,
            onChange: setName,
            placeholder: "Your full name",
            autoComplete: "name",
            ariaLabel: "Full name",
            maxLength: DISPLAY_NAME_MAX,
          })}
          <div className="relative mt-12 flex h-16 w-full justify-center">
            <Swap shown={typed}>{cta("Continue", typed)}</Swap>
          </div>
          {status()}
        </form>
      );
      break;
    }
    case "code": {
      title = codeFor === "reset" ? "Password reset" : codeFor === "signIn" ? "Secure sign-in" : "Verify email";
      const complete = code.length === CODE_LENGTH;
      panel = (
        <form noValidate onSubmit={submitCode} className="flex w-full flex-col items-center">
          <div className="mb-6 flex flex-col items-center text-center">
            <h1 className={cn(label, "mb-2")}>{title}</h1>
            <p id="auth-code-for" className="text-sm text-slate-500 dark:text-white/70">
              Enter the {CODE_LENGTH}-digit code sent to <br />
              <b className="text-slate-700 dark:text-white/80">{email.trim()}</b>
            </p>
          </div>
          {field({
            id: "auth-code",
            value: code,
            onChange: (value) => setCode(value.replace(/\D/g, "").slice(0, CODE_LENGTH)),
            placeholder: "0".repeat(CODE_LENGTH),
            autoComplete: "one-time-code",
            ariaLabel: "Code",
            inputMode: "numeric",
            maxLength: CODE_LENGTH,
            mono: true,
            describedBy: "auth-code-for",
          })}
          <div className="relative mt-8 flex h-14 w-full justify-center">
            <Swap shown={complete}>{cta(codeFor === "reset" ? "Continue" : "Verify Code", complete)}</Swap>
            <Swap shown={!complete}>
              <button type="button" onClick={() => void resend()} disabled={cooldown > 0 || pending} className={quietLink}>
                {cooldown > 0 ? `Resend code in ${cooldown}s` : "Didnâ€™t receive it? Resend"}
              </button>
            </Swap>
          </div>
          {status("The code expires in 15 minutes.")}
        </form>
      );
      break;
    }
    case "createPassword": {
      title = "Email verified";
      const enough = password.length >= PASSWORD_MIN;
      panel = (
        <form noValidate onSubmit={submitFirstPassword} className="flex w-full flex-col items-center">
          <div className="mb-6 flex flex-col items-center text-center">
            <ShieldCheck aria-hidden className="mb-2 h-5 w-5 text-green-600 dark:text-green-400" />
            <h1 className={cn(label, "mb-1")}>{title}</h1>
            <p className="text-sm text-slate-500 dark:text-white/70">Secure your new account with a password.</p>
          </div>
          {field({
            id: "auth-new-account-password",
            value: password,
            onChange: setPassword,
            placeholder: "Create password",
            autoComplete: "new-password",
            ariaLabel: "New password",
            maxLength: PASSWORD_MAX,
            withEye: true,
          })}
          <div className="relative mt-12 flex h-16 w-full justify-center">
            <Swap shown={enough}>{cta("Complete Sign Up", enough)}</Swap>
            <Swap shown={!enough}>
              <button type="button" onClick={signedIn} disabled={pending} className={quietLink}>
                Skip for now
              </button>
            </Swap>
          </div>
          {status(`${PASSWORD_MIN}â€“${PASSWORD_MAX} characters.`)}
        </form>
      );
      break;
    }
    case "newPassword": {
      title = "Create new password";
      const enough = password.length >= PASSWORD_MIN;
      panel = (
        <form noValidate onSubmit={submitReset} className="flex w-full flex-col items-center">
          <div className="mb-6 flex items-center gap-3 text-slate-500 dark:text-white/70">
            <Lock aria-hidden className="h-5 w-5" />
            <h1 className={label}>{title}</h1>
          </div>
          {field({
            id: "auth-reset-password",
            value: password,
            onChange: setPassword,
            placeholder: "New password",
            autoComplete: "new-password",
            ariaLabel: "New password",
            maxLength: PASSWORD_MAX,
            withEye: true,
          })}
          <div className="relative mt-12 flex h-16 w-full justify-center">
            <Swap shown={enough}>{cta("Save & Sign In", enough)}</Swap>
          </div>
          {status(`${PASSWORD_MIN}â€“${PASSWORD_MAX} characters. Every device will be signed out.`)}
        </form>
      );
      break;
    }
    case "twoFactor": {
      title = "Two-factor authentication";
      const ready = recovery ? code.trim().length >= 6 : code.length === 6;
      panel = (
        <form noValidate onSubmit={submitSecondStep} className="flex w-full flex-col items-center">
          <div className="mb-6 flex flex-col items-center text-center">
            <ShieldCheck aria-hidden className="mb-3 h-5 w-5 text-slate-400 dark:text-white/70" />
            <h1 className={cn(label, "mb-2")}>{title}</h1>
            <p id="auth-second-step" className="text-sm text-slate-500 dark:text-white/70">
              {recovery ? "Enter one of your recovery codes." : "Enter the 6-digit code from your authenticator app."}
            </p>
          </div>
          {field({
            id: recovery ? "auth-recovery" : "auth-totp",
            value: code,
            onChange: (value) => setCode(recovery ? value.trim().slice(0, 20) : value.replace(/\D/g, "").slice(0, 6)),
            placeholder: recovery ? "A1B2-C3D4" : "000000",
            autoComplete: recovery ? "off" : "one-time-code",
            ariaLabel: recovery ? "Recovery code" : "Authenticator code",
            inputMode: recovery ? "text" : "numeric",
            maxLength: recovery ? 20 : 6,
            mono: !recovery,
            describedBy: "auth-second-step",
          })}
          <div className="relative mt-8 flex h-14 w-full justify-center">
            <Swap shown={ready}>{cta("Authenticate", ready)}</Swap>
            <Swap shown={!ready}>
              <button
                type="button"
                onClick={() => {
                  setRecovery((using) => !using);
                  setCode("");
                  setError(null);
                  input.current?.focus();
                }}
                disabled={pending}
                className={quietLink}
              >
                {recovery ? "Use authenticator app" : "Use a recovery code instead"}
              </button>
            </Swap>
          </div>
          {status()}
        </form>
      );
      break;
    }
  }

  const showBadge = step !== "email" && email.trim() !== "" && !leaving;

  return (
    // The ground is the theme's own (pure white; the app's near-black), opaque over the page's wash.
    <div className="relative isolate flex min-h-dvh w-full flex-col bg-background text-slate-900 transition-colors duration-700 dark:text-white">
      {/* Dark only: two slow pools of light. The white theme stays pure white. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 hidden overflow-hidden dark:block">
        <div className="animate-auth-drift absolute left-[-10%] top-[-20%] h-[60%] w-[60%] rounded-full bg-blue-600/10 blur-[120px]" />
        <div className="animate-auth-drift absolute bottom-[-20%] right-[-10%] h-[50%] w-[50%] rounded-full bg-purple-600/10 blur-[120px] [animation-delay:-10s]" />
      </div>

      <header className="relative z-30 flex w-full items-center justify-between p-6 sm:p-8">
        <div className="flex items-center gap-4">
          <button
            type="button"
            onClick={goBack}
            disabled={!previous || pending}
            inert={!previous || undefined}
            aria-label="Go back"
            className={cn(
              "rounded-full p-2 text-slate-900 transition-all duration-300 hover:bg-slate-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 dark:text-white/90 dark:hover:bg-white/10",
              previous ? "translate-x-0 opacity-100" : "pointer-events-none -translate-x-4 opacity-0",
            )}
          >
            <ChevronLeft aria-hidden className="h-5 w-5" strokeWidth={2.5} />
          </button>
          <div
            className={cn(
              "flex items-center gap-3 text-slate-900 transition-transform duration-300 dark:text-white/90",
              EASE,
              previous ? "translate-x-0" : "-translate-x-12",
            )}
          >
            <BrandMark size={24} className="text-current" />
            <span className="hidden text-base font-semibold tracking-wide min-[480px]:block">NetSec AI</span>
          </div>
        </div>
        <ThemeMenu className="border-transparent bg-transparent text-slate-500 hover:bg-slate-900/5 hover:text-slate-900 dark:text-white/70 dark:hover:bg-white/10 dark:hover:text-white" />
      </header>

      <main id="main" tabIndex={-1} className="relative z-10 flex w-full flex-1 flex-col items-center justify-center px-6 pb-28 pt-14 outline-none sm:pb-20">
        {/* Always in the page, so each new step's name is announced when it arrives. */}
        <p className="sr-only" aria-live="polite">
          {available && !leaving ? title : ""}
        </p>
        {/* The address the later steps are for: tap it to change it. */}
        <div className="pointer-events-none absolute inset-x-0 top-2 flex justify-center">
          <button
            type="button"
            onClick={() => go("email", { back: true })}
            disabled={pending || !showBadge}
            inert={!showBadge || undefined}
            // Named only while shown, so the hidden badge never answers to "email" next to the field.
            aria-label={showBadge ? `Change email address (${email.trim()})` : undefined}
            className={cn(
              "flex max-w-[calc(100vw-3rem)] items-center gap-2 rounded-full border border-slate-900/10 bg-slate-900/5 px-4 py-2 backdrop-blur-md transition-all duration-500 hover:bg-slate-900/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10",
              EASE,
              showBadge ? "pointer-events-auto translate-y-0 scale-100 opacity-100" : "-translate-y-8 scale-95 opacity-0",
            )}
          >
            <User aria-hidden className="h-4 w-4 shrink-0 text-slate-500 dark:text-white/70" />
            <span className="truncate text-sm font-medium text-slate-700 dark:text-white/80">{email.trim()}</span>
          </button>
        </div>

        {/* The step is centred in the page's flow, so at any size or zoom it is pushed down (the
            page scrolls) rather than drawn over the header, the badge or the hint. */}
        <div className="relative flex w-full max-w-4xl justify-center">
          {available ? (
            <div
              key={step}
              inert={leaving || undefined}
              className={cn(
                "flex w-full justify-center transition-[opacity,transform] duration-500",
                EASE,
                leaving
                  ? cn("scale-95 opacity-0", back ? "translate-y-12" : "-translate-y-12")
                  : cn("animate-in fade-in-0 zoom-in-95 duration-500", back ? "slide-in-from-top-12" : "slide-in-from-bottom-12"),
              )}
            >
              {panel}
            </div>
          ) : (
            <div role="alert" className="flex max-w-md flex-col items-center gap-3 text-center">
              <h1 className={label}>Sign-in unavailable here</h1>
              <p className="text-sm text-slate-600 dark:text-white/60">
                This address isn&apos;t the one NetSec AI signs in at right now. Please use the link you were given, or
                try again in a little while.
              </p>
            </div>
          )}
        </div>
      </main>

      {/* A hint, not content: in the flow under the step, and only where there is room for it. */}
      <footer className="pointer-events-none hidden w-full items-center justify-center p-6 min-[480px]:[@media(min-height:700px)]:flex">
        <p className="text-xs font-medium uppercase tracking-wider text-slate-600 dark:text-white/70">
          Press <kbd className="mx-1 rounded-md bg-slate-200 px-1.5 py-0.5 font-sans text-slate-700 dark:bg-white/10 dark:text-white/70">Enter</kbd> to continue
        </p>
      </footer>
    </div>
  );
}
