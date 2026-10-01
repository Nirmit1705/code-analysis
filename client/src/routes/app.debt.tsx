import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ExternalLink } from "lucide-react";

import {
  AiNote,
  Panel,
  PanelHeader,
  Ring,
  ScoreBar,
  StatCell,
  Tag,
  severityTone,
  type Tone,
} from "@/components/ui-kit";
import { useActiveRepositoryId } from "@/lib/active-repo";
import {
  SEVERITIES,
  SEVERITY_WEIGHT,
  useDebt,
  type DebtIssueRead,
  type DebtIssueType,
  type DebtRead,
  type DebtSeverity,
} from "@/lib/queries/debt";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/debt")({
  head: () => ({
    meta: [
      { title: "Technical Debt — CodeLens AI" },
      { name: "description", content: "A technical debt inventory from the analysis rules: every finding with its severity, location and why that kind of debt matters." },
      { property: "og:title", content: "Technical Debt — CodeLens AI" },
      { property: "og:description", content: "Debt findings ranked by severity, with the score broken down by category." },
    ],
  }),
  component: Debt,
});

const PAGE_SIZE = 25;

function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatScore(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function signed(n: number): string {
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${formatScore(Math.abs(n))}`;
}

function Debt() {
  const [severity, setSeverity] = useState<DebtSeverity | null>(null);
  const [category, setCategory] = useState<DebtIssueType | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE_SIZE);

  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const debt = useDebt(activeRepositoryId);
  const data = debt.data;
  const issues = useMemo(() => (data?.analyzed ? data.issues : []), [data]);

  const filtered = useMemo(
    () => issues.filter((i) => (severity === null || i.severity === severity) && (category === null || i.issue_type === category)),
    [issues, severity, category],
  );

  const chooseSeverity = (s: DebtSeverity | null) => {
    setSeverity(s);
    setLimit(PAGE_SIZE);
  };
  const chooseCategory = (c: DebtIssueType | null) => {
    setCategory(c);
    setLimit(PAGE_SIZE);
  };

  const subtitle =
    data?.analyzed
      ? `${data.total_issues} finding${data.total_issues === 1 ? "" : "s"} · score is the severity-weighted sum (critical ${SEVERITY_WEIGHT.critical} · high ${SEVERITY_WEIGHT.high} · medium ${SEVERITY_WEIGHT.medium} · low ${SEVERITY_WEIGHT.low}) — lower is better.`
      : "Findings from the analysis rules, ranked by severity.";

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0">
          <h1 className="text-[17px] font-semibold tracking-tight">Technical Debt</h1>
          <p className="text-[12px] text-muted-foreground">{subtitle}</p>
        </div>
        {data?.analyzed && data.total_issues > 0 ? (
          <div className="ml-auto flex flex-wrap gap-1.5" role="group" aria-label="Filter by severity">
            <FilterButton label="All" count={data.total_issues} active={severity === null} onClick={() => chooseSeverity(null)} />
            {SEVERITIES.map((s) => (
              <FilterButton
                key={s}
                label={s}
                count={data.severity_counts[s]}
                active={severity === s}
                disabled={data.severity_counts[s] === 0}
                onClick={() => chooseSeverity(severity === s ? null : s)}
              />
            ))}
          </div>
        ) : null}
      </div>

      {repositories.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading…</p>
        </Panel>
      ) : activeRepositoryId === null ? (
        <Panel>
          <div className="p-10 text-center">
            <p className="text-[13px] text-subtle">No repository connected yet.</p>
            <Link to="/onboarding" className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline">
              Connect a repository
            </Link>
          </div>
        </Panel>
      ) : debt.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading technical debt…</p>
        </Panel>
      ) : debt.isError || !data ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-danger">Failed to load technical debt.</p>
        </Panel>
      ) : !data.analyzed ? (
        <Panel>
          <div className="p-10 text-center">
            <p className="text-[13px] text-subtle">This repository hasn't been analyzed yet.</p>
            <Link to="/onboarding" className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline">
              Run the first analysis
            </Link>
          </div>
        </Panel>
      ) : (
        <div className="grid gap-3 lg:grid-cols-[260px_minmax(0,1fr)] 2xl:grid-cols-[260px_minmax(0,1fr)_290px]">
          <ScorePanel data={data} />

          <div className="min-w-0 space-y-3">
            {data.total_issues === 0 ? (
              <Panel>
                <div className="p-10 text-center">
                  <p className="text-[13px] font-medium text-success">No debt findings in this analysis.</p>
                  <p className="mt-1 text-[12px] text-muted-foreground">
                    None of the seven rules fired for {data.branch ?? "this branch"} at {data.commit_sha ? shortSha(data.commit_sha) : "this commit"}.
                  </p>
                </div>
              </Panel>
            ) : (
              <>
                {category !== null ? (
                  <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
                    <span>
                      Category: <span className="text-foreground">{data.categories.find((c) => c.issue_type === category)?.label}</span>
                    </span>
                    <button type="button" onClick={() => chooseCategory(null)} className="focus-ring text-violet-soft hover:underline">
                      Clear
                    </button>
                  </div>
                ) : null}

                {filtered.length === 0 ? (
                  <Panel>
                    <p className="p-8 text-center text-[13px] text-subtle">No findings match this filter.</p>
                  </Panel>
                ) : (
                  filtered.slice(0, limit).map((i) => (
                    <IssueCard
                      key={i.id}
                      issue={i}
                      open={openId === i.id}
                      onToggle={() => setOpenId(openId === i.id ? null : i.id)}
                    />
                  ))
                )}

                {filtered.length > limit ? (
                  <div className="flex items-center justify-between gap-3 rounded-md border border-border px-4 py-2.5">
                    <span className="text-[11.5px] text-subtle">
                      Showing {limit} of {filtered.length} findings
                    </span>
                    <button
                      type="button"
                      onClick={() => setLimit((n) => n + PAGE_SIZE)}
                      className="focus-ring rounded-md border border-border bg-surface-2 px-2.5 py-1 text-[11.5px] hover:border-border-strong hover:bg-surface-3"
                    >
                      Show {Math.min(PAGE_SIZE, filtered.length - limit)} more
                    </button>
                  </div>
                ) : null}
                {data.truncated ? (
                  <p className="text-[11.5px] text-subtle">
                    Only the {data.issues.length} most severe of {data.total_issues} findings are listed; the counts and score cover all of them.
                  </p>
                ) : null}
              </>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:col-span-2 2xl:col-span-1 2xl:grid-cols-1">
            <CategoryPanel data={data} selected={category} onSelect={chooseCategory} />
            <TrendPanel data={data} />
          </div>
        </div>
      )}
    </div>
  );
}

function FilterButton({
  label,
  count,
  active,
  disabled,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={cn(
        "focus-ring rounded-md border px-2.5 py-1.5 text-[12px] capitalize disabled:cursor-not-allowed disabled:opacity-40",
        active
          ? "border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)]"
          : "border-border text-muted-foreground hover:border-border-strong",
      )}
    >
      {label} <span className={cn("font-mono text-[10.5px]", active ? "opacity-80" : "text-subtle")}>{count}</span>
    </button>
  );
}

function ScorePanel({ data }: { data: DebtRead }) {
  const c = data.severity_counts;
  // Real fraction, not a made-up scale: how much of the score comes from the
  // two most severe tiers. 0 when there is no debt at all.
  const severeShare =
    data.debt_score > 0 ? ((c.critical * SEVERITY_WEIGHT.critical + c.high * SEVERITY_WEIGHT.high) / data.debt_score) * 100 : 0;
  const ringTone: Tone = data.total_issues === 0 ? "success" : c.critical > 0 ? "danger" : c.high > 0 ? "warning" : "violet";
  const delta = data.score_delta;
  const deltaTone: Tone = delta === null || delta === 0 ? "neutral" : delta > 0 ? "warning" : "success";

  return (
    <Panel ambient className="h-fit">
      <PanelHeader title="Debt score" subtitle="Lower is better" icon={<AlertTriangle className="size-3.5" />} />
      <div className="flex flex-col items-center gap-3 p-5">
        <Ring value={severeShare} size={92} tone={ringTone} label={formatScore(data.debt_score)} />
        {data.total_issues > 0 ? (
          <p className="text-center text-[11.5px] text-muted-foreground">
            {Math.round(severeShare)}% of the score comes from critical and high findings.
          </p>
        ) : (
          <p className="text-center text-[11.5px] text-muted-foreground">No findings, so the score is 0.</p>
        )}
        {data.previous !== null && delta !== null ? (
          <p className="text-center text-[11.5px] text-muted-foreground">
            <span
              className={cn(
                "font-mono text-[12.5px]",
                deltaTone === "warning" && "text-warning",
                deltaTone === "success" && "text-success",
              )}
            >
              {delta === 0 ? "no change" : signed(delta)}
            </span>{" "}
            vs <span className="font-mono text-foreground">{shortSha(data.previous.commit_sha)}</span> ({formatDate(data.previous.completed_at)})
          </p>
        ) : (
          <p className="text-center text-[11px] text-subtle">
            No earlier analysis at a different commit on this branch to compare against.
          </p>
        )}
      </div>
      <div className="grid grid-cols-2 gap-px border-t border-border bg-border">
        <div className="bg-surface"><StatCell label="Critical" value={c.critical} tone={c.critical > 0 ? "danger" : "neutral"} /></div>
        <div className="bg-surface"><StatCell label="High" value={c.high} tone={c.high > 0 ? "warning" : "neutral"} /></div>
        <div className="bg-surface"><StatCell label="Medium" value={c.medium} /></div>
        <div className="bg-surface"><StatCell label="Low" value={c.low} /></div>
        <div className="bg-surface">
          <StatCell
            label="New"
            value={data.new_since_previous ?? "—"}
            {...(data.previous ? { sub: `vs ${shortSha(data.previous.commit_sha)}` } : {})}
            tone={data.new_since_previous ? "warning" : "neutral"}
          />
        </div>
        <div className="bg-surface">
          <StatCell
            label="Resolved"
            value={data.resolved_since_previous ?? "—"}
            {...(data.previous ? { sub: `vs ${shortSha(data.previous.commit_sha)}` } : {})}
            tone={data.resolved_since_previous ? "success" : "neutral"}
          />
        </div>
      </div>
      <p className="border-t border-border px-4 py-2 text-[10.5px] leading-snug text-subtle">
        Lockfiles, build-wrapper scripts and minified bundles (package-lock.json, poetry.lock, gradlew…) are not counted.
      </p>
      {data.previous ? (
        <p className="border-t border-border px-4 py-2 text-[10.5px] leading-snug text-subtle">
          New / resolved compare distinct findings (type, file, module) against the previous run; two findings in the same file
          count once.
        </p>
      ) : null}
    </Panel>
  );
}

function IssueCard({ issue: i, open, onToggle }: { issue: DebtIssueRead; open: boolean; onToggle: () => void }) {
  return (
    <Panel>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="focus-ring flex w-full items-center gap-3 px-4 py-3 text-left"
      >
        <Tag tone={severityTone(i.severity)} caps>{i.severity}</Tag>
        <span className="hidden font-mono text-[11px] text-subtle sm:inline">{i.display_id}</span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{i.title}</span>
        <Tag className="hidden sm:inline-flex">{i.category}</Tag>
        <ChevronDown className={cn("size-3.5 shrink-0 text-subtle transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <div className="space-y-3 border-t border-border p-4">
          <div>
            <p className="label-xs mb-1">Finding</p>
            <p className="break-words text-[12.5px] leading-relaxed text-secondary-foreground">{i.description}</p>
          </div>
          {i.file_path || i.module ? (
            <div>
              <p className="label-xs mb-1.5">Where</p>
              <div className="flex flex-wrap items-center gap-2">
                {i.file_path ? <Tag mono className="max-w-full break-all">{i.file_path}</Tag> : null}
                {i.module ? <span className="font-mono text-[11px] text-subtle">module {i.module}</span> : null}
                {i.file_path ? (
                  <Link
                    to="/app/codebase"
                    search={{ file: i.file_path }}
                    className="focus-ring inline-flex items-center gap-1 text-[12px] text-violet-soft hover:underline"
                  >
                    Open in Codebase <ExternalLink className="size-3" />
                  </Link>
                ) : null}
              </div>
            </div>
          ) : null}
          <div>
            <p className="label-xs mb-1">Why this matters</p>
            <p className="text-[12.5px] leading-relaxed text-secondary-foreground">{i.why}</p>
          </div>
          <AiNote title="Recommended fix">
            Not available yet — AI-written recommendations for each finding are coming in a later update.
          </AiNote>
        </div>
      ) : null}
    </Panel>
  );
}

function CategoryPanel({
  data,
  selected,
  onSelect,
}: {
  data: DebtRead;
  selected: DebtIssueType | null;
  onSelect: (c: DebtIssueType | null) => void;
}) {
  const present = data.categories.filter((c) => c.count > 0);
  const empty = data.categories.filter((c) => c.count === 0);
  const max = Math.max(1, ...present.map((c) => c.weighted_score));

  return (
    <Panel>
      <PanelHeader title="Debt by category" subtitle="Share of the score · click to filter" />
      {present.length === 0 ? (
        <p className="p-4 text-[12px] text-muted-foreground">No findings in any category.</p>
      ) : (
        <ul className="space-y-1 p-2">
          {present.map((c) => (
            <li key={c.issue_type}>
              <button
                type="button"
                onClick={() => onSelect(selected === c.issue_type ? null : c.issue_type)}
                aria-pressed={selected === c.issue_type}
                className={cn(
                  "focus-ring w-full rounded-md px-2 py-2 text-left transition-colors",
                  selected === c.issue_type ? "bg-violet/12" : "hover:bg-surface-2",
                )}
              >
                <div className="mb-1.5 flex items-baseline gap-2 text-[12px]">
                  <span>{c.label}</span>
                  <span className="ml-auto font-mono text-[11.5px] text-muted-foreground">{formatScore(c.weighted_score)}</span>
                </div>
                <ScoreBar value={(c.weighted_score / max) * 100} tone={c.worst_severity ? severityTone(c.worst_severity) : "neutral"} />
                <p className="mt-1 text-[11px] text-subtle">
                  {c.count} finding{c.count === 1 ? "" : "s"} · worst {c.worst_severity}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="border-t border-border px-4 py-2 text-[10.5px] leading-snug text-subtle">
        Bars are relative to the largest category. No findings in: {empty.length ? empty.map((c) => c.label).join(", ") : "none — every category has findings"}.
      </p>
    </Panel>
  );
}

function TrendPanel({ data }: { data: DebtRead }) {
  const trend = data.trend;
  const max = Math.max(1, ...trend.map((t) => t.debt_score));
  const dense = trend.length > 12;

  return (
    <Panel>
      <PanelHeader
        title="Debt trend"
        subtitle={trend.length < 2 ? "needs 2 completed runs on a branch" : `${data.trend_branch} · ${trend.length} runs · lower is better`}
      />
      {trend.length < 2 ? (
        <p className="p-4 text-[12px] text-muted-foreground">A trend appears once this branch has two completed analyses.</p>
      ) : (
        <>
          <div className="flex h-[132px] items-end gap-1 p-4">
            {trend.map((t, idx) => (
              <div
                key={t.analysis_run_id}
                className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1.5"
                title={`${shortSha(t.commit_sha)} · ${formatDate(t.completed_at)} · score ${formatScore(t.debt_score)}`}
              >
                {dense ? null : <span className="font-mono text-[10px] text-subtle">{formatScore(t.debt_score)}</span>}
                <div
                  className={cn(
                    "w-full rounded-t-sm bg-gradient-to-t",
                    idx === trend.length - 1 ? "from-violet/40 to-violet" : "from-violet/20 to-violet/55",
                  )}
                  style={{ height: `${Math.max(2, (t.debt_score / max) * 76)}px` }}
                />
                {dense ? null : <span className="max-w-full truncate font-mono text-[9.5px] text-subtle">{shortSha(t.commit_sha).slice(0, 4)}</span>}
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-2 text-[10.5px] text-subtle">
            <span className="font-mono">{shortSha(trend[0]!.commit_sha)} · {formatScore(trend[0]!.debt_score)}</span>
            <span>bars start at 0 · hover for details</span>
            <span className="font-mono">{shortSha(trend[trend.length - 1]!.commit_sha)} · {formatScore(trend[trend.length - 1]!.debt_score)}</span>
          </div>
        </>
      )}
    </Panel>
  );
}
