import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { User, Users, CreditCard, KeyRound, Bell } from "lucide-react";

import { Panel, PanelHeader, ScoreBar, Tag } from "@/components/ui-kit";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/settings")({
  head: () => ({
    meta: [
      { title: "Profile & Settings — CodeLens AI" },
      { name: "description", content: "Manage your profile, team members, notification rules, API tokens and workspace plan usage." },
      { property: "og:title", content: "Profile & Settings — CodeLens AI" },
      { property: "og:description", content: "Workspace, team and billing configuration for CodeLens AI." },
    ],
  }),
  component: SettingsPage,
});

const tabs = [
  { id: "profile", label: "Profile", icon: User },
  { id: "team", label: "Team", icon: Users },
  { id: "notifications", label: "Notifications", icon: Bell },
  { id: "tokens", label: "API tokens", icon: KeyRound },
  { id: "billing", label: "Plan & usage", icon: CreditCard },
];

const team = [
  { name: "Maya Okafor", email: "maya@hiresight.io", role: "Owner", last: "active now" },
  { name: "Dmitri Aleksandrov", email: "dmitri@hiresight.io", role: "Admin", last: "2 hours ago" },
  { name: "Sarah Chen", email: "sarah@hiresight.io", role: "Member", last: "yesterday" },
  { name: "Tomás Vieira", email: "tomas@hiresight.io", role: "Member", last: "3 days ago" },
  { name: "Priya Raghavan", email: "priya@hiresight.io", role: "Viewer", last: "1 week ago" },
];

function SettingsPage() {
  const [tab, setTab] = useState("profile");

  return (
    <div className="mx-auto max-w-5xl space-y-3 p-4">
      <div>
        <h1 className="text-[17px] font-semibold tracking-tight">Profile & Settings</h1>
        <p className="text-[12px] text-muted-foreground">Workspace: HireSight Engineering</p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "focus-ring flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[12px]",
              tab === t.id ? "border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)]" : "border-border text-muted-foreground hover:border-border-strong",
            )}
          >
            <t.icon className="size-3.5" /> {t.label}
          </button>
        ))}
      </div>

      {tab === "profile" ? (
        <Panel>
          <PanelHeader title="Your profile" subtitle="signed in with GitHub" />
          <div className="grid gap-4 p-4 sm:grid-cols-2">
            <Field label="Full name" value="Maya Okafor" />
            <Field label="Email" value="maya@hiresight.io" />
            <Field label="GitHub handle" value="m-okafor" mono />
            <Field label="Role" value="Owner" />
          </div>
        </Panel>
      ) : null}

      {tab === "team" ? (
        <Panel>
          <PanelHeader title="Team members" subtitle="5 of 10 seats used" actions={<Tag tone="violet">Invite</Tag>} />
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border">
                {["Member", "Email", "Role", "Last active"].map((h) => (
                  <th key={h} className="label-xs px-4 py-2 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {team.map((m) => (
                <tr key={m.email} className="hover:bg-surface-2/60">
                  <td className="px-4 py-2.5 text-[12.5px]">{m.name}</td>
                  <td className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">{m.email}</td>
                  <td className="px-4 py-2.5"><Tag tone={m.role === "Owner" ? "violet" : "neutral"}>{m.role}</Tag></td>
                  <td className="px-4 py-2.5 text-[11.5px] text-subtle">{m.last}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : null}

      {tab === "notifications" ? (
        <Panel>
          <PanelHeader title="Notification rules" subtitle="delivered to Slack #eng-health and email" />
          <ul className="divide-y divide-border">
            {[
              ["Critical debt introduced", "Notify when a merge raises the debt score by 5 or more", true],
              ["New circular dependency", "Notify on any newly closed cycle", true],
              ["CVE detected in dependencies", "Notify on high or critical advisories", true],
              ["Weekly architecture digest", "Monday 09:00 summary of score movement", true],
              ["Analysis completed", "Every successful run on a tracked branch", false],
            ].map(([title, sub, on]) => (
              <li key={title as string} className="flex items-center gap-3 px-4 py-3">
                <div>
                  <p className="text-[12.5px]">{title}</p>
                  <p className="text-[11.5px] text-subtle">{sub}</p>
                </div>
                <Toggle initial={on as boolean} />
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {tab === "tokens" ? (
        <Panel>
          <PanelHeader title="API tokens" subtitle="use these in CI to gate merges on architecture checks" />
          <ul className="divide-y divide-border">
            {[
              ["ci-github-actions", "sbst_live_••••••••7f2a", "created Jul 2, 2026", "used 4 minutes ago"],
              ["local-cli-maya", "sbst_live_••••••••b19c", "created May 18, 2026", "used yesterday"],
            ].map(([name, key, created, used]) => (
              <li key={name} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span className="text-[12.5px]">{name}</span>
                <span className="font-mono text-[11.5px] text-muted-foreground">{key}</span>
                <span className="ml-auto text-[11px] text-subtle">{created} · {used}</span>
                <button className="focus-ring rounded-md border border-danger/30 bg-danger/10 px-2 py-1 text-[11.5px] text-danger">
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {tab === "billing" ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <Panel ambient>
            <PanelHeader title="Current plan" subtitle="Team · billed annually" actions={<Tag tone="violet">active</Tag>} />
            <div className="space-y-3 p-4">
              <p className="font-mono text-[22px]">$49<span className="text-[12px] text-subtle"> / developer / month</span></p>
              <ul className="space-y-1.5 text-[12px] text-muted-foreground">
                {["Unlimited private repositories", "10 seats included", "Repository-aware AI assistant", "CI architecture gates"].map((f) => (
                  <li key={f} className="flex gap-2">
                    <span className="mt-[7px] size-1 shrink-0 rounded-full bg-violet" />
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          </Panel>
          <Panel>
            <PanelHeader title="Usage this cycle" subtitle="resets Sep 1, 2026" />
            <div className="space-y-4 p-4">
              {[
                ["Analyses", 412, 1000],
                ["Assistant messages", 2860, 5000],
                ["Indexed files", 3184, 10000],
              ].map(([label, used, total]) => (
                <div key={label as string}>
                  <div className="mb-1.5 flex items-baseline text-[12px]">
                    <span>{label}</span>
                    <span className="ml-auto font-mono text-[11.5px] text-muted-foreground">
                      {(used as number).toLocaleString()} / {(total as number).toLocaleString()}
                    </span>
                  </div>
                  <ScoreBar value={((used as number) / (total as number)) * 100} />
                </div>
              ))}
            </div>
          </Panel>
        </div>
      ) : null}
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <label className="block">
      <span className="label-xs">{label}</span>
      <input
        defaultValue={value}
        className={cn(
          "focus-ring mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-[12.5px] outline-none",
          mono && "font-mono text-[12px]",
        )}
      />
    </label>
  );
}

function Toggle({ initial }: { initial: boolean }) {
  const [on, setOn] = useState(initial);
  return (
    <button
      onClick={() => setOn((v) => !v)}
      className={cn(
        "focus-ring ml-auto h-5 w-9 shrink-0 rounded-full border transition-colors",
        on ? "border-violet/50 bg-violet/40" : "border-border bg-surface-2",
      )}
    >
      <span
        className={cn(
          "block size-3.5 rounded-full bg-foreground/85 transition-transform",
          on ? "translate-x-[19px]" : "translate-x-[3px]",
        )}
      />
    </button>
  );
}
