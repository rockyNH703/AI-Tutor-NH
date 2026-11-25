import { Bug, GraduationCap, Network } from "lucide-react";

import { BrandLockup } from "./brand/brand-mark";

/**
 * The sign-in screens' brand panel, beside the form from 1024px: Networkers Home's console ink
 * (the `.rail` token island, like the app's own rail), so signing in already looks like the app it
 * opens. It says what the product does in three plain lines, all things the app really does, and
 * nothing interactive lives here: the form beside it is the whole task.
 */
export function AuthBrandPanel() {
  return (
    <aside
      aria-label="About NetSec AI"
      className="rail relative hidden w-[40%] max-w-[36rem] shrink-0 flex-col justify-between overflow-hidden p-10 lg:flex xl:p-14"
    >
      <div
        aria-hidden
        className="nh-grid pointer-events-none absolute inset-0"
      />
      <BrandLockup className="relative" />
      <div className="relative max-w-sm">
        <p className="text-balance font-display text-4xl font-black leading-[1.1] tracking-[-0.02em] text-foreground">
          Built for <span className="text-signal">network</span> and security
          engineers.
        </p>
        <ul className="mt-8 space-y-4 text-sm text-text-secondary">
          <li className="flex items-start gap-3">
            <Bug aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
            Troubleshoot configs, logs and outages, with secrets masked before
            the model sees them.
          </li>
          <li className="flex items-start gap-3">
            <GraduationCap
              aria-hidden
              className="mt-0.5 h-4 w-4 shrink-0 text-brand"
            />
            Practise for CCNA, CCNP and CCIE, and see where your skills stand.
          </li>
          <li className="flex items-start gap-3">
            <Network
              aria-hidden
              className="mt-0.5 h-4 w-4 shrink-0 text-brand"
            />
            Work through your Networkers Home program, month by month.
          </li>
        </ul>
      </div>
      <div className="relative">
        <div aria-hidden className="signal-line mb-4 w-24" />
        <p className="text-xs text-text-secondary">
          Part of Networkers Home, Bangalore.
        </p>
      </div>
    </aside>
  );
}
