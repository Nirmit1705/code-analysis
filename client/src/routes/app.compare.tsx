import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { formatDistanceToNow } from "date-fns";
import {
  GitBranch,
  ChevronDown,
  ArrowLeftRight,
  Plus,
  Minus,
  Plug,
  Waypoints,
  History,
  CheckCircle2,
  AlertTriangle,
  ArrowUp,
  ArrowDown,
  Minus as DashIcon,
} from "lucide-react";

import { AiNote, Panel, PanelHeader, Tag, toneColor, type Tone } from "@/components/ui-kit";
import { ApiError } from "@/lib/api";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { useCompare, type CompareRead, type DependencyRisk } from "@/lib/queries/compare";
import { completedRuns, useAnalysisRuns, type AnalysisRunRead } from "@/lib/queries/history";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/compare")({
  head: () => ({
    meta: [
      { title: "Compare — Substrate" },
      {
        name: "description",
        content:
          "Compare two analyzed commits architecturally: added and removed files, API surface changes, and dependency-edge risk.",
      },
      { property: "og:title", content: "Compare — Substrate" },
      { property: "og:description", content: "An architectural diff, not a line diff." },
    ],
  }),
  component: Compare,
});

function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

function riskTone(risk: DependencyRisk): Tone {
  return risk === "high" ? "danger" : risk === "medium" ? "warning" : "success";
}

/** Preserves the overall newest-first order (from `useAnalysisRuns`) within
 * each branch group, and orders the groups themselves by each group's own
 * newest run — so the branch with the most recent activity (almost always
 * the one a user wants to diff) sorts first, matching-first-seen order. */
function groupByBranch(runs: AnalysisRunRead[]): Array<[string, AnalysisRunRead[]]> {
  const map = new Map<string, AnalysisRunRead[]>();
  for (const run of runs) {
    const group = map.get(run.branch);
    if (group) group.push(run);
    else map.set(run.branch, [run]);
  }
  return Array.from(map.entries());
}

function EmptyPanel({
  icon,
  title,
  description,
  action,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <Panel>
      <div className="flex flex-col items-center gap-2.5 p-10 text-center">
        <span className="grid size-11 place-items-center rounded-xl bg-surface-2 text-subtle">
          {icon}
        </span>
        <h3 className="text-[14.5px] font-semibold">{title}</h3>
        <p className="max-w-md text-[12px] leading-relaxed text-muted-foreground">{description}</p>
        {action}
      </div>
    </Panel>
  );
}

