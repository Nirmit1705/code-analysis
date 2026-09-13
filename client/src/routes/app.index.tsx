import type { UseQueryResult } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { RefreshCw, Sparkles, GitBranch, ArrowUpRight, ChevronsUpDown } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { ArchGraph } from "@/components/arch-graph";
import { Panel, PanelHeader, Ring, StatCell, Tag } from "@/components/ui-kit";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { layoutNodes, toArchGraphEdges } from "@/lib/graph-layout";
import { useArchitecture, type ArchitectureRead } from "@/lib/queries/architecture";
import { useDashboard, type DashboardRead } from "@/lib/queries/dashboard";
import { useRepositories } from "@/lib/queries/repos";

export const Route = createFileRoute("/app/")({
  head: () => ({
    meta: [
      { title: "Project Overview — Substrate" },
      {
        name: "description",
        content:
          "Project health, architecture snapshot, recent changes and AI insights for the analyzed repository.",
      },
      { property: "og:title", content: "Project Overview — Substrate" },
      {
        property: "og:description",
        content:
          "Architecture health, code quality, dependency health and technical debt at a glance.",
      },
    ],
  }),
  component: Overview,
});

// Backend `languages[]` (Phase 6.3) deliberately has no `color` field —
// display colors are a frontend concern (same stance Architecture's own
// backend takes on layout). Cycled by index; `languages` arrives sorted
// loc-desc, so the largest language always gets the first color.
const LANGUAGE_COLORS = [
  "var(--info)",
  "var(--violet)",
  "var(--cyan)",
  "var(--magenta)",
  "var(--warning)",
];

function Overview() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const activeRepository = repositories.data?.find((r) => r.id === activeRepositoryId) ?? null;
  const dashboard = useDashboard(activeRepositoryId);
  const architecture = useArchitecture(activeRepositoryId);

  const repoDisplayName = repositories.isLoading
    ? "Loading…"
    : (activeRepository?.full_name ?? "No repository connected");

  // `repositories.isLoading` is checked *before* `activeRepositoryId === null`
  // — while the repositories list is still resolving, `activeRepositoryId`
  // is legitimately still `null` too (nothing to fall back to yet), and
  // that's a loading state, not "no repository connected". Also gates
  // `useDashboard` from being read as errored below: a `null` repository id
  // makes that query `enabled: false`, which TanStack Query reports as
  // `isLoading: false, isError: false, data: undefined` — indistinguishable
  // from "loaded successfully with no data" unless this is checked first.
  const body = repositories.isLoading ? (
    <Panel>
      <p className="p-8 text-center text-[13px] text-subtle">Loading…</p>
    </Panel>
  ) : activeRepositoryId === null ? (
    <Panel>
      <div className="p-10 text-center">
        <p className="text-[13px] text-subtle">No repository connected yet.</p>
        <Link
          to="/onboarding"
          className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline"
        >
          Connect a repository
        </Link>
      </div>
    </Panel>
  ) : dashboard.isLoading ? (
    <Panel>
      <p className="p-8 text-center text-[13px] text-subtle">Loading dashboard…</p>
    </Panel>
  ) : dashboard.isError || !dashboard.data ? (
    <Panel>
      <p className="p-8 text-center text-[13px] text-danger">Failed to load the dashboard.</p>
    </Panel>
  ) : !dashboard.data.analyzed ? (
    <Panel>
      <div className="p-10 text-center">
        <p className="text-[13px] text-subtle">This repository hasn't been analyzed yet.</p>
        <Link
          to="/onboarding"
          className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline"
        >
          Run the first analysis
        </Link>
      </div>
    </Panel>
  ) : (
    <DashboardBody data={dashboard.data} architecture={architecture} />
  );

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="flex items-center gap-2 font-mono text-[15px] font-medium">
            {repoDisplayName}
            {activeRepository?.private ? <Tag tone="warning">private</Tag> : null}
          </h1>
          <p className="mt-1 flex items-center gap-2 text-[12px] text-subtle">
            {!dashboard.data ? (
              <span>—</span>
            ) : dashboard.data.analyzed ? (
              <>
                <span className="flex items-center gap-1 font-mono">
                  <GitBranch className="size-3" /> {dashboard.data.branch} ·{" "}
                  {dashboard.data.commit_sha?.slice(0, 7)}
                </span>
                · last analyzed{" "}
                {dashboard.data.completed_at
                  ? formatDistanceToNow(new Date(dashboard.data.completed_at), { addSuffix: true })
                  : "just now"}
              </>
            ) : (
              <span>not yet analyzed</span>
            )}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button className="focus-ring flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] hover:border-border-strong">
            <ChevronsUpDown className="size-3.5 text-subtle" /> {dashboard.data?.branch ?? "branch"}
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

      {body}
    </div>
  );
}

