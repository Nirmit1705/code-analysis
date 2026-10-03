import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { formatDistanceToNow } from "date-fns";
import { useEffect, useMemo, useRef, useState } from "react";
import { GitBranch, RefreshCw, Sparkles } from "lucide-react";

import { Panel } from "@/components/ui-kit";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { useCreateAnalysisRun } from "@/lib/queries/analysis";
import { useSuggestedQuestions } from "@/lib/queries/assistant";
import { useCompare } from "@/lib/queries/compare";
import { useDashboard, type AiInsight, type DashboardRead, type RecentCommit } from "@/lib/queries/dashboard";
import { useDebt, type DebtCategory, type DebtIssueType, type DebtRead, type DebtSeverity } from "@/lib/queries/debt";
import { useDependencies, type DependenciesRead } from "@/lib/queries/dependencies";
import { isRunInFlight, useAnalysisRuns } from "@/lib/queries/history";
import { useOnboarding } from "@/lib/queries/onboarding";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/")({
  head: () => ({
    meta: [
      { title: "Project Overview — CodeLens AI" },
      {
        name: "description",
        content: "Where the analyzed repository stands: debt over time, failing checks, coupling, recent commits and AI notes.",
      },
      { property: "og:title", content: "Project Overview — CodeLens AI" },
      { property: "og:description", content: "The state of the codebase at a glance, with every number linked to its detail." },
    ],
  }),
  component: Overview,
});

const fmt = (n: number) => n.toLocaleString("en-US");
const plural = (n: number, one: string, many = `${one}s`) => `${fmt(n)} ${n === 1 ? one : many}`;
const shortSha = (sha: string) => sha.slice(0, 7);
const day = (iso: string) => new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
const time = (iso: string) => new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });

// Colours for the language bar, cycled by index (languages arrive largest first).
const LANGUAGE_COLORS = ["oklch(0.76 0.12 250)", "var(--violet)", "oklch(0.83 0.12 90)", "oklch(0.76 0.15 155)"];
const SEVERITY_COLOR: Record<DebtSeverity, string> = {
  critical: "var(--danger)",
  high: "oklch(0.72 0.17 50)",
  medium: "oklch(0.8 0.12 85)",
  low: "var(--subtle)",
};

function Overview() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const activeRepository = repositories.data?.find((r) => r.id === activeRepositoryId) ?? null;
  const dashboard = useDashboard(activeRepositoryId);
  const data = dashboard.data;

  return (
    <div className="mx-auto max-w-[1440px] space-y-8 p-4 pb-16">
      <Header
        repositoryId={activeRepositoryId}
        name={repositories.isLoading ? "Loading…" : (activeRepository?.full_name ?? "No repository connected")}
        isPrivate={activeRepository?.private ?? false}
        data={data?.analyzed ? data : null}
      />

      {repositories.isLoading ? (
        <Message>Loading…</Message>
      ) : activeRepositoryId === null ? (
        <Message link="Connect a repository">No repository connected yet.</Message>
      ) : dashboard.isLoading ? (
        <Message>Loading the overview…</Message>
      ) : dashboard.isError || !data ? (
        <Message danger>Failed to load the overview.</Message>
      ) : !data.analyzed ? (
        <Message link="Run the first analysis">This repository hasn't been analyzed yet.</Message>
      ) : (
        <Body data={data} repositoryId={activeRepositoryId} />
      )}
    </div>
  );
}

function Message({ children, link, danger }: { children: React.ReactNode; link?: string; danger?: boolean }) {
  return (
    <Panel>
      <div className="p-10 text-center">
        <p className={cn("text-[13px]", danger ? "text-danger" : "text-subtle")}>{children}</p>
        {link ? (
          <Link to="/onboarding" className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline">
            {link}
          </Link>
        ) : null}
      </div>
    </Panel>
  );
}

function Header({
  repositoryId,
  name,
  isPrivate,
  data,
}: {
  repositoryId: string | null;
  name: string;
  isPrivate: boolean;
  data: DashboardRead | null;
}) {
  const runs = useAnalysisRuns(repositoryId);
  const createRun = useCreateAnalysisRun(repositoryId ?? "");
  const queryClient = useQueryClient();
  const [queueError, setQueueError] = useState<string | null>(null);
  const inFlight = (runs.data ?? []).some(isRunInFlight);
  const runCommit = data?.recent_commits.find((c) => c.sha === data.commit_sha);

  const reanalyze = async () => {
    setQueueError(null);
    try {
      await createRun.mutateAsync();
      await queryClient.invalidateQueries({ queryKey: ["analysis-runs", repositoryId] });
    } catch {
      setQueueError("Couldn't start the analysis. Try again in a moment.");
    }
  };

  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-3">
      <div className="min-w-0">
        <p className="flex items-center gap-2 font-mono text-[12.5px] font-medium">
          {name}
          {isPrivate ? <span className="rounded-full bg-surface-2 px-2 py-0.5 font-sans text-[10px] text-muted-foreground">Private</span> : null}
        </p>
        {data?.commit_sha && data.completed_at ? (
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[10.5px] text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <GitBranch className="size-3.5" />
              {data.branch} at <span className="font-mono text-secondary-foreground">{shortSha(data.commit_sha)}</span>
              {runCommit ? <span className="truncate text-secondary-foreground">“{runCommit.message}”</span> : null}
            </span>
            <span>
              Analyzed {formatDistanceToNow(new Date(data.completed_at), { addSuffix: true })}, {time(data.completed_at)}
            </span>
          </p>
        ) : null}
      </div>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        {queueError ? <span className="text-[10.5px] text-danger">{queueError}</span> : null}
        {inFlight ? (
          <Link to="/app/history" className="text-[10.5px] text-violet-soft hover:underline">
            Analysis running, follow it in History
          </Link>
        ) : null}
        <button
          type="button"
          onClick={() => void reanalyze()}
          disabled={repositoryId === null || inFlight || createRun.isPending}
          className="focus-ring flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1.5 text-[10.5px] font-semibold text-secondary-foreground hover:border-border-strong disabled:opacity-50"
        >
          <RefreshCw className={cn("size-3.5", (inFlight || createRun.isPending) && "animate-spin")} />
          Re-analyze
        </button>
        <Link
          to="/app/assistant"
          className="focus-ring flex items-center gap-1.5 rounded-lg bg-violet px-3 py-1.5 text-[10.5px] font-semibold text-primary-foreground hover:bg-violet-soft"
        >
          <Sparkles className="size-3.5" /> Ask about this code
        </Link>
      </div>
    </header>
  );
}

