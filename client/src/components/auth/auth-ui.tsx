import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

// Shared by the sign-in, sign-up and setup screens. Sizes are written the way
// the rest of the app writes them (`text-[12.5px]` displays as 15px, see the
// type-scale table at the bottom of styles.css).

const buttonBase =
  "focus-ring inline-flex items-center justify-center gap-2.5 rounded-xl border font-semibold transition-[background-color,border-color,color,transform] duration-150 active:translate-y-px disabled:pointer-events-none disabled:opacity-60 motion-reduce:transition-none";

export const buttonSize = "h-[46px] px-5 text-[12.5px]";
export const buttonSizeLg = "h-12 px-6 text-[13.5px]";

export const buttonPrimary = cn(
  buttonBase,
  "border-transparent bg-primary text-primary-foreground hover:bg-violet-soft",
);
export const buttonSecondary = cn(
  buttonBase,
  "border-border-strong bg-surface-2 text-foreground hover:bg-surface-3",
);
export const buttonGhost = cn(
  buttonBase,
  "border-transparent text-muted-foreground hover:text-foreground",
);

export const inputClass =
  "w-full rounded-xl border border-border-strong bg-surface text-foreground outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-subtle focus-visible:border-primary focus-visible:ring-[3px] focus-visible:ring-primary/25 motion-reduce:transition-none";

/** A hairline rule with a short label in the middle ("or sign in with email"). */
export function AuthDivider({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-3.5 text-[11px] text-muted-foreground">
      <span className="h-px flex-1 bg-border" />
      <span>{children}</span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
