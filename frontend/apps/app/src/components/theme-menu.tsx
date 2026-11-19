"use client";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@v1/ui/dropdown-menu";
import { cn } from "@v1/ui/utils";

import { THEME_CHOICES, THEME_LABELS, ThemeIcon, useThemeChoice } from "./theme-switcher";

/**
 * The app header's compact theme control, opening Dark / White / System with a check on the
 * the active choice. The page stays in the accessibility tree while the menu is open.
 * `className` restyles the button (the sign-in screens use a borderless one).
 */
export function ThemeMenu({ className }: { className?: string } = {}) {
  const { shown, choose } = useThemeChoice();
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="Colour theme"
          title="Colour theme"
          className={cn(
            "icon-button",
            className,
          )}
        >
          {/* No icon until mounted: the server can't know the theme, and a wrong one would flash. */}
          {shown ? <ThemeIcon theme={shown} className="h-5 w-5" /> : <span className="h-5 w-5" />}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={6} collisionPadding={8} className="min-w-[9.5rem] p-1">
        <DropdownMenuRadioGroup value={shown} onValueChange={choose}>
          {THEME_CHOICES.map((theme) => (
            <DropdownMenuRadioItem key={theme} value={theme} className="gap-2.5 font-medium">
              <ThemeIcon theme={theme} className="h-4 w-4 text-text-secondary" />
              {THEME_LABELS[theme]}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
