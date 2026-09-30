import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { useState } from "react";
import { GitPullRequestArrow, History, Loader2, MousePointerClick, RefreshCcw } from "lucide-react";
import { toast } from "sonner";

import { Panel, PanelHeader, Sparkline, StatCell, Tag, type Tone } from "@/components/ui-kit";
import { ApiError } from "@/lib/api";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { useCreateAnalysisRun } from "@/lib/queries/analysis";
import {
  completedRuns,
  isRunInFlight,
  useAnalysisRuns,
  type AnalysisRunRead,
} from "@/lib/queries/history";
import { useRepoBranches, useRepositories } from "@/lib/queries/repos";

export const Route = createFileRoute("/app/history")({
  head: () => ({
    meta: [
      { title: "Analysis History — CodeLens AI" },
      { name: "description", content: "Every analysis run for this repository, with debt scores over time." },
      { property: "og:title", content: "Analysis History — CodeLens AI" },
      { property: "og:description", content: "Track how technical debt moves commit by commit." },
    ],
  }),
  component: HistoryPage,
});

// The endpoint returns the whole history (no server-side pagination — Compare's
// and Impact's pickers need the full list), so only the *table* is paged here;
// stats and the trend chart always use every run.
const PAGE_SIZE = 25;

function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds.toString().padStart(2, "0")}s` : `${seconds}s`;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1]! + sorted[mid]!) / 2 : sorted[mid]!;
}

function statusTag(run: AnalysisRunRead): { tone: Tone; label: string } {
  if (run.status === "completed") return { tone: "success", label: "completed" };
  if (run.status === "failed") return { tone: "danger", label: "failed" };
  return { tone: "violet", label: run.status.replace(/_/g, " ") };
}

function TriggerTag({ source }: { source: AnalysisRunRead["trigger_source"] }) {
  return source === "webhook" ? (
    <Tag tone="info">
      <GitPullRequestArrow className="size-3" /> push
    </Tag>
  ) : (
    <Tag tone="neutral">
      <MousePointerClick className="size-3" /> manual
    </Tag>
  );
}

/** Debt-score series for one branch, oldest → newest, completed runs only —
 * a failed/in-flight run has no debt score of its own, and mixing branches
 * would plot a trend that isn't any one line of history. */
function debtSeries(runs: AnalysisRunRead[], branch: string): AnalysisRunRead[] {
  return runs
    .filter((r) => r.branch === branch)
    .sort(
      (a, b) =>
        new Date(a.completed_at ?? a.created_at).getTime() -
        new Date(b.completed_at ?? b.created_at).getTime(),
    );
}

function HistoryPage() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const runsQuery = useAnalysisRuns(activeRepositoryId);
  const branches = useRepoBranches(activeRepositoryId);
  const createRun = useCreateAnalysisRun(activeRepositoryId ?? "");
  const queryClient = useQueryClient();

  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const runs = runsQuery.data ?? [];
  const completed = completedRuns(runs);

  // Each completed run's immediate predecessor on the same branch — exactly
  // what Impact diffs against (`get_previous_completed_run(..., branch=)`), and
  // the natural Compare base. `completed` is newest-first, so "earlier" is
  // later in the array. The first run on a branch has none, so it gets no
  // links (Impact would 409, Compare has nothing to diff).
  const previousOnBranch = new Map<string, AnalysisRunRead>();
  completed.forEach((run, i) => {
    const prev = completed.slice(i + 1).find((r) => r.branch === run.branch);
    if (prev) previousOnBranch.set(run.id, prev);
  });

  const anyInFlight = runs.some(isRunInFlight);
  const selectedBranch = branches.data?.find((b) => b.is_selected)?.name ?? null;

  // `mutateAsync` + try/catch, not `.mutate(vars, {onSuccess})` — see the
  // note in `onboarding.tsx::startAnalysis` (the per-call callbacks silently
  // never fired in this codebase).
  const reanalyze = async () => {
    try {
      await createRun.mutateAsync();
      await queryClient.invalidateQueries({ queryKey: ["analysis-runs", activeRepositoryId] });
    } catch (error) {
      toast.error(
        error instanceof ApiError && error.status < 500
          ? error.message
          : "Couldn't start the analysis — please try again.",
      );
    }
  };

  const webhookCount = runs.filter((r) => r.trigger_source === "webhook").length;
  const failedCount = runs.filter((r) => r.status === "failed").length;
  const durations = completed
    .filter((r) => r.completed_at)
    .map((r) => new Date(r.completed_at!).getTime() - new Date(r.created_at).getTime());
  const medianMs = median(durations);

  // The trend follows the tracked (selected) branch when it has enough
  // completed runs to draw a line; otherwise the branch with the most
  // completed runs (ties go to whichever has the newest run). Following the
  // newest run's branch instead would let one stray run on a side branch hide
  // the trend of the branch that actually has history.
  const completedPerBranch = new Map<string, number>();
  for (const run of completed) {
    completedPerBranch.set(run.branch, (completedPerBranch.get(run.branch) ?? 0) + 1);
  }
  let trendBranch: string | null = null;
  if (selectedBranch && (completedPerBranch.get(selectedBranch) ?? 0) >= 2) {
    trendBranch = selectedBranch;
  } else {
    let best = 0;
    for (const [branch, count] of completedPerBranch) {
      if (count > best) {
        best = count;
        trendBranch = branch;
      }
    }
  }
  const series = trendBranch ? debtSeries(completed, trendBranch) : [];
  const first = series[0];
  const last = series[series.length - 1];
  const debtDelta = first && last ? last.debt_score - first.debt_score : 0;
  const debtTone: Tone = debtDelta > 0 ? "warning" : debtDelta < 0 ? "success" : "neutral";

  const reanalyzeDisabled = createRun.isPending || anyInFlight || activeRepositoryId === null;

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Analysis History</h1>
          <p className="text-[12px] text-muted-foreground">
            Runs are triggered on push to the tracked branch, or manually.
          </p>
        </div>
        <button
          type="button"
          onClick={reanalyze}
          disabled={reanalyzeDisabled}
          className="focus-ring ml-auto flex items-center gap-1.5 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-2.5 py-1.5 text-[12px] hover:bg-[oklch(0.745_0.19_44)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {createRun.isPending || anyInFlight ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCcw className="size-3.5" />
          )}
          {createRun.isPending
            ? "Starting…"
            : anyInFlight
              ? "Analysis running…"
              : selectedBranch
                ? `Re-analyze ${selectedBranch}`
                : "Re-analyze"}
        </button>
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
          <div className="flex flex-col items-center gap-2 p-10 text-center">
            <span className="grid size-11 place-items-center rounded-xl bg-surface-2 text-subtle">
              <History className="size-5" />
            </span>
            <h3 className="text-[14.5px] font-semibold">No analysis runs yet</h3>
            <p className="max-w-md text-[12px] leading-relaxed text-muted-foreground">
              This repository hasn&apos;t been analyzed. Use Re-analyze above, or push a commit to
              the tracked branch.
            </p>
          </div>
        </Panel>
      ) : (
        <>
          <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
            <div className="bg-surface">
              <StatCell
                label="Total analyses"
                value={runs.length.toLocaleString()}
                sub={`${completed.length} completed${failedCount ? ` · ${failedCount} failed` : ""}`}
              />
            </div>
            <div className="bg-surface">
              <StatCell
                label="Triggered by push"
                value={webhookCount.toLocaleString()}
                sub={`${runs.length - webhookCount} manual`}
              />
            </div>
            <div className="bg-surface">
              <StatCell
                label="Median duration"
                value={medianMs === null ? "—" : formatDuration(medianMs)}
                sub="queued to completed"
              />
            </div>
            <div className="bg-surface">
              <StatCell
                label="Debt trend"
                tone={debtTone}
                value={
                  series.length < 2
                    ? "—"
                    : `${debtDelta > 0 ? "+" : debtDelta < 0 ? "−" : ""}${Math.abs(debtDelta).toFixed(1)}`
                }
                sub={
                  series.length < 2
                    ? "needs 2 completed runs on a branch"
                    : `${trendBranch} · ${series.length} runs`
                }
              />
            </div>
          </div>

          <div className="grid gap-3 xl:grid-cols-[1fr_300px]">
            <Panel>
              <PanelHeader
                title="Recent runs"
                subtitle="newest first"
                icon={<History className="size-3.5" />}
              />
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border">
                      {[
                        "Status",
                        "Trigger",
                        "Branch",
                        "Commit",
                        "Files",
                        "APIs",
                        "Debt",
                        "When",
                        "Inspect",
                      ].map((h) => (
                        <th key={h} className="label-xs px-4 py-2 font-medium whitespace-nowrap">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {runs.slice(0, visibleCount).map((r) => {
                      const st = statusTag(r);
                      const done = r.status === "completed";
                      const prev = previousOnBranch.get(r.id);
                      const when = new Date(r.completed_at ?? r.created_at);
                      return (
                        <tr key={r.id} className="hover:bg-surface-2/60">
                          <td className="px-4 py-2.5">
                            <span title={r.error_message ?? undefined}>
                              <Tag tone={st.tone}>{st.label}</Tag>
                            </span>
                          </td>
                          <td className="px-4 py-2.5">
                            <TriggerTag source={r.trigger_source} />
                          </td>
                          <td className="px-4 py-2.5 font-mono text-[11.5px]">{r.branch}</td>
                          <td className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">
                            {shortSha(r.commit_sha)}
                          </td>
                          <td className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">
                            {done ? r.file_count.toLocaleString() : "—"}
                          </td>
                          <td className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">
                            {done ? r.api_count.toLocaleString() : "—"}
                          </td>
                          <td className="px-4 py-2.5 font-mono text-[11.5px]">
                            {done ? r.debt_score.toFixed(1) : "—"}
                          </td>
                          <td
                            className="px-4 py-2.5 text-[11.5px] whitespace-nowrap text-subtle"
                            title={when.toLocaleString()}
                          >
                            {formatDistanceToNow(when, { addSuffix: true })}
                          </td>
                          <td className="px-4 py-2.5 text-[11.5px] whitespace-nowrap">
                            {prev ? (
                              <span className="flex items-center gap-2.5">
                                <Link
                                  to="/app/compare"
                                  search={{ base: prev.id, head: r.id }}
                                  title={`Compare with ${shortSha(prev.commit_sha)} (previous run on ${r.branch})`}
                                  className="focus-ring text-violet-soft hover:underline"
                                >
                                  Compare
                                </Link>
                                <Link
                                  to="/app/impact"
                                  search={{ run: r.id }}
                                  title="What this run changed downstream"
                                  className="focus-ring text-violet-soft hover:underline"
                                >
                                  Impact
                                </Link>
                              </span>
                            ) : (
                              <span
                                className="text-subtle"
                                title={
                                  done
                                    ? `First completed run on ${r.branch} — nothing earlier to compare against`
                                    : "Available once the run completes"
                                }
                              >
                                —
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {runs.length > visibleCount ? (
                <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-2.5">
                  <span className="text-[11.5px] text-subtle">
                    Showing {visibleCount} of {runs.length} runs
                  </span>
                  <button
                    type="button"
                    onClick={() => setVisibleCount((n) => n + PAGE_SIZE)}
                    className="focus-ring rounded-md border border-border bg-surface-2 px-2.5 py-1 text-[11.5px] hover:border-border-strong hover:bg-surface-3"
                  >
                    Show {Math.min(PAGE_SIZE, runs.length - visibleCount)} more
                  </button>
                </div>
              ) : null}
            </Panel>

            <Panel>
              <PanelHeader
                title="Debt score trend"
                subtitle={trendBranch ? `completed runs on ${trendBranch}` : "completed runs"}
              />
              <div className="space-y-3 p-4">
                {series.length >= 2 ? (
                  <>
                    <Sparkline
                      points={series.map((r) => r.debt_score)}
                      tone={debtTone === "neutral" ? "violet" : debtTone}
                      width={240}
                      height={44}
                    />
                    <p className="text-[11.5px] leading-relaxed text-muted-foreground">
                      {first!.debt_score.toFixed(1)} &rarr; {last!.debt_score.toFixed(1)} across{" "}
                      {series.length} completed runs. A higher score means more technical debt.
                    </p>
                  </>
                ) : (
                  <p className="text-[11.5px] leading-relaxed text-muted-foreground">
                    A trend needs at least two completed runs on the same branch.
                  </p>
                )}
              </div>
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