function DashboardBody({
  data,
  architecture,
}: {
  data: DashboardRead;
  architecture: UseQueryResult<ArchitectureRead, Error>;
}) {
  const stats = data.stats!;
  const archData = architecture.data;
  const positionedNodes = archData ? layoutNodes(archData.nodes) : [];
  const archGraphEdges = archData ? toArchGraphEdges(archData.edges) : [];
  const circularCount = archData ? archData.edges.filter((e) => e.circular).length : 0;
  const archSubtitle = architecture.isLoading
    ? "loading…"
    : !archData?.analyzed
      ? "not available"
      : `${archData.nodes.length} components · ${archData.edges.length} inferred edges · ${circularCount} circular ${circularCount === 1 ? "edge" : "edges"}`;

  return (
    <>
      {/* HEALTH */}
      <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-3 xl:grid-cols-5">
        {data.health_metrics.map((m) => (
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
            <div className="mt-2.5 flex items-center gap-2">
              <Ring value={m.value} tone={m.tone} size={48} />
            </div>
            <p className="mt-2 font-mono text-[11px] text-subtle">{m.note}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-3 xl:grid-cols-[1.55fr_1fr]">
        {/* ARCH SNAPSHOT — real data as of Phase 6.6 */}
        <Panel className="overflow-hidden">
          <PanelHeader
            title="Architecture snapshot"
            subtitle={archSubtitle}
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
            {architecture.isLoading ? (
              <p className="flex h-[300px] items-center justify-center text-[12px] text-subtle">
                Loading architecture…
              </p>
            ) : archData?.analyzed && positionedNodes.length > 0 ? (
              <ArchGraph
                nodes={positionedNodes}
                edges={archGraphEdges}
                compact
                className="h-[300px]"
              />
            ) : (
              <p className="flex h-[300px] items-center justify-center text-[12px] text-subtle">
                Architecture snapshot unavailable.
              </p>
            )}
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

        {/* AI INSIGHTS (always empty until Phase 8) + STATS */}
        <div className="space-y-3">
          <Panel>
            <PanelHeader
              title="AI insights"
              subtitle="generated from the current analysis"
              icon={<Sparkles className="size-3.5" />}
            />
            <div className="p-3">
              <p className="rounded-md border border-dashed border-border bg-surface-2/30 p-4 text-center text-[12px] text-subtle">
                AI-generated insights will appear here once the assistant is wired up.
              </p>
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Repository statistics" />
            <div className="grid grid-cols-3 divide-x divide-y divide-border">
              <StatCell label="Files" value={stats.files.toLocaleString()} />
              <StatCell label="Lines of code" value={stats.loc.toLocaleString()} />
              <StatCell label="Modules" value={stats.modules.toLocaleString()} />
              <StatCell label="APIs" value={stats.apis.toLocaleString()} />
              <StatCell label="Dependencies" value={stats.dependencies.toLocaleString()} />
              <StatCell label="Contributors" value={stats.contributors.toLocaleString()} />
            </div>
            <div className="border-t border-border p-3">
              <p className="label-xs mb-2">Language distribution</p>
              {data.languages.length > 0 ? (
                <>
                  <div className="flex h-1.5 overflow-hidden rounded-full">
                    {data.languages.map((l, i) => (
                      <span
                        key={l.name}
                        style={{
                          width: `${l.pct}%`,
                          background: LANGUAGE_COLORS[i % LANGUAGE_COLORS.length],
                        }}
                      />
                    ))}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-3">
                    {data.languages.map((l, i) => (
                      <span
                        key={l.name}
                        className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
                      >
                        <span
                          className="size-1.5 rounded-full"
                          style={{ background: LANGUAGE_COLORS[i % LANGUAGE_COLORS.length] }}
                        />
                        {l.name} <span className="font-mono text-subtle">{l.pct}%</span>
                      </span>
                    ))}
                  </div>
                </>
              ) : (
                <p className="text-[11px] text-subtle">
                  No detected-language files in the latest analysis.
                </p>
              )}
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
            <Link
              to="/app/compare"
              className="focus-ring rounded-sm px-1.5 py-1 text-[11.5px] text-violet-soft hover:bg-violet/12"
            >
              Compare branches
            </Link>
          }
        />
        {data.recent_commits.length > 0 ? (
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border">
                {["Commit", "Message", "Author", "Changed components", "Impact", "Date"].map(
                  (h) => (
                    <th key={h} className="label-xs px-4 py-2 font-medium">
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.recent_commits.map((c) => (
                <tr key={c.sha} className="transition-colors hover:bg-surface-2/60">
                  <td className="px-4 py-2.5 font-mono text-[12px] text-violet-soft">
                    {c.sha.slice(0, 7)}
                  </td>
                  <td className="max-w-[340px] truncate px-4 py-2.5 text-[12.5px]">{c.message}</td>
                  <td className="px-4 py-2.5 font-mono text-[12px] text-muted-foreground">
                    {c.author}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="flex flex-wrap gap-1">
                      {c.components.length > 0 ? (
                        c.components.map((m) => <Tag key={m}>{m}</Tag>)
                      ) : (
                        <span className="text-[11px] text-subtle">—</span>
                      )}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    <Tag
                      tone={
                        c.impact === "high"
                          ? "danger"
                          : c.impact === "medium"
                            ? "warning"
                            : "success"
                      }
                    >
                      {c.impact}
                    </Tag>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-[11.5px] text-subtle">
                    {formatDistanceToNow(new Date(c.date), { addSuffix: true })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="p-4 text-[12px] text-subtle">No recent commits found.</p>
        )}
      </Panel>
    </>
  );
}
