import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { GitBranch, RefreshCcw, PackageX, AlertOctagon } from "lucide-react";

import { ArchGraph } from "@/components/arch-graph";
import { Panel, PanelHeader, ScoreBar, StatCell, Tag, severityTone } from "@/components/ui-kit";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { layoutNodes, toArchGraphEdges } from "@/lib/graph-layout";
import { useArchitecture } from "@/lib/queries/architecture";
import type { CircularGroup } from "@/lib/queries/dependencies";
import { useDependencies } from "@/lib/queries/dependencies";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/dependencies")({
  head: () => ({
    meta: [
      { title: "Dependency Analysis — Substrate" },
      {
        name: "description",
        content:
          "Dependency graph, circular dependencies, coupling hotspots, orphaned modules and outdated external packages.",
      },
      { property: "og:title", content: "Dependency Analysis — Substrate" },
      {
        property: "og:description",
        content: "Understand internal coupling and external package risk in one view.",
      },
    ],
  }),
  component: Dependencies,
});

// A non-functional visual placeholder, carried forward as-is — same
// treatment `app.architecture.tsx` gives its own "by service/by module"
// toggle (Phase 6.6): these categories don't map cleanly onto the real
// `ModuleKind` set the backend actually classifies (`frontend`/`gateway`/
// `service`/`data`/`cache`/`external`), so force-wiring them to
// `ArchGraph`'s real `filterKinds` prop would misrepresent what's being
// filtered rather than add a real capability.
const filters = ["Frontend", "Backend", "Services", "Libraries", "External APIs"];

/** Renders one `CircularGroup` per the plan's own rule: a 2-file cycle as
 * `a ↔ b`, a larger SCC as `"{n}-way cycle: ..."`. A 1-file self-loop
 * (`analysis/graph.py`'s smallest possible real cycle — a file that,
 * however indirectly, imports itself) isn't explicitly covered by the
 * plan's two named cases, so it gets its own honest third phrasing rather
 * than being forced into either. */
function describeCycle(cycle: CircularGroup): string {
  if (cycle.files.length === 1) return `${cycle.files[0]} imports itself`;
  if (cycle.files.length === 2) return cycle.files.join(" ↔ ");
  return `${cycle.files.length}-way cycle: ${cycle.files.join(", ")}`;
}

