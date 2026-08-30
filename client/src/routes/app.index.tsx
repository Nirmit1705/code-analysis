import { createFileRoute, Link } from "@tanstack/react-router";
import { RefreshCw, Sparkles, GitBranch, ArrowUpRight, ChevronsUpDown } from "lucide-react";

import { ArchGraph } from "@/components/arch-graph";
import { AiNote, Panel, PanelHeader, Ring, Sparkline, StatCell, Tag } from "@/components/ui-kit";
import { aiInsights, healthMetrics, recentCommits, repo } from "@/lib/repo-data";

export const Route = createFileRoute("/app/")({
  head: () => ({
    meta: [
      { title: "Project Overview — Substrate" },
      { name: "description", content: "Project health, architecture snapshot, recent changes and AI insights for the analyzed repository." },
      { property: "og:title", content: "Project Overview — Substrate" },
      { property: "og:description", content: "Architecture health, code quality, dependency health and technical debt at a glance." },
    ],
  }),
  component: Overview,
});

const trends: Record<string, number[]> = {
  architecture: [71, 74, 73, 78, 79, 81, 82],
  quality: [80, 79, 78, 78, 77, 78, 76],
  dependency: [78, 76, 74, 70, 69, 70, 64],
  docs: [22, 24, 27, 30, 33, 38, 41],
  debt: [58, 61, 60, 65, 68, 70, 72],
};

