import { Link } from "@tanstack/react-router";
import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { useMe } from "@/lib/queries/auth";
import { cn } from "@/lib/utils";

export const ONBOARDING_STEPS = [
  { key: "connect", label: "Connect GitHub", hint: "Give CodeLens AI read access" },
  { key: "repository", label: "Choose repository", hint: "Pick what to understand" },
  { key: "branch", label: "Choose branch", hint: "Pick the branch to track" },
  { key: "analysis", label: "Analyze", hint: "Building your codebase map" },
  { key: "ready", label: "Ready", hint: "Open your workspace" },
] as const;

const LAST = ONBOARDING_STEPS.length - 1;

function Wordmark({ className }: { className?: string }) {
  return (
    <Link to="/" className="focus-ring w-fit rounded-md">
      <img
        src="/codelens-wordmark-on-dark.svg"
        alt="CodeLens AI"
        width={175}
        height={30}
        className={className}
      />
    </Link>
  );
}

function StepMarker({ index, step }: { index: number; step: number }) {
  const state = index < step ? "done" : index === step ? "current" : "pending";
  // Arriving at the last step means everything is finished, so it gets the check too.
  const showCheck = state === "done" || (state === "current" && index === LAST);
  return (
    <span
      className={cn(
        "grid size-7 place-items-center rounded-full border font-mono text-[10.5px] transition-[background-color,border-color,color] duration-300 motion-reduce:transition-none",
        state === "current" && "border-primary bg-primary text-primary-foreground",
        state === "done" && "border-primary text-primary",
        state === "pending" && "border-border-strong text-muted-foreground/70",
      )}
    >
      {showCheck ? (
        <Check
          strokeWidth={3}
          className="size-3.5 animate-in duration-200 zoom-in-50 motion-reduce:animate-none"
        />
      ) : (
        index + 1
      )}
    </span>
  );
}

/** The setup layout: a vertical progress rail on large screens (a thin bar and
 * step count on phones) beside the current step. Changing `step` re-keys the
 * content so each step eases in; the rail's connector line fills as steps finish. */
export function OnboardingShell({ step, children }: { step: number; children: ReactNode }) {
  const me = useMe();
  const current = ONBOARDING_STEPS[step];

  return (
    <div className="min-h-screen bg-background font-auth text-foreground lg:flex">
      <aside className="hidden w-[380px] shrink-0 flex-col border-r border-border bg-surface px-12 pb-10 pt-11 lg:sticky lg:top-0 lg:flex lg:h-screen">
        <Wordmark className="h-[30px] w-auto" />

        <nav aria-label="Setup progress" className="mt-[72px] flex-1">
          <ol>
            {ONBOARDING_STEPS.map((s, i) => (
              <li key={s.key} className="flex gap-4" aria-current={i === step ? "step" : undefined}>
                <div className="flex flex-col items-center">
                  <StepMarker index={i} step={step} />
                  {i < LAST ? (
                    <span className="relative min-h-[34px] w-0.5 flex-1 bg-border">
                      <span
                        className={cn(
                          "absolute inset-0 origin-top bg-primary transition-transform duration-500 ease-out motion-reduce:transition-none",
                          i < step ? "scale-y-100 delay-150" : "scale-y-0",
                        )}
                      />
                    </span>
                  ) : null}
                </div>
                <div className={cn(i < LAST && "pb-[26px]")}>
                  <div
                    className={cn(
                      "text-[13.5px] leading-7 transition-colors duration-300 motion-reduce:transition-none",
                      i === step
                        ? "font-semibold text-foreground"
                        : i < step
                          ? "text-muted-foreground"
                          : "text-muted-foreground/70",
                    )}
                  >
                    {s.label}
                  </div>
                  {i === step ? (
                    <div className="mt-0.5 text-[11px] text-muted-foreground">{s.hint}</div>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        </nav>

        <div className="flex flex-col gap-3.5 text-[11px] text-muted-foreground">
          {me.data?.email ? <span className="truncate">Signed in as {me.data.email}</span> : null}
          {step < 3 ? (
            <Link
              to="/app"
              className="focus-ring w-fit rounded-sm transition-colors hover:text-foreground hover:underline"
            >
              Skip setup for now
            </Link>
          ) : null}
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="border-b border-border px-6 pb-4 pt-5 sm:px-12 lg:hidden">
          <div className="flex items-center justify-between">
            <Wordmark className="h-[26px] w-auto" />
            {step < 3 ? (
              <Link to="/app" className="text-[11px] text-muted-foreground hover:text-foreground">
                Skip setup
              </Link>
            ) : null}
          </div>
          <div className="mt-4 flex items-baseline justify-between text-[11px]">
            <span className="font-semibold">{current?.label}</span>
            <span className="text-muted-foreground">
              Step {step + 1} of {ONBOARDING_STEPS.length}
            </span>
          </div>
          <div className="mt-2 h-0.5 overflow-hidden rounded-full bg-border">
            <div
              className="h-full bg-primary transition-[width] duration-500 ease-out motion-reduce:transition-none"
              style={{ width: `${((step + 1) / ONBOARDING_STEPS.length) * 100}%` }}
            />
          </div>
        </header>

        <main className="px-6 pb-14 pt-9 sm:px-12 lg:px-24 lg:pb-12 lg:pt-24">
          <div
            key={step}
            className="animate-in duration-300 fade-in slide-in-from-bottom-2 motion-reduce:animate-none"
          >
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
