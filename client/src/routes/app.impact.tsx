import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { formatDistanceToNow } from "date-fns";
import {
  Target,
  GitBranch,
  ArrowRight,
  Plug,
  CheckCircle2,
  AlertTriangle,
  Flag,
} from "lucide-react";

import { AiNote, Panel, PanelHeader, Ring, type Tone } from "@/components/ui-kit";
import { ApiError } from "@/lib/api";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { useImpact, type ImpactRead, type ImpactRisk } from "@/lib/queries/impact";
import { completedRuns, useAnalysisRuns, type AnalysisRunRead } from "@/lib/queries/history";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/impact")({
  head: () => ({
    meta: [
      { title: "Impact Analysis — CodeLens AI" },
      {
        name: "description",
        content:
          "Pick an analyzed commit and see everything it affects downstream, before you ship it.",
      },
      { property: "og:title", content: "Impact Analysis — CodeLens AI" },
      {
        property: "og:description",
        content: "Direct and indirect blast radius, API impact and risk scoring, computed from the real dependency graph.",
      },
    ],
  }),
  component: Impact,
});

function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

function riskTone(risk: ImpactRisk): Tone {
  return risk === "HIGH" ? "danger" : risk === "MEDIUM" ? "warning" : "success";
}

function formatPoints(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** Built from `ImpactRead.breakdown` (the server's own arithmetic), so it
 * states exactly how the score was reached and never claims a cause the
 * response can't back up. */
function impactCaption(data: ImpactRead): string {
  const b = data.breakdown;
  const testNote =
    b.excluded_test_files > 0
      ? ` ${b.excluded_test_files} test file${b.excluded_test_files === 1 ? "" : "s"} also reached by this change, listed below but not scored.`
      : "";
  if (data.direct.length + data.indirect.length === 0 && data.affected_apis.length === 0) {
    return `No downstream dependents and no affected endpoints.${testNote}`;
  }
  const terms = [`${formatPoints(b.file_points)} from dependents`, `${formatPoints(b.api_points)} from endpoints`];
  if (b.cycle_bonus > 0) terms.push(`${formatPoints(b.cycle_bonus)} for a changed file in a dependency cycle`);
  const kind =
    b.change_kind === "breaking"
      ? "this change removed a file, symbol or endpoint"
      : "no file, symbol or endpoint was removed";
  return `Score ${data.score} = (${terms.join(" + ")}) \u00d7 ${b.multiplier} — ${kind}.${testNote}`;
}

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

function RunRow({
  run,
  isActive,
  onSelect,
}: {
  run: AnalysisRunRead;
  isActive: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "relative flex w-full flex-col gap-1 border-t border-border px-3.5 py-2.5 text-left first:border-t-0 hover:bg-surface-2",
        isActive && "bg-violet/10",
      )}
    >
      {isActive ? <span className="absolute inset-y-0 left-0 w-[2px] bg-violet" /> : null}
      <span className="flex items-center gap-2">
        <span className="font-mono text-[12px] font-semibold">{shortSha(run.commit_sha)}</span>
        <span className="ml-auto shrink-0 text-[10.5px] whitespace-nowrap text-subtle">
          {formatDistanceToNow(new Date(run.completed_at ?? run.created_at), { addSuffix: true })}
        </span>
      </span>
      <span className="flex items-center gap-1.5 font-mono text-[10.5px] text-subtle">
        <GitBranch className="size-3" />
        {run.file_count} files &middot; debt {run.debt_score.toFixed(1)}
      </span>
    </button>
  );
}

function CommitChip({
  label,
  branch,
  sha,
  when,
}: {
  label: string;
  branch: string;
  sha: string;
  when: string | null;
}) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-[10px] tracking-wide text-subtle uppercase">{label}</p>
      <div className="flex items-center gap-1.5">
        <GitBranch className="size-3.5 text-subtle" />
        <span className="font-mono text-[13px] font-semibold">{branch}</span>
        <span className="rounded-sm bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-subtle">
          {shortSha(sha)}
        </span>
      </div>
      {when ? (
        <p className="text-[10.5px] text-subtle">
          {formatDistanceToNow(new Date(when), { addSuffix: true })}
        </p>
      ) : null}
    </div>
  );
}

