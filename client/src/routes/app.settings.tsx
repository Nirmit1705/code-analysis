import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { LogOut } from "lucide-react";

import { Panel } from "@/components/ui-kit";
import { endSession, forgetSession, useMe } from "@/lib/queries/auth";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/settings")({
  head: () => ({
    meta: [
      { title: "Profile & Settings — CodeLens AI" },
      { name: "description", content: "Your CodeLens AI account: the email you sign in with and the repositories you have connected." },
      { property: "og:title", content: "Profile & Settings — CodeLens AI" },
      { property: "og:description", content: "Your CodeLens AI account." },
    ],
  }),
  component: SettingsPage,
});

const day = (iso: string) => new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

function SettingsPage() {
  const me = useMe();
  const repositories = useRepositories();

  return (
    <div className="mx-auto max-w-[1280px] p-4 pb-16">
      {me.isLoading ? (
        <Message>Loading your account…</Message>
      ) : me.isError || !me.data ? (
        <Message danger>Failed to load your account.</Message>
      ) : (
        <div className="grid max-w-[760px] gap-[18px] pt-3">
          <header className="mb-2 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
            <div className="min-w-0">
              <h1 className="text-[28px] leading-[1.1] font-semibold tracking-[-0.028em]">Your account</h1>
              <p className="mt-2.5 text-[11px] break-words text-muted-foreground">Signed in as {me.data.email}.</p>
            </div>
            <LogOutButton />
          </header>

          <section aria-labelledby="account-title" className="rounded-[14px] border border-border bg-surface px-[22px] pt-5 pb-[22px]">
            <h2 id="account-title" className="text-[14.5px] font-semibold tracking-[-0.01em]">
              Account
            </h2>
            <dl className="mt-3.5 grid">
              <Row label="Email">{me.data.email}</Row>
              <Row label="Member since">{day(me.data.created_at)}</Row>
              <Row label="Repositories">
                {repositories.isLoading ? (
                  <span className="text-subtle">Loading…</span>
                ) : (repositories.data ?? []).length === 0 ? (
                  <span>
                    None yet.{" "}
                    <Link to="/onboarding" className="focus-ring text-violet-soft hover:underline">
                      Connect a repository
                    </Link>
                  </span>
                ) : (
                  <span>
                    {(repositories.data ?? []).map((r, i) => (
                      <span key={r.id}>
                        {i > 0 ? ", " : null}
                        <span className="font-mono text-[11px]">{r.full_name}</span>
                      </span>
                    ))}
                    <small className="mt-0.5 block text-[10px] text-subtle">
                      Each repository's own settings are on the{" "}
                      <Link to="/app/repository" className="focus-ring text-violet-soft hover:underline">
                        Repository Settings
                      </Link>{" "}
                      screen.
                    </small>
                  </span>
                )}
              </Row>
            </dl>
          </section>

          <section aria-labelledby="later-title" className="rounded-[14px] border border-border bg-surface px-[22px] pt-5 pb-[22px]">
            <h2 id="later-title" className="text-[14.5px] font-semibold tracking-[-0.01em]">
              Not in CodeLens AI yet
            </h2>
            <p className="mt-0.5 text-[10px] text-subtle">
              These have no data behind them yet, so there's nothing to set. They'll appear here once they exist.
            </p>
            <div className="mt-3.5 flex flex-wrap gap-1.5">
              {["Team members", "Notifications", "API tokens", "Plan and billing"].map((t) => (
                <span key={t} className="rounded-full border border-dashed border-border-strong px-2.5 py-1 text-[10px] text-muted-foreground">
                  {t}
                </span>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

/** Ends the session on the server, then forgets it here. If the server can't
 * be reached the session is still alive there, so this says so and offers to
 * log out on this device only instead of pretending it worked. */
function LogOutButton() {
  const [state, setState] = useState<"idle" | "pending" | "unreachable">("idle");

  const logOut = async () => {
    setState("pending");
    if (await endSession()) forgetSession();
    else setState("unreachable");
  };

  return (
    <div className="grid justify-items-end gap-2">
      <button
        type="button"
        onClick={() => void logOut()}
        disabled={state === "pending"}
        className="focus-ring flex items-center gap-1.5 rounded-lg border border-border-strong px-4 py-2 text-[11px] font-semibold text-secondary-foreground transition-colors hover:bg-surface-2 disabled:opacity-60"
      >
        <LogOut className="size-3.5" />
        {state === "pending" ? "Logging out…" : "Log out"}
      </button>
      {state === "unreachable" ? (
        <p role="alert" className="max-w-[40ch] text-right text-[10px] leading-relaxed text-danger">
          Couldn't reach CodeLens AI, so your session is still active on the server.{" "}
          <button type="button" onClick={forgetSession} className="focus-ring font-semibold underline">
            Log out on this device only
          </button>
        </p>
      ) : null}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-0.5 border-t border-border py-[11px] text-[12.5px] first:border-t-0 sm:grid-cols-[170px_minmax(0,1fr)] sm:gap-3.5">
      <dt className="text-[11px] text-subtle">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function Message({ children, danger }: { children: React.ReactNode; danger?: boolean }) {
  return (
    <Panel>
      <div className="p-10 text-center">
        <p className={cn("text-[13px]", danger ? "text-danger" : "text-subtle")}>{children}</p>
      </div>
    </Panel>
  );
}