function Dependencies() {
  const [active, setActive] = useState<string[]>([]);
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const dependencies = useDependencies(activeRepositoryId);
  const architecture = useArchitecture(activeRepositoryId);

  const data = dependencies.data;
  const archData = architecture.data;
  const positionedNodes = archData ? layoutNodes(archData.nodes) : [];
  const archGraphEdges = archData ? toArchGraphEdges(archData.edges) : [];

  const subtitle = !data?.analyzed
    ? "—"
    : `${archData?.edges.length ?? 0} architecture edge${archData?.edges.length === 1 ? "" : "s"} · ` +
      `${data.coupled.length} coupled module${data.coupled.length === 1 ? "" : "s"} · ` +
      `${data.external.length} outdated package${data.external.length === 1 ? "" : "s"} flagged`;

  const maxCoupledScore = data ? Math.max(1, ...data.coupled.map((m) => m.score)) : 1;
  const topCycle = data?.circular[0];
  const topCoupled = data?.coupled[0];

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Dependency Analysis</h1>
          <p className="text-[12px] text-muted-foreground">{subtitle}</p>
        </div>
        <div className="ml-auto flex flex-wrap gap-1.5">
          {filters.map((f) => {
            const on = active.includes(f);
            return (
              <button
                key={f}
                onClick={() => setActive((a) => (on ? a.filter((x) => x !== f) : [...a, f]))}
                className={cn(
                  "focus-ring rounded-md border px-2.5 py-1.5 text-[12px]",
                  on
                    ? "border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] "
                    : "border-border text-muted-foreground hover:border-border-strong",
                )}
              >
                {f}
              </button>
            );
          })}
        </div>
      </div>

      {repositories.isLoading ? (
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
      ) : dependencies.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading dependencies…</p>
        </Panel>
      ) : dependencies.isError || !data ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-danger">Failed to load dependency data.</p>
        </Panel>
      ) : !data.analyzed ? (
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
        <>
          {/* RISK SUMMARY */}
          <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
            <div className="bg-surface">
              <StatCell
                label="Circular dependencies"
                value={data.circular.length}
                tone={data.circular.length > 0 ? "danger" : "success"}
                sub={topCycle ? describeCycle(topCycle) : "no cycles detected"}
              />
            </div>
            <div className="bg-surface">
              <StatCell
                label="Coupled modules"
                value={data.coupled.length}
                sub={
                  topCoupled
                    ? `top: ${topCoupled.module} (score ${topCoupled.score})`
                    : "no internal edges"
                }
              />
            </div>
            <div className="bg-surface">
              <StatCell
                label="Orphan files"
                value={data.orphans.length}
                tone={data.orphans.length > 0 ? "warning" : "success"}
                sub="no internal imports either direction"
              />
            </div>
            <div className="bg-surface">
              <StatCell
                label="Outdated packages"
                value={data.external.length}
                tone={data.external.length > 0 ? "warning" : "success"}
                sub={
                  data.external.length > 0
                    ? `${data.external.reduce((sum, e) => sum + e.used_by, 0)} usage site(s)`
                    : "none flagged"
                }
              />
            </div>
          </div>

          <div className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
            <Panel className="overflow-hidden">
              <PanelHeader
                title="Dependency graph"
                subtitle="risk edges are dashed"
                icon={<GitBranch className="size-3.5" />}
              />
              <div className="p-3">
                {architecture.isLoading ? (
                  <p className="flex h-[330px] items-center justify-center text-[12px] text-subtle">
                    Loading architecture…
                  </p>
                ) : archData?.analyzed && positionedNodes.length > 0 ? (
                  <ArchGraph
                    nodes={positionedNodes}
                    edges={archGraphEdges}
                    className="h-[330px]"
                    compact
                  />
                ) : (
                  <p className="flex h-[330px] items-center justify-center text-[12px] text-subtle">
                    Architecture snapshot unavailable.
                  </p>
                )}
              </div>
            </Panel>

            <div className="space-y-3">
              <Panel>
                <PanelHeader
                  title="Circular dependencies"
                  icon={<RefreshCcw className="size-3.5" />}
                />
                {data.circular.length ? (
                  <ul className="divide-y divide-border">
                    {data.circular.map((c, i) => (
                      <li key={i} className="flex items-start gap-2 px-4 py-3">
                        <span className="min-w-0 flex-1 break-words font-mono text-[11.5px]">
                          {describeCycle(c)}
                        </span>
                        <Tag tone={severityTone(c.severity)} className="shrink-0">
                          {c.severity}
                        </Tag>
                        <Tag mono className="shrink-0">
                          {c.edge_count} edges
                        </Tag>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="p-4 text-[12px] text-subtle">No circular dependencies detected.</p>
                )}
              </Panel>

              <Panel>
                <PanelHeader
                  title="Highly coupled modules"
                  subtitle="fan-in + fan-out, relative to this repo's most-coupled module"
                />
                {data.coupled.length ? (
                  <>
                    {/* The backend returns the full ranking, not a fixed
                        top-N (per the plan's own "leaderboard" intent) —
                        real repos can have more entries than fit this
                        column gracefully (HireSight has 18), so this panel
                        shows the top 8 by score (already sorted score-desc
                        by the backend) with an explicit "+N more" count
                        rather than silently truncating. */}
                    <ul className="space-y-3 p-4">
                      {data.coupled.slice(0, 8).map((m) => (
                        <li key={m.module}>
                          <div className="mb-1.5 flex items-center gap-2 text-[12px]">
                            <span className="min-w-0 truncate font-mono">{m.module}</span>
                            <span className="shrink-0 font-mono text-[11px] text-subtle">
                              fan-in {m.fan_in} · fan-out {m.fan_out}
                            </span>
                            <span className="ml-auto shrink-0 font-mono text-[11.5px] text-violet-soft">
                              {m.score}
                            </span>
                          </div>
                          <ScoreBar value={(m.score / maxCoupledScore) * 100} />
                        </li>
                      ))}
                    </ul>
                    {data.coupled.length > 8 ? (
                      <p className="px-4 pb-3 text-[11px] text-subtle">
                        +{data.coupled.length - 8} more coupled module
                        {data.coupled.length - 8 === 1 ? "" : "s"}
                      </p>
                    ) : null}
                  </>
                ) : (
                  <p className="p-4 text-[12px] text-subtle">No internal coupling detected.</p>
                )}
              </Panel>

              <Panel>
                <PanelHeader
                  title="Orphan components"
                  subtitle="no internal imports either direction"
                  icon={<PackageX className="size-3.5" />}
                />
                {data.orphans.length ? (
                  <ul className="divide-y divide-border">
                    {data.orphans.map((o) => (
                      <li
                        key={o}
                        className="break-all px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground"
                      >
                        {o}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="p-4 text-[12px] text-subtle">No orphan files detected.</p>
                )}
              </Panel>
            </div>
          </div>

          <Panel>
            <PanelHeader
              title="External dependencies"
              subtitle={
                data.external.length
                  ? `${data.external.length} outdated package${data.external.length === 1 ? "" : "s"} flagged`
                  : "no outdated packages flagged"
              }
              icon={<AlertOctagon className="size-3.5" />}
            />
            {data.external.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border">
                      {["Package", "Finding", "Used by", "Severity"].map((h) => (
                        <th key={h} className="label-xs px-4 py-2 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {data.external.map((d) => (
                      <tr key={d.name} className="hover:bg-surface-2/60">
                        <td className="px-4 py-2.5 font-mono text-[12px]">{d.name}</td>
                        <td className="max-w-[420px] px-4 py-2.5 text-[11.5px] text-muted-foreground">
                          {d.description}
                        </td>
                        <td className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">
                          {d.used_by} file{d.used_by === 1 ? "" : "s"}
                        </td>
                        <td className="px-4 py-2.5">
                          <Tag tone={severityTone(d.severity)}>{d.severity}</Tag>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="p-4 text-[12px] text-subtle">
                No outdated packages flagged by the current heuristic (a small hand-maintained table
                of well-known packages, not a full registry/CVE scan).
              </p>
            )}
          </Panel>

          <p className="rounded-md border border-dashed border-border bg-surface-2/30 p-3 text-center text-[11.5px] text-subtle">
            AI-generated insights will appear here once the assistant is wired up.
          </p>
        </>
      )}
    </div>
  );
}