function Overview() {
  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="flex items-center gap-2 font-mono text-[15px] font-medium">
            {repo.name}
            <Tag tone="warning">private</Tag>
          </h1>
          <p className="mt-1 flex items-center gap-2 text-[12px] text-subtle">
            <span className="flex items-center gap-1 font-mono">
              <GitBranch className="size-3" /> {repo.branch} · a3f91c2
            </span>
            · last analyzed {repo.lastAnalyzed}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button className="focus-ring flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] hover:border-border-strong">
            <ChevronsUpDown className="size-3.5 text-subtle" /> {repo.branch}
          </button>
          <button className="focus-ring flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] hover:border-border-strong">
            <RefreshCw className="size-3.5 text-subtle" /> Re-analyze
          </button>
          <Link
            to="/app/assistant"
            className="focus-ring flex items-center gap-1.5 rounded-md border border-violet/45 bg-violet/20 px-2.5 py-1.5 text-[12px] font-medium hover:bg-violet/28"
          >
            <Sparkles className="size-3.5 text-violet-soft" /> Ask AI
          </Link>
        </div>
      </header>

      {/* HEALTH */}
      <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-3 xl:grid-cols-5">
        {healthMetrics.map((m) => (
          <div key={m.key} className="bg-surface p-3.5">
            <div className="flex items-start justify-between gap-2">
              <p className="label-xs">{m.label}</p>
              <span
                className={`font-mono text-[11px] ${m.delta >= 0 ? "text-success" : "text-danger"}`}
              >
                {m.delta >= 0 ? "+" : ""}
                {m.delta}
              </span>
            </div>
            <div className="mt-2.5 flex items-center justify-between gap-2">
              <Ring value={m.value} tone={m.tone} size={48} />
              <Sparkline points={trends[m.key] ?? [1, 2]} tone={m.tone} width={72} height={26} />
            </div>
            <p className="mt-2 font-mono text-[11px] text-subtle">{m.note}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-3 xl:grid-cols-[1.55fr_1fr]">
        {/* ARCH SNAPSHOT */}
        <Panel className="overflow-hidden">
          <PanelHeader
            title="Architecture snapshot"
            subtitle="9 components · 14 inferred edges · 2 risk paths"
            actions={
              <Link
                to="/app/architecture"
                className="focus-ring flex items-center gap-1 rounded-sm px-1.5 py-1 text-[11.5px] text-violet-soft hover:bg-violet/12"
              >
                Open explorer <ArrowUpRight className="size-3" />
              </Link>
            }
          />
          <div className="p-3">
            <ArchGraph compact className="h-[300px]" />
            <div className="mt-2 flex flex-wrap items-center gap-3 px-1 text-[11px] text-subtle">
              {[
                ["Frontend", "var(--info)"],
                ["Gateway / Services", "var(--violet)"],
                ["Data", "var(--success)"],
                ["Cache", "var(--cyan)"],
                ["External", "var(--magenta)"],
                ["Risk edge", "var(--danger)"],
              ].map(([label, color]) => (
                <span key={label} className="flex items-center gap-1.5">
                  <span className="size-1.5 rounded-full" style={{ background: color }} />
                  {label}
                </span>
              ))}
            </div>
          </div>
        </Panel>

        {/* AI INSIGHTS + STATS */}
        <div className="space-y-3">
          <Panel>
            <PanelHeader title="AI insights" subtitle="generated from the current analysis" icon={<Sparkles className="size-3.5" />} />
            <div className="space-y-2 p-3">
              {aiInsights.map((i) => (
                <div key={i.title} className="rounded-md border border-border bg-surface-2/40 p-3">
                  <p className="flex items-center gap-2 text-[12.5px] font-medium">
                    <span
                      className="size-1.5 rounded-full"
                      style={{
                        background:
                          i.tone === "danger" ? "var(--danger)" : i.tone === "warning" ? "var(--warning)" : "var(--info)",
                      }}
                    />
                    {i.title}
                  </p>
                  <p className="mt-1.5 text-[12px] leading-relaxed text-muted-foreground">{i.body}</p>
                </div>
              ))}
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Repository statistics" />
            <div className="grid grid-cols-3 divide-x divide-y divide-border">
              <StatCell label="Files" value={repo.stats.files.toLocaleString()} />
              <StatCell label="Lines of code" value="412.9k" />
              <StatCell label="Modules" value={repo.stats.modules} />
              <StatCell label="APIs" value={repo.stats.apis} />
              <StatCell label="Dependencies" value={repo.stats.dependencies} />
              <StatCell label="Contributors" value={repo.stats.contributors} />
            </div>
            <div className="border-t border-border p-3">
              <p className="label-xs mb-2">Language distribution</p>
              <div className="flex h-1.5 overflow-hidden rounded-full">
                {repo.languages.map((l) => (
                  <span key={l.name} style={{ width: `${l.pct}%`, background: l.color }} />
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-3">
                {repo.languages.map((l) => (
                  <span key={l.name} className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    <span className="size-1.5 rounded-full" style={{ background: l.color }} />
                    {l.name} <span className="font-mono text-subtle">{l.pct}%</span>
                  </span>
                ))}
              </div>
            </div>
          </Panel>
        </div>
      </div>

      {/* RECENT CHANGES */}
      <Panel>
        <PanelHeader
          title="Recent changes"
          subtitle="architecture impact per commit"
          actions={
            <Link to="/app/compare" className="focus-ring rounded-sm px-1.5 py-1 text-[11.5px] text-violet-soft hover:bg-violet/12">
              Compare branches
            </Link>
          }
        />
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-border">
              {["Commit", "Message", "Author", "Changed components", "Impact", "Date"].map((h) => (
                <th key={h} className="label-xs px-4 py-2 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {recentCommits.map((c) => (
              <tr key={c.sha} className="transition-colors hover:bg-surface-2/60">
                <td className="px-4 py-2.5 font-mono text-[12px] text-violet-soft">{c.sha}</td>
                <td className="max-w-[340px] truncate px-4 py-2.5 text-[12.5px]">{c.message}</td>
                <td className="px-4 py-2.5 font-mono text-[12px] text-muted-foreground">{c.author}</td>
                <td className="px-4 py-2.5">
                  <span className="flex flex-wrap gap-1">
                    {c.components.map((m) => (
                      <Tag key={m}>{m}</Tag>
                    ))}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  <Tag tone={c.impact === "high" ? "danger" : c.impact === "medium" ? "warning" : "success"}>
                    {c.impact}
                  </Tag>
                </td>
                <td className="px-4 py-2.5 font-mono text-[11.5px] text-subtle">{c.date}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <AiNote title="Suggested next step">
        Dependency health dropped 6 points this week — <span className="mono text-violet-soft">axios@0.27.2</span> and{" "}
        <span className="mono text-violet-soft">jsonwebtoken@8.5.1</span> both carry known CVEs and are imported by 13
        modules. Run impact analysis on <span className="mono text-violet-soft">jwt.strategy.ts</span> before upgrading.
      </AiNote>
    </div>
  );
}