function Body({ data, repositoryId }: { data: DashboardRead; repositoryId: string }) {
  const debt = useDebt(repositoryId);
  const deps = useDependencies(repositoryId);
  const debtData = debt.data?.analyzed ? debt.data : null;
  const depsData = deps.data?.analyzed ? deps.data : null;

  return (
    <>
      <Hero data={data} debt={debtData} deps={depsData} repositoryId={repositoryId} loading={debt.isLoading || deps.isLoading} />

      <div className="grid grid-cols-12 gap-4">
        <Section className="col-span-12 xl:col-span-8" title="Debt score over time" link={{ to: "/app/history", label: "History" }}
          sub={debtData ? `${debtData.trend_branch ?? data.branch}, ${plural(debtData.trend.length, "analysis", "analyses")}${debtData.trend[0] ? ` since ${day(debtData.trend[0].completed_at)}` : ""}. Lower is better.` : undefined}>
          {debtData ? <DebtTrend debt={debtData} /> : <Pending query={debt} what="debt history" />}
        </Section>

        <Section className="col-span-12 xl:col-span-4" title="What the debt is made of" link={{ to: "/app/debt", label: "Debt" }}
          sub={debtData ? `${plural(debtData.total_issues, "finding")}, weighted by severity` : undefined}>
          {debtData ? <DebtMakeup debt={debtData} /> : <Pending query={debt} what="debt findings" />}
        </Section>

        <Section className="col-span-12 lg:col-span-6" title="Where changes ripple" link={{ to: "/app/dependencies", label: "Dependencies" }}
          sub="Most coupled modules. Imported by on the left, imports on the right.">
          {depsData ? <Coupling deps={depsData} /> : <Pending query={deps} what="dependencies" />}
        </Section>

        <Section className="col-span-12 lg:col-span-6" title="Recent commits" link={{ to: "/app/compare", label: "Compare" }}
          sub="Impact from how many parts of the code each commit touched">
          <Commits commits={data.recent_commits} />
        </Section>

        <Section className="col-span-12 lg:col-span-7" title="What the AI noticed" sub="Written from this analysis's findings, most serious first">
          <AiNoticed insights={data.ai_insights} />
        </Section>

        <Section className="col-span-12 lg:col-span-5" title="Pick up where you left off" link={{ to: "/app/onboarding", label: "Onboarding" }}>
          <PickUp repositoryId={repositoryId} />
        </Section>

        <Section className="col-span-12" title="What it is built from" link={{ to: "/app/codebase", label: "Codebase" }}
          sub={`${fmt(data.languages.reduce((s, l) => s + l.loc, 0))} lines in recognized languages; the rest of the ${fmt(data.stats!.loc)} are config, lockfiles and docs`}>
          <Composition data={data} />
        </Section>
      </div>
    </>
  );
}

type SectionLink = { to: "/app/history" | "/app/debt" | "/app/dependencies" | "/app/compare" | "/app/onboarding" | "/app/codebase"; label: string };

function Section({ title, sub, link, className, children }: { title: string; sub?: string | undefined; link?: SectionLink; className?: string; children: React.ReactNode }) {
  return (
    <section className={cn("min-w-0 rounded-[14px] border border-border bg-surface px-[22px] py-5", className)}>
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[12.5px] font-semibold tracking-[-0.005em]">{title}</h2>
          {sub ? <p className="mt-0.5 text-[10px] text-subtle">{sub}</p> : null}
        </div>
        {link ? (
          <Link to={link.to} className="focus-ring shrink-0 text-[10px] text-violet-soft hover:underline">
            {link.label}
          </Link>
        ) : null}
      </div>
      {children}
    </section>
  );
}

function Pending({ query, what }: { query: { isLoading: boolean; isError: boolean }; what: string }) {
  return (
    <p className={cn("py-10 text-center text-[11px]", query.isError ? "text-danger" : "text-subtle")}>
      {query.isLoading ? `Loading ${what}…` : query.isError ? `Couldn't load ${what}.` : `No ${what} for this analysis.`}
    </p>
  );
}

// --- top section ---------------------------------------------------------------------

type HeroView = "headline" | "diff" | "checks";
const HERO_VIEWS: { key: HeroView; label: string }[] = [
  { key: "headline", label: "Headline" },
  { key: "diff", label: "Since last commit" },
  { key: "checks", label: "Checks" },
];