function RunPickerDropdown({
  runs,
  selectedId,
  onSelect,
}: {
  runs: AnalysisRunRead[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div
      className="absolute top-[calc(100%+6px)] left-0 z-30 max-h-72 w-full min-w-[280px] overflow-y-auto rounded-lg border border-border-strong bg-popover p-1 shadow-[var(--shadow-pop)]"
      onMouseDown={(e) => e.stopPropagation()}
    >
      {groupByBranch(runs).map(([branch, branchRuns]) => (
        <div key={branch}>
          <p className="label-xs px-2 py-1.5">{branch}</p>
          {branchRuns.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => onSelect(r.id)}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-sm px-2 py-1.5 text-left hover:bg-surface-2",
                r.id === selectedId && "bg-violet/10",
              )}
            >
              <span className="font-mono text-[11.5px] font-medium">{shortSha(r.commit_sha)}</span>
              <span className="ml-auto shrink-0 text-[10.5px] whitespace-nowrap text-subtle">
                {formatDistanceToNow(new Date(r.completed_at ?? r.created_at), { addSuffix: true })}
              </span>
              <Tag tone={r.debt_score >= 50 ? "danger" : r.debt_score >= 25 ? "warning" : "success"} mono>
                {r.debt_score.toFixed(1)}
              </Tag>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

function RunPicker({
  label,
  runs,
  selectedId,
  onSelect,
  isOpen,
  onToggle,
}: {
  label: string;
  runs: AnalysisRunRead[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const selected = runs.find((r) => r.id === selectedId) ?? null;

  return (
    <div className="relative min-w-[230px] flex-1" onMouseDown={(e) => e.stopPropagation()}>
      <p className="label-xs mb-1.5">{label}</p>
      <button
        type="button"
        onClick={onToggle}
        className="focus-ring flex w-full items-center gap-2 rounded-md border border-border bg-surface-2 px-2.5 py-2 text-left transition-colors hover:border-border-strong hover:bg-surface-3"
      >
        <GitBranch className="size-3.5 shrink-0 text-subtle" />
        <span className="truncate font-mono text-[12.5px] font-semibold">
          {selected?.branch ?? "Select a run"}
        </span>
        {selected ? (
          <span className="shrink-0 rounded-sm bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-subtle">
            {shortSha(selected.commit_sha)}
          </span>
        ) : null}
        <span className="ml-auto shrink-0 text-[11px] text-subtle">
          {selected
            ? formatDistanceToNow(new Date(selected.completed_at ?? selected.created_at), {
                addSuffix: true,
              })
            : ""}
        </span>
        <ChevronDown
          className={cn("size-3.5 shrink-0 text-subtle transition-transform", isOpen && "rotate-180")}
        />
      </button>
      {isOpen ? <RunPickerDropdown runs={runs} selectedId={selectedId} onSelect={onSelect} /> : null}
    </div>
  );
}

function DeltaTile({
  label,
  delta,
  before,
  after,
  isDebt,
}: {
  label: string;
  delta: number;
  before: number;
  after: number;
  isDebt?: boolean;
}) {
  const color = isDebt
    ? delta > 0
      ? "text-warning"
      : delta < 0
        ? "text-success"
        : "text-foreground"
    : "text-foreground";
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
  const magnitude = isDebt ? Math.abs(delta).toFixed(1) : Math.abs(delta).toString();
  const trendIcon =
    delta > 0 ? (
      <ArrowUp className={cn("size-3", color)} />
    ) : delta < 0 ? (
      <ArrowDown className={cn("size-3", color)} />
    ) : (
      <DashIcon className="size-3 text-subtle" />
    );
  const sub = isDebt
    ? `${before.toFixed(1)} → ${after.toFixed(1)}${delta > 0 ? " · worsened" : delta < 0 ? " · improved" : " · unchanged"}`
    : `${before} → ${after}`;

  return (
    <div className="bg-surface px-4 py-3.5">
      <p className="label-xs">{label}</p>
      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span className={cn("font-mono text-[17px] font-semibold", color)}>
          {sign}
          {magnitude}
        </span>
        {trendIcon}
      </div>
      <p className="mt-1 text-[11px] text-subtle">{sub}</p>
    </div>
  );
}

function CompareResult({
  data,
  runs,
}: {
  data: CompareRead;
  runs: AnalysisRunRead[];
}) {
  const baseRun = runs.find((r) => r.id === data.base_run_id);
  const headRun = runs.find((r) => r.id === data.head_run_id);
  const hasChanges =
    data.added_files.length > 0 ||
    data.removed_files.length > 0 ||
    data.changed_dependencies.length > 0 ||
    data.added_apis.length > 0 ||
    data.removed_apis.length > 0;

  return (
    <div className="space-y-3">
      {baseRun && headRun ? (
        <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-5">
          <DeltaTile
            label="Files"
            delta={data.file_count_delta}
            before={baseRun.file_count}
            after={headRun.file_count}
          />
          <DeltaTile
            label="Modules"
            delta={data.module_count_delta}
            before={baseRun.module_count}
            after={headRun.module_count}
          />
          <DeltaTile
            label="APIs"
            delta={data.api_count_delta}
            before={baseRun.api_count}
            after={headRun.api_count}
          />
          <DeltaTile
            label="Dependencies"
            delta={data.dependency_count_delta}
            before={baseRun.dependency_count}
            after={headRun.dependency_count}
          />
          <DeltaTile
            label="Debt score"
            delta={data.debt_score_delta}
            before={baseRun.debt_score}
            after={headRun.debt_score}
            isDebt
          />
        </div>
      ) : null}

      {!hasChanges ? (
        <EmptyPanel
          icon={<CheckCircle2 className="size-5 text-success" />}
          title="These two runs are identical"
          description={`No file, API or dependency changes between ${data.head_branch}@${shortSha(data.head_commit_sha)} and ${data.base_branch}@${shortSha(data.base_commit_sha)}. Pick a different pair of runs above to see a real diff.`}
        />
      ) : (
        <>
          <div className="grid gap-3 lg:grid-cols-2">
            <Panel>
              <PanelHeader
                title="Added"
                subtitle={`present in ${data.head_branch}@${shortSha(data.head_commit_sha)}`}
                icon={<Plus className="size-3.5" />}
                actions={
                  <span className="font-mono text-[11px] text-subtle">
                    {data.added_files.length} file{data.added_files.length === 1 ? "" : "s"}
                  </span>
                }
              />
              {data.added_files.length ? (
                <ul className="divide-y divide-border">
                  {data.added_files.map((f) => (
                    <li key={f.path} className="flex items-start gap-2.5 px-4 py-2.5">
                      <Plus className="mt-0.5 size-3.5 shrink-0 text-success" />
                      <div className="min-w-0">
                        <p className="mono text-[11.5px] break-all">{f.path}</p>
                        <p className="mt-0.5 text-[11px] text-muted-foreground">{f.note}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="p-4 text-[12px] text-subtle">No files added.</p>
              )}
            </Panel>

            <Panel>
              <PanelHeader
                title="Removed"
                subtitle={`only in ${data.base_branch}@${shortSha(data.base_commit_sha)}`}
                icon={<Minus className="size-3.5" />}
                actions={
                  <span className="font-mono text-[11px] text-subtle">
                    {data.removed_files.length} file{data.removed_files.length === 1 ? "" : "s"}
                  </span>
                }
              />
              {data.removed_files.length ? (
                <ul className="divide-y divide-border">
                  {data.removed_files.map((f) => (
                    <li key={f.path} className="flex items-start gap-2.5 px-4 py-2.5">
                      <Minus className="mt-0.5 size-3.5 shrink-0 text-danger" />
                      <div className="min-w-0">
                        <p className="mono text-[11.5px] break-all">{f.path}</p>
                        <p className="mt-0.5 text-[11px] text-muted-foreground">{f.note}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="p-4 text-[12px] text-subtle">No files removed.</p>
              )}
            </Panel>
          </div>

          <Panel>
            <PanelHeader
              title="API surface"
              subtitle="endpoints added or removed between the two runs"
              icon={<Plug className="size-3.5" />}
            />
            <div className="space-y-4 p-4">
              <div>
                <p className="label-xs mb-2">Added &middot; {data.added_apis.length}</p>
                {data.added_apis.length ? (
                  <div className="flex flex-wrap gap-2">
                    {data.added_apis.map((a) => (
                      <span
                        key={a}
                        className="inline-flex items-center gap-1.5 rounded-md border border-success/25 bg-success/[0.08] px-2.5 py-1 font-mono text-[11px] text-success"
                      >
                        <Plus className="size-3" />
                        {a}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-[11.5px] text-subtle">None.</p>
                )}
              </div>
              <div>
                <p className="label-xs mb-2">Removed &middot; {data.removed_apis.length}</p>
                {data.removed_apis.length ? (
                  <div className="flex flex-wrap gap-2">
                    {data.removed_apis.map((a) => (
                      <span
                        key={a}
                        className="inline-flex items-center gap-1.5 rounded-md border border-danger/25 bg-danger/[0.08] px-2.5 py-1 font-mono text-[11px] text-danger"
                      >
                        <Minus className="size-3" />
                        {a}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-[11.5px] text-subtle">None.</p>
                )}
              </div>
            </div>
          </Panel>

          <Panel>
            <PanelHeader
              title="Dependency changes"
              subtitle="edges added or removed &middot; risk from the head run's own coupling &amp; cycle graph"
              icon={<Waypoints className="size-3.5" />}
              actions={
                <span className="font-mono text-[11px] text-subtle">
                  {data.changed_dependencies.length} edge{data.changed_dependencies.length === 1 ? "" : "s"}
                </span>
              }
            />
            {data.changed_dependencies.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="w-[3px] p-0" />
                      {["Edge", "Change", "Risk"].map((h) => (
                        <th key={h} className="label-xs px-4 py-2 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.changed_dependencies.map((d, i) => (
                      <tr key={i} className="border-t border-border">
                        <td className="w-[3px] p-0" style={{ background: toneColor(riskTone(d.risk)) }} />
                        <td className="px-4 py-2.5 font-mono text-[11.5px] whitespace-nowrap">{d.edge}</td>
                        <td className="px-4 py-2.5 text-[11.5px] text-muted-foreground">{d.change}</td>
                        <td className="px-4 py-2.5">
                          <Tag tone={riskTone(d.risk)}>{d.risk}</Tag>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="p-4 text-[12px] text-subtle">No dependency edges changed.</p>
            )}
          </Panel>
        </>
      )}

      <AiNote>{data.explanation ?? "No AI explanation available for this comparison."}</AiNote>

      <p className="rounded-md border border-dashed border-border bg-surface-2/30 p-3 text-center text-[11.5px] text-subtle">
        Diffed from two already-completed analysis runs &mdash; not a live git patch. Dependency risk
        reuses the same coupling and cycle detection the Architecture and Dependencies screens already
        show.
      </p>
    </div>
  );
}

function Compare() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const runsQuery = useAnalysisRuns(activeRepositoryId);
  const runs = completedRuns(runsQuery.data);

  const [baseRunId, setBaseRunId] = useState<string | null>(null);
  const [headRunId, setHeadRunId] = useState<string | null>(null);
  const [openPicker, setOpenPicker] = useState<"base" | "head" | null>(null);

  // Default selection once the run history loads: head = most recently
  // completed run, base = the one immediately before it — "compare against
  // the previous run" is the overwhelmingly common case, but either picker
  // can still be changed freely afterward (design decision #3 doesn't
  // restrict Compare to adjacent runs).
  useEffect(() => {
    if (runs.length >= 2 && baseRunId === null && headRunId === null) {
      setHeadRunId(runs[0]!.id);
      setBaseRunId(runs[1]!.id);
    }
  }, [runs, baseRunId, headRunId]);

  useEffect(() => {
    if (!openPicker) return;
    const close = () => setOpenPicker(null);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [openPicker]);

  const compare = useCompare(activeRepositoryId, baseRunId, headRunId);

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="panel ambient-panel flex flex-wrap items-end justify-between gap-4 px-5 py-4">
        <div>
          <p className="label-xs mb-1.5">Change Intelligence</p>
          <h1 className="text-[19px] font-semibold tracking-tight text-foreground">Compare</h1>
          <p className="mt-1 max-w-xl text-[12.5px] text-muted-foreground">
            Architectural diff between two analyzed commits &mdash; added and removed files, API
            surface changes, and dependency-edge risk.
          </p>
        </div>
      </div>

      {repositories.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading&hellip;</p>
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
      ) : runsQuery.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading analysis history&hellip;</p>
        </Panel>
      ) : runsQuery.isError ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-danger">Failed to load analysis history.</p>
        </Panel>
      ) : runs.length < 2 ? (
        <EmptyPanel
          icon={<History className="size-5" />}
          title="Not enough analysis history yet"
          description="Compare needs two completed analysis runs on this repository. Once a new commit has been analyzed, you'll be able to see exactly what changed."
          action={
            <Link
              to="/app/history"
              className="focus-ring mt-1 inline-flex items-center gap-1.5 rounded-md border border-primary bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] hover:bg-[oklch(0.745_0.19_44)]"
            >
              <History className="size-3.5" /> Go to History
            </Link>
          }
        />
      ) : (
        <>
          <Panel ambient>
            <div className="flex flex-wrap items-center gap-2.5 p-3.5">
              <RunPicker
                label="Base"
                runs={runs}
                selectedId={baseRunId}
                onSelect={(id) => {
                  setBaseRunId(id);
                  setOpenPicker(null);
                }}
                isOpen={openPicker === "base"}
                onToggle={() => setOpenPicker((p) => (p === "base" ? null : "base"))}
              />
              <button
                type="button"
                title="Swap base and head"
                onClick={() => {
                  const b = baseRunId;
                  setBaseRunId(headRunId);
                  setHeadRunId(b);
                }}
                className="focus-ring mb-[1px] grid size-9 shrink-0 place-items-center self-end rounded-md border border-border bg-surface-2 text-muted-foreground transition-colors hover:border-violet/45 hover:text-violet-soft"
              >
                <ArrowLeftRight className="size-3.5" />
              </button>
              <RunPicker
                label="Head"
                runs={runs}
                selectedId={headRunId}
                onSelect={(id) => {
                  setHeadRunId(id);
                  setOpenPicker(null);
                }}
                isOpen={openPicker === "head"}
                onToggle={() => setOpenPicker((p) => (p === "head" ? null : "head"))}
              />
            </div>
          </Panel>

          {compare.isLoading ? (
            <div className="space-y-3">
              <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-5">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="h-[74px] animate-pulse bg-surface-2" />
                ))}
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
                <div className="h-56 animate-pulse rounded-lg bg-surface-2" />
                <div className="h-56 animate-pulse rounded-lg bg-surface-2" />
              </div>
            </div>
          ) : compare.isError ? (
            <EmptyPanel
              icon={<AlertTriangle className="size-5 text-danger" />}
              title="Couldn't load this comparison"
              description={
                compare.error instanceof ApiError
                  ? compare.error.message
                  : "Something went wrong comparing these two runs — please try again."
              }
            />
          ) : compare.data ? (
            <CompareResult data={compare.data} runs={runs} />
          ) : null}
        </>
      )}
    </div>
  );
}