function ImpactResult({ data, previousRun }: { data: ImpactRead; previousRun: AnalysisRunRead | null }) {
  const clean = data.direct.length === 0 && data.indirect.length === 0 && data.affected_apis.length === 0;

  return (
    <div className="space-y-3">
      <Panel>
        <div className="flex flex-wrap items-center gap-4 p-4">
          {previousRun ? (
            <CommitChip
              label="Predecessor"
              branch={previousRun.branch}
              sha={previousRun.commit_sha}
              when={previousRun.completed_at ?? previousRun.created_at}
            />
          ) : null}
          <ArrowRight className="size-4 shrink-0 text-subtle" />
          <CommitChip label="Analyzing" branch={data.branch} sha={data.commit_sha} when={null} />

          <div className="ml-auto flex items-center gap-3.5 rounded-lg border border-border bg-surface-2 px-4 py-2.5">
            <Ring value={data.score} tone={riskTone(data.risk)} label={String(data.score)} />
            <div>
              <p className="label-xs">Risk</p>
              <p
                className={cn(
                  "mt-0.5 flex items-center gap-1.5 font-mono text-[14px] font-semibold",
                  riskTone(data.risk) === "danger"
                    ? "text-danger"
                    : riskTone(data.risk) === "warning"
                      ? "text-warning"
                      : "text-success",
                )}
              >
                <AlertTriangle className="size-3.5" /> {data.risk}
              </p>
            </div>
          </div>
        </div>
        <p className="border-t border-border px-4 py-2.5 text-[11.5px] leading-relaxed text-muted-foreground">
          {impactCaption(data)}
        </p>
      </Panel>

      {clean ? (
        <EmptyPanel
          icon={<CheckCircle2 className="size-5 text-success" />}
          title="Nothing downstream depends on this change"
          description="The files this commit changed aren't imported anywhere else in the repository yet — safe to merge from a blast-radius standpoint."
        />
      ) : (
        <>
          <div className="grid gap-3 lg:grid-cols-2">
            <Panel>
              <PanelHeader
                title="Directly affected"
                subtitle="1 hop &middot; imports a changed file"
                actions={
                  <span className="font-mono text-[11px] text-subtle">
                    {data.direct.length} file{data.direct.length === 1 ? "" : "s"}
                  </span>
                }
              />
              {data.direct.length ? (
                <ul className="divide-y divide-border">
                  {data.direct.map((f) => (
                    <li key={f.path} className="px-4 py-2.5">
                      <p className="mono text-[11.5px] break-all text-foreground">{f.path}</p>
                      {f.why ? <p className="mt-0.5 text-[11px] text-muted-foreground">{f.why}</p> : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="p-4 text-[12px] text-subtle">No direct dependents.</p>
              )}
            </Panel>

            <Panel>
              <PanelHeader
                title="Indirectly affected"
                subtitle="2+ hops through the dependency graph"
                actions={
                  <span className="font-mono text-[11px] text-subtle">
                    {data.indirect.length} file{data.indirect.length === 1 ? "" : "s"}
                  </span>
                }
              />
              {data.indirect.length ? (
                <ul className="divide-y divide-border">
                  {data.indirect.map((f) => (
                    <li key={f.path} className="flex items-center gap-2 px-4 py-2.5">
                      <p className="mono min-w-0 flex-1 truncate text-[11.5px] text-muted-foreground">
                        {f.path}
                      </p>
                      {f.hops !== null ? (
                        <span className="mono shrink-0 rounded-sm bg-surface-3 px-1.5 py-0.5 text-[10px] text-subtle">
                          {f.hops} hops
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="p-4 text-[12px] text-subtle">No indirect dependents.</p>
              )}
            </Panel>
          </div>

          <Panel>
            <PanelHeader
              title="Affected APIs"
              subtitle="endpoints reachable from the changed files"
              icon={<Plug className="size-3.5" />}
            />
            <div className="flex flex-wrap gap-2 p-4">
              {data.affected_apis.length ? (
                data.affected_apis.map((a) => (
                  <span
                    key={a}
                    className="rounded-md border border-cyan/25 bg-cyan/[0.07] px-2.5 py-1 font-mono text-[11.5px] text-cyan"
                  >
                    {a}
                  </span>
                ))
              ) : (
                <p className="text-[12px] text-subtle">No API endpoints reachable from this change.</p>
              )}
            </div>
          </Panel>
        </>
      )}

      <AiNote>{data.explanation ?? "No AI explanation available for this impact analysis."}</AiNote>

      <p className="rounded-md border border-dashed border-border bg-surface-2/30 p-3 text-center text-[11.5px] text-subtle">
        Direct dependents are one import away from a changed file, indirect are two or more &mdash; the
        same reverse dependency walk the Architecture and Dependencies screens are built on.
      </p>
    </div>
  );
}

function Impact() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const runsQuery = useAnalysisRuns(activeRepositoryId);
  const runs = completedRuns(runsQuery.data);

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  useEffect(() => {
    if (runs.length > 0 && selectedRunId === null) {
      setSelectedRunId(runs[0]!.id);
    }
  }, [runs, selectedRunId]);

  const impact = useImpact(activeRepositoryId, selectedRunId);
  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;
  const impactData = impact.data;
  const previousRun = impactData
    ? (runs.find((r) => r.id === impactData.previous_analysis_run_id) ?? null)
    : null;

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="panel ambient-panel flex flex-wrap items-end justify-between gap-4 px-5 py-4">
        <div>
          <p className="label-xs mb-1.5">Change Intelligence</p>
          <h1 className="text-[19px] font-semibold tracking-tight text-foreground">Impact Analysis</h1>
          <p className="mt-1 max-w-xl text-[12.5px] text-muted-foreground">
            What a change actually breaks downstream &mdash; computed by walking the real dependency
            graph from a commit's changed files. Pick any analyzed commit on the left.
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
      ) : runs.length === 0 ? (
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
        <div className="grid gap-3 xl:grid-cols-[280px_1fr]">
          <Panel className="h-fit">
            <PanelHeader
              title="Recent runs"
              subtitle="select one to compute impact"
              icon={<Target className="size-3.5" />}
            />
            {groupByBranch(runs).map(([branch, branchRuns]) => (
              <div key={branch}>
                <p className="label-xs px-3.5 py-2">{branch}</p>
                {branchRuns.map((run) => (
                  <RunRow
                    key={run.id}
                    run={run}
                    isActive={run.id === selectedRunId}
                    onSelect={() => setSelectedRunId(run.id)}
                  />
                ))}
              </div>
            ))}
          </Panel>

          <div>
            {impact.isLoading ? (
              <div className="space-y-3">
                <div className="h-32 animate-pulse rounded-lg bg-surface-2" />
                <div className="grid gap-3 lg:grid-cols-2">
                  <div className="h-48 animate-pulse rounded-lg bg-surface-2" />
                  <div className="h-48 animate-pulse rounded-lg bg-surface-2" />
                </div>
              </div>
            ) : impact.isError ? (
              impact.error instanceof ApiError && impact.error.status === 409 ? (
                <EmptyPanel
                  icon={<Flag className="size-5" />}
                  title="First analyzed commit on this branch"
                  description={`There's nothing earlier on ${selectedRun?.branch ?? "this branch"} to compare ${
                    selectedRun ? shortSha(selectedRun.commit_sha) : "this commit"
                  } against yet. Impact starts showing from the next analyzed commit onward.`}
                />
              ) : (
                <EmptyPanel
                  icon={<AlertTriangle className="size-5 text-danger" />}
                  title="Couldn't compute impact"
                  description={
                    impact.error instanceof ApiError
                      ? impact.error.message
                      : "Something went wrong computing impact for this commit — please try again."
                  }
                />
              )
            ) : impact.data ? (
              <ImpactResult data={impact.data} previousRun={previousRun} />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
