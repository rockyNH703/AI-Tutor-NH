"use client";

import { Avatar, AvatarFallback, AvatarImage } from "@v1/ui/avatar";
import type { ReactNode } from "react";

import { type FriendZonePlayer, avatarSrc } from "./api";

/** A member's photo when they let you see it (friends only), else their initials. Decorative: the name is beside it. */
export function PlayerAvatar({ player }: { player: Pick<FriendZonePlayer, "initials" | "avatarUrl"> }) {
  const photo = avatarSrc(player.avatarUrl);
  return (
    <Avatar aria-hidden className="h-10 w-10 rounded-full">
      {photo && <AvatarImage src={photo} alt="" loading="lazy" className="rounded-full object-cover" />}
      <AvatarFallback className="rounded-full bg-surface-2 text-sm font-semibold text-foreground">{player.initials}</AvatarFallback>
    </Avatar>
  );
}

/** A member's name, in its own writing direction. */
export function PlayerName({ name, className }: { name: string; className?: string }) {
  return (
    <bdi dir="auto" className={className ?? "font-semibold"}>
      {name}
    </bdi>
  );
}

/** One member in a list: photo or initials, name, a line under it, and the row's actions. */
export function PlayerRow({ player, line, children }: { player: FriendZonePlayer; line?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-3">
      <PlayerAvatar player={player} />
      <span className="min-w-[10rem] flex-1">
        <span className="block break-words text-sm text-foreground">
          <PlayerName name={player.displayName} />
        </span>
        {line && <span className="block text-xs text-text-secondary">{line}</span>}
      </span>
      {children && <span className="flex flex-wrap gap-2">{children}</span>}
    </div>
  );
}

export const LIST = "flex flex-col divide-y divide-border rounded-xl border border-border bg-card";