function Hero({
  data,
  debt,
  deps,
  repositoryId,
  loading,
}: {
  data: DashboardRead;
  debt: DebtRead | null;
  deps: DependenciesRead | null;
  repositoryId: string;
  loading: boolean;
}) {
  const [view, setView] = useState<HeroView>("headline");
  const documented = data.health_metrics.find((m) => m.key === "docs")?.value ?? null;

  return (
    <section aria-label="Summary" className="grid gap-4">
      <div role="group" aria-label="Summary view" className="inline-flex justify-self-start rounded-[10px] border border-border bg-surface p-[3px]">
        {HERO_VIEWS.map((v) => (
          <button
            key={v.key}
            type="button"
            aria-pressed={view === v.key}
            onClick={() => setView(v.key)}
            className={cn(
              "focus-ring rounded-[7px] px-3 py-1.5 text-[10px] transition-colors",
              view === v.key ? "bg-surface-3 text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {v.label}
          </button>
        ))}
      </div>

      {!debt || !deps ? (
        <p className="py-8 text-[11px] text-subtle">{loading ? "Loading the summary…" : "The summary needs the debt and dependency data, which didn't load."}</p>
      ) : view === "headline" ? (
        <Headline debt={debt} deps={deps} documented={documented} />
      ) : view === "diff" ? (
        <SinceLastCommit debt={debt} data={data} repositoryId={repositoryId} />
      ) : (
        <Checks debt={debt} deps={deps} documented={documented} commitSha={data.commit_sha} />
      )}
    </section>
  );
}

function cycleLabel(deps: DependenciesRead): { files: number; root: string | null } | null {
  const cycle = deps.circular[0];
  if (!cycle) return null;
  const tops = new Set(cycle.files.map((f) => f.split("/")[0]));
  return { files: cycle.files.length, root: tops.size === 1 ? [...tops][0]! : null };
}

function Headline({ debt, deps, documented }: { debt: DebtRead; deps: DependenciesRead; documented: number | null }) {
  const delta = debt.score_delta;
  const critical = debt.severity_counts.critical;
  const trend =
    delta === null ? "Debt is being tracked" : delta < 0 ? "Debt is falling" : delta > 0 ? "Debt is rising" : "Debt is holding steady";
  const tail = critical > 0 ? `, but ${plural(critical, "critical finding")} ${critical === 1 ? "remains" : "remain"}` : ", with nothing critical left";
  const cycle = cycleLabel(deps);

  const figures: { to: "/app/debt" | "/app/dependencies"; label: string; value: string; note?: string | undefined; good?: boolean }[] = [
    {
      to: "/app/debt",
      label: "Debt score",
      value: fmt(debt.debt_score),
      note: delta !== null && delta !== 0 ? `${delta > 0 ? "+" : "−"}${fmt(Math.abs(delta))}` : undefined,
      good: delta !== null && delta < 0,
    },
    { to: "/app/debt", label: "Critical findings", value: fmt(critical), note: `of ${fmt(debt.total_issues)}` },
    { to: "/app/dependencies", label: "Import cycles", value: fmt(deps.circular.length), note: cycle ? plural(cycle.files, "file") : undefined },
    { to: "/app/debt", label: "Documented", value: documented !== null ? `${Math.round(documented)}%` : "–", note: "of symbols" },
  ];

  return (
    <div className="grid items-end gap-x-12 gap-y-6 xl:grid-cols-[minmax(0,1fr)_auto]">
      <h1 className="max-w-[22ch] text-[clamp(28px,3.4vw,44px)] leading-[1.08] font-semibold tracking-[-0.03em] text-balance">
        {trend}
        {tail}.
      </h1>
      <dl className="grid grid-cols-2 gap-x-9 gap-y-3 sm:grid-cols-4">
        {figures.map((f) => (
          <Link key={f.label} to={f.to} className="focus-ring group grid gap-0.5 rounded-md">
            <dt className="text-[10px] text-subtle">{f.label}</dt>
            <dd className="text-[26px] font-semibold tracking-[-0.02em] tabular-nums transition-colors group-hover:text-violet-soft">
              {f.value}
              {f.note ? (
                <small className={cn("ml-1.5 text-[10.5px] font-medium tracking-normal", f.good ? "text-success" : "text-muted-foreground")}>
                  {f.note}
                </small>
              ) : null}
            </dd>
          </Link>
        ))}
      </dl>
    </div>
  );
}

function SinceLastCommit({ debt, data, repositoryId }: { debt: DebtRead; data: DashboardRead; repositoryId: string }) {
  // Fetched only when this view is opened: Compare also writes an AI explanation.
  const compare = useCompare(repositoryId, debt.previous?.analysis_run_id ?? null, data.analysis_run_id);
  if (!debt.previous) {
    return (
      <p className="rounded-[14px] border border-border bg-surface px-5 py-8 text-center text-[11px] text-subtle">
        There's no analysis of an earlier commit on {debt.branch ?? data.branch} to compare with yet.
      </p>
    );
  }
  if (compare.isLoading) return <p className="py-8 text-[11px] text-subtle">Comparing with {shortSha(debt.previous.commit_sha)}…</p>;
  if (compare.isError || !compare.data) return <p className="py-8 text-[11px] text-danger">Couldn't load the comparison.</p>;

  const c = compare.data;
  const resolved = debt.resolved_since_previous ?? 0;
  const added = debt.new_since_previous ?? 0;
  const delta = debt.score_delta ?? 0;
  const fileChanges = c.added_files.length + c.removed_files.length;
  const apiChanges = c.added_apis.length + c.removed_apis.length;
  const importChanges = c.changed_dependencies.length;
  type Line = { sign: "+" | "−" | " "; value: number; what: string; tone: "good" | "bad" | "neutral" | "same" };
  const lines: Line[] = [
    {
      sign: delta < 0 ? "−" : delta > 0 ? "+" : " ",
      value: Math.abs(delta),
      what: `debt score, ${fmt(debt.previous_debt_score ?? debt.debt_score)} to ${fmt(debt.debt_score)}`,
      tone: delta < 0 ? "good" : delta > 0 ? "bad" : "same",
    },
    { sign: resolved ? "−" : " ", value: resolved, what: "findings resolved", tone: resolved ? "good" : "same" },
    { sign: added ? "+" : " ", value: added, what: "new findings", tone: added ? "bad" : "same" },
    { sign: importChanges ? "±" as " " : " ", value: importChanges, what: importChanges === 1 ? "import changed" : "imports changed", tone: importChanges ? "neutral" : "same" },
    { sign: fileChanges ? "±" as " " : " ", value: fileChanges, what: "files added or removed", tone: fileChanges ? "neutral" : "same" },
    { sign: apiChanges ? "±" as " " : " ", value: apiChanges, what: "endpoints added or removed", tone: apiChanges ? "neutral" : "same" },
  ];

  return (
    <div className="overflow-hidden rounded-[14px] border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1 border-b border-border px-[18px] py-3 text-[10.5px] text-muted-foreground">
        <span className="font-mono text-[11.5px] text-foreground">
          {shortSha(c.base_commit_sha)} → {shortSha(c.head_commit_sha)}
        </span>
        <span>What changed since the previous commit's analysis on {c.head_branch}</span>
        <Link
          to="/app/compare"
          search={{ base: c.base_run_id, head: c.head_run_id }}
          className="focus-ring ml-auto text-[10px] text-violet-soft hover:underline"
        >
          Open Compare
        </Link>
      </div>
      <ul className="py-2 font-mono text-[11.5px]">
        {lines.map((l) => (
          <li
            key={l.what}
            className={cn(
              "grid grid-cols-[34px_110px_minmax(0,1fr)] items-baseline py-[5px] pr-[18px]",
              l.tone === "good" && "bg-success/8 text-success",
              l.tone === "bad" && "bg-danger/8 text-danger",
            )}
          >
            <span className={cn("text-center", l.tone === "same" || l.tone === "neutral" ? "text-subtle" : undefined)}>{l.sign}</span>
            <span className={cn("font-medium tabular-nums", l.tone === "same" && "text-subtle")}>{fmt(l.value)}</span>
            <span className={cn("font-sans", l.tone === "same" ? "text-subtle" : "text-secondary-foreground")}>{l.what}</span>
          </li>
        ))}
      </ul>
      <p className="border-t border-border px-[18px] pt-2.5 pb-3.5 text-[10px] text-subtle">
        {plural(data.stats!.files, "file")}, {plural(data.stats!.apis, "endpoint")} and {plural(data.stats!.dependencies, "import")} in total.
      </p>
    </div>
  );
}

type CheckStatus = "pass" | "warn" | "fail";
const SEVERITY_ORDER: DebtSeverity[] = ["critical", "high", "medium", "low"];

function statusFrom(severity: DebtSeverity | null): CheckStatus {
  if (severity === null) return "pass";
  return severity === "critical" || severity === "high" ? "fail" : "warn";
}

function worstOf(...categories: DebtCategory[]): DebtSeverity | null {
  let worst: DebtSeverity | null = null;
  for (const c of categories) {
    if (c.worst_severity && (worst === null || SEVERITY_ORDER.indexOf(c.worst_severity) < SEVERITY_ORDER.indexOf(worst))) worst = c.worst_severity;
  }
  return worst;
}

function Checks({ debt, deps, documented, commitSha }: { debt: DebtRead; deps: DependenciesRead; documented: number | null; commitSha: string | null }) {
  const cat = (type: DebtIssueType): DebtCategory =>
    debt.categories.find((c) => c.issue_type === type) ?? { issue_type: type, label: type, count: 0, worst_severity: null, weighted_score: 0 };
  const complexity = cat("high_complexity");
  const largeFile = cat("large_file");
  const largeModule = cat("large_module");
  const coupling = cat("excessive_coupling");
  const docs = cat("missing_documentation");
  const circular = cat("circular_dependency");
  const outdated = cat("outdated_dependency");
  const cycle = cycleLabel(deps);
  const delta = debt.score_delta;
  const prev = debt.previous ? shortSha(debt.previous.commit_sha) : null;

  type Check = { name: string; status: CheckStatus; detail: string; to: "/app/history" | "/app/debt" | "/app/dependencies"; label: string; weight: number };
  const checks: Check[] = [
    {
      name: delta === null ? "Debt score" : delta < 0 ? "Debt is going down" : delta > 0 ? "Debt is going up" : "Debt is unchanged",
      status: delta === null || delta === 0 ? "warn" : delta < 0 ? "pass" : "fail",
      detail:
        delta === null || prev === null
          ? `${fmt(debt.debt_score)} now; no earlier commit to compare with`
          : `${fmt(debt.debt_score)} now, ${delta <= 0 ? "down" : "up"} ${fmt(Math.abs(delta))} since ${prev}; ${plural(debt.resolved_since_previous ?? 0, "finding")} resolved, ${debt.new_since_previous ? fmt(debt.new_since_previous) : "none"} new`,
      to: "/app/history",
      label: "History",
      weight: 0,
    },
    {
      name: "No import cycles",
      status: statusFrom(circular.worst_severity),
      detail: cycle
        ? `${cycle.files}${cycle.root ? ` ${cycle.root}` : ""} files import each other in ${plural(deps.circular.length, "cycle")}`
        : "No files import each other in a loop",
      to: "/app/dependencies",
      label: "Dependencies",
      weight: circular.weighted_score,
    },
    {
      name: "Function complexity",
      status: statusFrom(complexity.worst_severity),
      detail: complexity.count ? `${plural(complexity.count, "function")} over the complexity limit` : "No function over the complexity limit",
      to: "/app/debt",
      label: "Debt",
      weight: complexity.weighted_score,
    },
    {
      name: "File and module size",
      status: statusFrom(worstOf(largeFile, largeModule)),
      detail:
        largeFile.count + largeModule.count
          ? `${plural(largeFile.count, "large file")} and ${plural(largeModule.count, "large module")}`
          : "No oversized files or modules",
      to: "/app/debt",
      label: "Debt",
      weight: largeFile.weighted_score + largeModule.weighted_score,
    },
    {
      name: "Coupling",
      status: statusFrom(coupling.worst_severity),
      detail: coupling.count ? `${plural(coupling.count, "module")} over the coupling limit` : "No module over the coupling limit",
      to: "/app/dependencies",
      label: "Dependencies",
      weight: coupling.weighted_score,
    },
    {
      name: "Documentation",
      status: statusFrom(docs.worst_severity),
      detail: [documented !== null ? `${Math.round(documented)}% of symbols documented` : null, docs.count ? plural(docs.count, "finding") : null]
        .filter(Boolean)
        .join(", "),
      to: "/app/debt",
      label: "Debt",
      weight: docs.weighted_score,
    },
    {
      name: "Dependencies up to date",
      status: statusFrom(outdated.worst_severity),
      detail: outdated.count ? plural(outdated.count, "outdated package") : "No outdated packages found",
      to: "/app/dependencies",
      label: "Dependencies",
      weight: outdated.weighted_score,
    },
  ];
  const rank: Record<CheckStatus, number> = { fail: 0, warn: 1, pass: 2 };
  checks.sort((a, b) => rank[a.status] - rank[b.status] || b.weight - a.weight);
  const count = (s: CheckStatus) => checks.filter((c) => c.status === s).length;
  const tally = (
    [
      ["fail", "failing", "text-danger"],
      ["warn", "warning", "text-warning"],
      ["pass", "passing", "text-success"],
    ] as const
  ).filter(([s]) => count(s) > 0);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3.5 gap-y-1">
        <h1 className="text-[17px] font-semibold tracking-[-0.015em]">Analysis checks</h1>
        <span className="text-[11px] text-muted-foreground">
          {tally.map(([s, word, cls], i) => (
            <span key={s}>
              {i > 0 ? ", " : ""}
              <b className={cn("font-semibold", cls)}>{count(s)}</b> {word}
            </span>
          ))}
          {commitSha ? `, from the ${shortSha(commitSha)} analysis` : ""}
        </span>
      </div>
      <ul className="grid gap-px overflow-hidden rounded-[14px] border border-border bg-border md:grid-cols-2">
        {checks.map((c) => (
          <li key={c.name} className="bg-surface md:[&:last-child:nth-child(odd)]:col-span-2">
            <Link
              to={c.to}
              className="focus-ring group grid grid-cols-[26px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5 px-[18px] py-3.5 transition-colors hover:bg-surface-2"
            >
              <StatusMark status={c.status} />
              <span className="text-[12px] font-semibold">{c.name}</span>
              <span className="row-span-2 text-[10px] text-subtle group-hover:text-violet-soft">{c.label}</span>
              <span className="col-start-2 text-[10.5px] text-muted-foreground">{c.detail}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusMark({ status }: { status: CheckStatus }) {
  const color = status === "pass" ? "var(--success)" : status === "fail" ? "var(--danger)" : "var(--warning)";
  return (
    <span
      role="img"
      aria-label={status === "pass" ? "Passing" : status === "fail" ? "Failing" : "Warning"}
      className="row-span-2 grid size-[22px] place-items-center rounded-full"
      style={{ color, background: `color-mix(in oklch, ${color} 18%, transparent)` }}
    >
      <svg viewBox="0 0 12 12" fill="none" className="size-3">
        {status === "pass" ? (
          <path d="M2.5 6.3l2.3 2.2L9.5 3.7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        ) : status === "fail" ? (
          <path d="M3.5 3.5l5 5M8.5 3.5l-5 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        ) : (
          <>
            <path d="M6 3v3.6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            <circle cx="6" cy="9" r="1.1" fill="currentColor" />
          </>
        )}
      </svg>
    </span>
  );
}

// --- instruments ---------------------------------------------------------------------

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry!.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function DebtTrend({ debt }: { debt: DebtRead }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const points = debt.trend;
  const H = 220, L = 38, R = 14, T = 14, B = 26;

  const geo = useMemo(() => {
    if (points.length === 0 || W === 0) return null;
    const xs = points.map((p) => new Date(p.completed_at).getTime());
    const vals = points.map((p) => p.debt_score);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const lo = Math.max(0, Math.floor((Math.min(...vals) - 10) / 20) * 20);
    const hi = Math.ceil((Math.max(...vals) + 10) / 20) * 20;
    const X = (t: number) => L + ((t - x0) / (x1 - x0 || 1)) * (W - L - R);
    const Y = (v: number) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
    let line = `M ${X(xs[0]!)} ${Y(vals[0]!)}`;
    for (let i = 1; i < points.length; i++) line += ` H ${X(xs[i]!)} V ${Y(vals[i]!)}`;
    const ticks: number[] = [];
    for (let v = lo; v <= hi; v += 20) ticks.push(v);
    const commitChanges = points.map((p, i) => (i > 0 && p.commit_sha !== points[i - 1]!.commit_sha ? i : -1)).filter((i) => i >= 0);
    return { xs, vals, X, Y, line, area: `${line} V ${H - B} H ${X(xs[0]!)} Z`, ticks, commitChanges };
  }, [points, W]);

  const last = points.length - 1;
  const hi = debt.trend.reduce((m, p) => Math.max(m, p.debt_score), 0);
  const delta = debt.score_delta;

  return (
    <div>
      <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
        {points.length === 0 ? (
          <p className="py-10 text-center text-[11px] text-subtle">No completed analyses to chart yet.</p>
        ) : (
          <svg
            width="100%"
            height={H}
            viewBox={`0 0 ${Math.max(W, 1)} ${H}`}
            role="img"
            aria-label="Debt score per analysis"
            className="block overflow-visible"
            onMouseMove={(e) => {
              if (!geo) return;
              const mx = e.clientX - e.currentTarget.getBoundingClientRect().left;
              let best = 0;
              geo.xs.forEach((t, i) => {
                if (Math.abs(geo.X(t) - mx) < Math.abs(geo.X(geo.xs[best]!) - mx)) best = i;
              });
              setHover(best);
            }}
          >
            {geo ? (
              <>
                <defs>
                  <linearGradient id="debt-fill" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0" stopColor="var(--violet)" stopOpacity="0.22" />
                    <stop offset="1" stopColor="var(--violet)" stopOpacity="0" />
                  </linearGradient>
                </defs>
                {geo.ticks.map((v) => (
                  <g key={v}>
                    <line x1={L} x2={W - R} y1={geo.Y(v)} y2={geo.Y(v)} stroke="var(--border)" />
                    <text x={L - 8} y={geo.Y(v) + 4} textAnchor="end" className="fill-subtle text-[11.5px]" style={{ fontSize: 11.5 }}>
                      {v}
                    </text>
                  </g>
                ))}
                {[0, ...geo.commitChanges, last].filter((i, k, a) => a.indexOf(i) === k).map((i, k, a) => (
                  <text
                    key={`d${i}`}
                    x={geo.X(geo.xs[i]!)}
                    y={H - 6}
                    textAnchor={k === 0 ? "start" : k === a.length - 1 ? "end" : "middle"}
                    className="fill-subtle"
                    style={{ fontSize: 11.5 }}
                  >
                    {day(points[i]!.completed_at)}
                  </text>
                ))}
                <text x={L + 6} y={T + 10} className="fill-muted-foreground font-mono" style={{ fontSize: 11.5 }}>
                  {shortSha(points[0]!.commit_sha)}
                </text>
                {geo.commitChanges.map((i) => (
                  <g key={`c${i}`}>
                    <line x1={geo.X(geo.xs[i]!)} x2={geo.X(geo.xs[i]!)} y1={T} y2={H - B} stroke="var(--border-strong)" strokeDasharray="3 4" />
                    <text x={geo.X(geo.xs[i]!) + 6} y={T + 10} className="fill-muted-foreground font-mono" style={{ fontSize: 11.5 }}>
                      {shortSha(points[i]!.commit_sha)}
                    </text>
                  </g>
                ))}
                <path d={geo.area} fill="url(#debt-fill)" />
                <path d={geo.line} fill="none" stroke="var(--violet)" strokeWidth={2} strokeLinejoin="round" />
                {geo.xs.map((t, i) => (
                  <circle
                    key={i}
                    cx={geo.X(t)}
                    cy={geo.Y(geo.vals[i]!)}
                    r={i === last ? 4.5 : 2.2}
                    fill={i === last ? "var(--violet)" : "var(--surface)"}
                    stroke="var(--violet)"
                    strokeWidth={1.5}
                  />
                ))}
                <text x={geo.X(geo.xs[last]!) - 8} y={geo.Y(geo.vals[last]!) - 10} textAnchor="end" className="fill-foreground" style={{ fontSize: 13, fontWeight: 600 }}>
                  {fmt(geo.vals[last]!)}
                </text>
                {hover !== null ? (
                  <line x1={geo.X(geo.xs[hover]!)} x2={geo.X(geo.xs[hover]!)} y1={T} y2={H - B} stroke="var(--secondary-foreground)" strokeOpacity={0.35} />
                ) : null}
              </>
            ) : null}
          </svg>
        )}
        {geo && hover !== null ? (
          <div
            className="pointer-events-none absolute rounded-lg border border-border-strong bg-surface-3 px-2.5 py-1.5 text-[10px] whitespace-nowrap text-secondary-foreground"
            style={{ left: geo.X(geo.xs[hover]!), top: geo.Y(geo.vals[hover]!), transform: "translate(-50%, calc(-100% - 12px))" }}
          >
            <b className="font-semibold text-foreground tabular-nums">{fmt(points[hover]!.debt_score)}</b> at{" "}
            <span className="font-mono">{shortSha(points[hover]!.commit_sha)}</span>, {day(points[hover]!.completed_at)} {time(points[hover]!.completed_at)}
          </div>
        ) : null}
      </div>
      <div className="mt-3.5 flex flex-wrap gap-x-[22px] gap-y-1.5 text-[10.5px] text-muted-foreground">
        <span>
          Now <b className="font-semibold text-foreground tabular-nums">{fmt(debt.debt_score)}</b>
        </span>
        <span>
          Highest <b className="font-semibold text-foreground tabular-nums">{fmt(hi)}</b>
        </span>
        {delta !== null && debt.previous ? (
          <span>
            Since {shortSha(debt.previous.commit_sha)}{" "}
            <b className={cn("font-semibold tabular-nums", delta < 0 ? "text-success" : delta > 0 ? "text-danger" : "text-foreground")}>
              {delta > 0 ? "+" : delta < 0 ? "−" : ""}
              {fmt(Math.abs(delta))}
            </b>
          </span>
        ) : null}
        {debt.resolved_since_previous !== null ? (
          <span>
            Resolved <b className="font-semibold text-foreground tabular-nums">{fmt(debt.resolved_since_previous)}</b>
          </span>
        ) : null}
        {debt.new_since_previous !== null ? (
          <span>
            New <b className="font-semibold text-foreground tabular-nums">{fmt(debt.new_since_previous)}</b>
          </span>
        ) : null}
      </div>
    </div>
  );
}

function DebtMakeup({ debt }: { debt: DebtRead }) {
  const total = debt.total_issues;
  const severities = SEVERITY_ORDER.filter((s) => debt.severity_counts[s] > 0);
  const maxWeight = Math.max(0, ...debt.categories.map((c) => c.weighted_score));
  const categories = [...debt.categories].sort((a, b) => b.weighted_score - a.weighted_score);
  return (
    <div>
      {total > 0 ? (
        <>
          <div role="img" aria-label="Findings by severity" className="mt-1 mb-2.5 flex h-2.5 gap-0.5 overflow-hidden rounded-[5px]">
            {severities.map((s) => (
              <span key={s} style={{ width: `${(debt.severity_counts[s] / total) * 100}%`, background: SEVERITY_COLOR[s] }} />
            ))}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
            {severities.map((s) => (
              <span key={s} className="flex items-center">
                <i className="mr-1.5 inline-block size-2 rounded-[2px]" style={{ background: SEVERITY_COLOR[s] }} />
                <b className="mr-1 font-semibold text-foreground tabular-nums">{fmt(debt.severity_counts[s])}</b>
                {s}
              </span>
            ))}
          </div>
        </>
      ) : (
        <p className="text-[11px] text-success">No findings in this analysis.</p>
      )}
      <ul className="mt-5 grid gap-[11px]">
        {categories.map((c) => (
          <li key={c.issue_type} className={cn("grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-[5px] text-[11px]", c.count === 0 && "text-subtle")}>
            <span>{c.label}</span>
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {c.count === 0 ? "none" : `${plural(c.count, "finding")}, ${fmt(c.weighted_score)} pts`}
            </span>
            <span className="col-span-2 h-1 overflow-hidden rounded-[2px] bg-surface-3">
              <span className="block h-full rounded-[2px] bg-secondary-foreground" style={{ width: `${maxWeight ? (c.weighted_score / maxWeight) * 100 : 0}%` }} />
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Coupling({ deps }: { deps: DependenciesRead }) {
  const rows = deps.coupled.slice(0, 6);
  const max = Math.max(1, ...rows.flatMap((r) => [r.fan_in, r.fan_out]));
  const cycle = deps.circular[0];
  return (
    <div>
      {rows.length === 0 ? (
        <p className="py-6 text-center text-[11px] text-subtle">No modules import each other.</p>
      ) : (
        <div className="grid gap-0.5">
          <div className="grid grid-cols-[minmax(0,1.4fr)_1fr_1fr] gap-3 px-2 pb-2 text-[9.5px] text-subtle" style={{ fontSize: 11.5 }}>
            <span>Module</span>
            <span className="text-right">imported by</span>
            <span>imports</span>
          </div>
          {rows.map((r) => (
            <Link
              key={r.module}
              to="/app/dependencies"
              className="focus-ring grid grid-cols-[minmax(0,1.4fr)_1fr_1fr] items-center gap-3 rounded-lg px-2 py-[9px] hover:bg-surface-2"
            >
              <span className="truncate font-mono text-[10px] text-secondary-foreground [direction:rtl] text-left" title={r.module}>
                <bdi>{r.module}</bdi>
              </span>
              <span className="flex items-center justify-end gap-2 text-[9.5px] text-muted-foreground tabular-nums" style={{ fontSize: 12 }}>
                {r.fan_in}
                <span className="h-2 rounded-[2px] bg-[oklch(0.76_0.12_250)]" style={{ width: `${(r.fan_in / max) * 100}%` }} />
              </span>
              <span className="flex items-center gap-2 text-[9.5px] text-muted-foreground tabular-nums" style={{ fontSize: 12 }}>
                <span className="h-2 rounded-[2px] bg-violet" style={{ width: `${(r.fan_out / max) * 100}%` }} />
                {r.fan_out}
              </span>
            </Link>
          ))}
        </div>
      )}
      {cycle ? (
        <Link
          to="/app/dependencies"
          className="focus-ring mt-4 grid gap-2 rounded-[10px] border border-danger/30 bg-danger/9 px-4 py-3.5 hover:border-danger/50"
        >
          <strong className="text-[11px] font-semibold">{cycle.files.length} files import each other in a cycle</strong>
          <span className="text-[10px] text-muted-foreground">Changing any one of them can affect all the others. The loop starts at:</span>
          <span className="font-mono text-[9.5px] leading-[1.7] text-secondary-foreground" style={{ fontSize: 12 }}>
            {cycle.files.slice(0, 4).map((f) => f.split("/").pop()).join(" → ")}
            {cycle.files.length > 4 ? <span className="text-subtle"> → {cycle.files.length - 4} more</span> : null}
          </span>
        </Link>
      ) : null}
    </div>
  );
}

const IMPACT_COLOR: Record<RecentCommit["impact"], string> = {
  high: "var(--danger)",
  medium: "oklch(0.8 0.12 85)",
  low: "var(--subtle)",
};

function Commits({ commits }: { commits: RecentCommit[] }) {
  if (commits.length === 0) return <p className="py-6 text-center text-[11px] text-subtle">No recent commits found.</p>;
  return (
    <ul className="relative before:absolute before:top-2 before:bottom-2 before:left-[5px] before:w-px before:bg-border">
      {commits.map((c, i) => {
        const prev = commits[i - 1];
        const gapDays = prev ? (new Date(prev.date).getTime() - new Date(c.date).getTime()) / 86_400_000 : 0;
        return (
          <li key={c.sha}>
            {gapDays > 45 ? (
              <p className="py-1 pl-[34px] text-[9.5px] text-subtle" style={{ fontSize: 11.5 }}>
                {Math.round(gapDays / 30)} months without commits
              </p>
            ) : null}
            <div className="relative grid grid-cols-[22px_minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 py-[9px]">
              <span
                className="relative z-10 mt-[5px] size-[11px] rounded-full border-2 bg-background"
                style={{ borderColor: IMPACT_COLOR[c.impact], background: c.impact === "high" ? IMPACT_COLOR.high : undefined }}
              />
              <span className="truncate text-[11.5px]">{c.message}</span>
              <span className="text-[10px] whitespace-nowrap text-subtle tabular-nums">
                {formatDistanceToNow(new Date(c.date), { addSuffix: true })}
              </span>
              <span className="col-start-2 col-end-4 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[10px] text-muted-foreground">
                <span className="font-mono text-secondary-foreground">{shortSha(c.sha)}</span>
                <span style={{ color: IMPACT_COLOR[c.impact] }}>{c.impact} impact</span>
                <span>{plural(c.components.length, "area")} touched</span>
                <span>{c.author}</span>
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

const INSIGHT_COLOR: Record<AiInsight["tone"], string> = {
  danger: "var(--danger)",
  warning: "var(--warning)",
  info: "oklch(0.76 0.12 250)",
};

function AiNoticed({ insights }: { insights: AiInsight[] }) {
  if (insights.length === 0) {
    return (
      <p className="py-6 text-center text-[11px] text-subtle">
        No AI notes for this analysis. Either none were generated, or nothing stood out.
      </p>
    );
  }
  const order: Record<AiInsight["tone"], number> = { danger: 0, warning: 1, info: 2 };
  const sorted = [...insights].sort((a, b) => order[a.tone] - order[b.tone]);
  return (
    <ul>
      {sorted.map((insight, i) => (
        <li key={i} className={cn("grid grid-cols-[14px_minmax(0,1fr)] gap-x-3 gap-y-1 py-3.5", i > 0 ? "border-t border-border" : "pt-0.5")}>
          <span
            className="mt-[7px] size-2 rounded-full"
            style={{ background: INSIGHT_COLOR[insight.tone], boxShadow: `0 0 0 4px color-mix(in oklch, ${INSIGHT_COLOR[insight.tone]} 18%, transparent)` }}
          />
          <h3 className="text-[12px] font-semibold">{insight.title}</h3>
          <p className="col-start-2 max-w-[70ch] text-[11px] leading-[1.6] text-muted-foreground">{insight.body}</p>
        </li>
      ))}
    </ul>
  );
}

const STEP_COLOR: Record<string, string> = {
  frontend: "oklch(0.76 0.12 250)",
  gateway: "oklch(0.77 0.17 52)",
  data: "oklch(0.76 0.15 155)",
  cache: "oklch(0.8 0.11 205)",
  service: "oklch(0.83 0.12 90)",
  external: "oklch(0.75 0.16 350)",
};

function PickUp({ repositoryId }: { repositoryId: string }) {
  const onboarding = useOnboarding(repositoryId);
  const suggested = useSuggestedQuestions(repositoryId);
  const ob = onboarding.data?.analyzed ? onboarding.data : null;
  const next = ob?.steps.find((s) => !s.done) ?? null;
  const questions = suggested.data?.questions.slice(0, 3) ?? [];

  return (
    <div className="grid gap-[22px]">
      <div>
        {ob && ob.steps.length > 0 ? (
          <>
            <p className="text-[10px] text-subtle">
              <b className="font-semibold text-foreground">{ob.completed_count}</b> of {ob.steps.length} onboarding steps read
            </p>
            <div className="mt-2.5 mb-3 grid gap-[3px]" style={{ gridTemplateColumns: `repeat(${ob.steps.length}, minmax(0, 1fr))` }}>
              {ob.steps.map((s) => (
                <span key={s.id} className="h-1.5 rounded-[2px] bg-surface-3" style={s.done ? { background: STEP_COLOR[s.kind] } : undefined} />
              ))}
            </div>
            {next ? (
              <Link
                to="/app/onboarding"
                className="focus-ring flex items-center justify-between gap-3 rounded-[10px] bg-surface-2 px-3.5 py-3 hover:bg-surface-3"
              >
                <span className="min-w-0">
                  <span className="block text-[10px] text-subtle">Next, step {ob.steps.indexOf(next) + 1}</span>
                  <b className="block truncate text-[11.5px] font-semibold">{next.title}</b>
                </span>
                <span className="text-[10px] whitespace-nowrap text-violet-soft">Continue</span>
              </Link>
            ) : (
              <p className="text-[11px] text-success">Every step is read.</p>
            )}
          </>
        ) : (
          <p className="text-[11px] text-subtle">{onboarding.isLoading ? "Loading onboarding…" : "No onboarding path for this analysis."}</p>
        )}
      </div>
      {questions.length > 0 ? (
        <div>
          <p className="text-[10px] text-subtle">Questions you could ask about this code</p>
          <ul className="mt-2.5 grid gap-1.5">
            {questions.map((q) => (
              <li key={q}>
                <Link
                  to="/app/assistant"
                  search={{ q }}
                  className="focus-ring block rounded-lg border border-border px-3 py-[9px] text-[11px] text-secondary-foreground hover:border-border-strong hover:text-foreground"
                >
                  {q}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Composition({ data }: { data: DashboardRead }) {
  const s = data.stats!;
  const stats: [string, number][] = [
    ["Files", s.files],
    ["Lines, all files", s.loc],
    ["Modules", s.modules],
    ["API endpoints", s.apis],
    ["Import edges", s.dependencies],
    ["Contributors", s.contributors],
  ];
  return (
    <div className="grid items-center gap-x-12 gap-y-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,2fr)]">
      <div>
        {data.languages.length > 0 ? (
          <>
            <div className="mb-2.5 flex h-2 gap-0.5 overflow-hidden rounded">
              {data.languages.map((l, i) => (
                <span key={l.name} style={{ width: `${l.pct}%`, background: LANGUAGE_COLORS[i % LANGUAGE_COLORS.length] }} />
              ))}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
              {data.languages.map((l, i) => (
                <span key={l.name} className="flex items-center">
                  <i className="mr-1.5 inline-block size-2 rounded-[2px]" style={{ background: LANGUAGE_COLORS[i % LANGUAGE_COLORS.length] }} />
                  {l.name} <span className="ml-1 tabular-nums">{l.pct}%</span>
                </span>
              ))}
            </div>
          </>
        ) : (
          <p className="text-[11px] text-subtle">No files in a recognized language.</p>
        )}
      </div>
      <dl className="grid grid-cols-3 gap-x-6 gap-y-3 sm:grid-cols-6">
        {stats.map(([label, value]) => (
          <div key={label}>
            <dt className="text-[9.5px] text-subtle" style={{ fontSize: 12 }}>
              {label}
            </dt>
            <dd className="mt-0.5 text-[14.5px] font-semibold tabular-nums">{fmt(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
