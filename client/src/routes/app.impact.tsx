import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { formatDistanceToNow, formatDistanceToNowStrict } from "date-fns";
import { z } from "zod";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  FileDiff,
  Flag,
  GitBranch,
  Sparkles,
} from "lucide-react";

import { ImpactMap } from "@/components/impact/impact-map";
import { ImpactVerdict } from "@/components/impact/impact-verdict";
import { Panel } from "@/components/ui-kit";
import { ApiError } from "@/lib/api";
import { useActiveRepositoryId } from "@/lib/active-repo";
import {
  buildReach,
  hopsText,
  plural,
  shortSha,
  splitPath,
  type Reach,
  type ReachItem,
} from "@/lib/impact-view";
import { useImpact, type ImpactRead } from "@/lib/queries/impact";
import { completedRuns, useAnalysisRuns, type AnalysisRunRead } from "@/lib/queries/history";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

const searchSchema = z.object({
  run: z.coerce.string().optional(),
});

export const Route = createFileRoute("/app/impact")({
  validateSearch: searchSchema,
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
        content:
          "Direct and indirect blast radius, API impact and risk scoring, computed from the real dependency graph.",
      },
    ],
  }),
  component: Impact,
});

function groupByBranch(runs: AnalysisRunRead[]): Array<[string, AnalysisRunRead[]]> {
  const map = new Map<string, AnalysisRunRead[]>();
  for (const run of runs) {
    const group = map.get(run.branch);
    if (group) group.push(run);
    else map.set(run.branch, [run]);
  }
  return Array.from(map.entries());
}

/** Renders `backticked` spans as code. The server's `why` sentences
 * ("imports `path`") and the AI summary both mark file names that way. */
function Rich({ text, subtle }: { text: string; subtle?: boolean }) {
  return (
    <>
      {text.split(/`([^`]+)`/).map((part, i) =>
        i % 2 === 1 ? (
          <code
            key={i}
            className={
              subtle
                ? "font-mono break-all text-foreground/80"
                : "rounded-[5px] border border-border bg-surface-2 px-1.5 py-px font-mono text-[0.86em] break-all"
            }
          >
            {part}
          </code>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
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
      <div className="flex flex-col items-center gap-2.5 px-6 py-16 text-center">
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

/** Analyzed commits, grouped by branch, as a git-style timeline. Below the
 * `xl` breakpoint it turns into a row of chips you scroll sideways. */
function RunTimeline({
  runs,
  selectedRunId,
  onSelect,
}: {
  runs: AnalysisRunRead[];
  selectedRunId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <aside
      aria-label="Analyzed commits"
      className="min-w-0 xl:sticky xl:top-4 xl:max-h-[calc(100vh-6rem)] xl:self-start xl:overflow-y-auto xl:border-r xl:border-border xl:pr-3"
    >
      <h2 className="ml-2 text-[11px] font-bold text-foreground">Analyzed commits</h2>
      <p className="mt-0.5 ml-2 text-[10.5px] text-subtle">Pick one to see what it reaches</p>

      {groupByBranch(runs).map(([branch, branchRuns]) => (
        <div key={branch}>
          <p className="mt-4 mb-1.5 ml-2 flex items-center gap-1.5 font-mono text-[10.5px] font-semibold text-muted-foreground">
            <GitBranch className="size-3.5 shrink-0" />
            <span className="truncate">{branch}</span>
          </p>
          <ul className="relative flex gap-1.5 overflow-x-auto pb-2 xl:flex-col xl:gap-0 xl:overflow-visible xl:pb-0 xl:before:absolute xl:before:inset-y-3.5 xl:before:left-4 xl:before:w-px xl:before:bg-border-strong xl:before:content-['']">
            {branchRuns.map((run) => {
              const active = run.id === selectedRunId;
              return (
                <li key={run.id} className="shrink-0 xl:shrink">
                  <button
                    type="button"
                    onClick={() => onSelect(run.id)}
                    aria-current={active}
                    className={cn(
                      "focus-ring relative block w-[176px] rounded-lg border py-2 pr-3 pl-8 text-left xl:w-full xl:border-transparent",
                      active
                        ? "border-border bg-surface-2"
                        : "border-border hover:bg-surface xl:hover:bg-surface",
                    )}
                  >
                    <span
                      className={cn(
                        "absolute top-3.5 left-[11px] size-[11px] rounded-full border-2",
                        active
                          ? "border-violet bg-violet shadow-[0_0_0_4px_oklch(0.703_0.198_42/0.18)]"
                          : "border-border-strong bg-background",
                      )}
                    />
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="font-mono text-[10.5px] font-semibold text-foreground">
                        {shortSha(run.commit_sha)}
                      </span>
                      <span className="shrink-0 text-[10px] whitespace-nowrap text-subtle">
                        {formatDistanceToNowStrict(new Date(run.completed_at ?? run.created_at), {
                          addSuffix: true,
                        })}
                      </span>
                    </span>
                    <span className="mt-px block font-mono text-[10px] text-subtle">
                      {run.file_count} files &middot; debt {run.debt_score.toFixed(1)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </aside>
  );
}

function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-md border border-border bg-surface-2 px-1.5 py-px font-mono text-[10px] text-foreground">
      {children}
    </span>
  );
}

function ImpactHeader({
  run,
  previousRun,
}: {
  run: AnalysisRunRead;
  previousRun: AnalysisRunRead | null;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[24px] leading-tight font-extrabold tracking-tight text-foreground">
          What{" "}
          <span className="font-mono font-semibold tracking-tighter text-violet-soft">
            {shortSha(run.commit_sha)}
          </span>{" "}
          reaches
        </h1>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          <Chip>{run.branch}</Chip>
          {previousRun ? (
            <>
              <Chip>{shortSha(previousRun.commit_sha)}</Chip>
              <ArrowRight className="size-3.5 text-subtle" />
              <Chip>{shortSha(run.commit_sha)}</Chip>
            </>
          ) : null}
          <span>
            analyzed{" "}
            {formatDistanceToNow(new Date(run.completed_at ?? run.created_at), { addSuffix: true })}
          </span>
        </div>
      </div>
      {previousRun ? (
        <Link
          to="/app/compare"
          search={{ base: previousRun.id, head: run.id }}
          className="focus-ring inline-flex items-center gap-2 rounded-lg border border-border-strong bg-surface px-3.5 py-2 text-[11px] font-semibold text-foreground hover:bg-surface-2"
        >
          <FileDiff className="size-3.5" />
          Compare with {shortSha(previousRun.commit_sha)}
        </Link>
      ) : null}
    </div>
  );
}

function AiSummary({ text }: { text: string | null }) {
  return (
    <section
      aria-label="AI summary"
      className="grid gap-2 md:grid-cols-[148px_minmax(0,1fr)] md:gap-5"
    >
      <div className="flex items-start gap-2 pt-0.5 text-[11px] font-bold text-foreground">
        <Sparkles className="mt-0.5 size-3.5 shrink-0 text-violet-soft" />
        <div>
          AI summary
          <p className="text-[10.5px] font-medium text-subtle">
            Written from the facts on this page
          </p>
        </div>
      </div>
      <p className="max-w-[72ch] text-[13.5px] leading-relaxed text-foreground/90">
        {text ? (
          <Rich text={text} />
        ) : (
          <span className="text-subtle">No AI explanation available for this impact analysis.</span>
        )}
      </p>
    </section>
  );
}

type Active = { id: string | null; from: "map" | "list" | null };

function GroupHeader({ title, count }: { title: string; count: string }) {
  return (
    <div className="sticky top-0 z-10 flex items-baseline justify-between bg-surface px-3 pt-3.5 pb-1.5 text-[10.5px] font-bold text-foreground">
      {title}
      <span className="font-medium text-subtle">{count}</span>
    </div>
  );
}

function ReachRow({
  item,
  active,
  onActive,
}: {
  item: ReachItem;
  active: boolean;
  onActive: (id: string | null, from: "list") => void;
}) {
  const { dir, name } = splitPath(item.path);
  return (
    <div
      role="listitem"
      tabIndex={0}
      data-row-id={item.id}
      onMouseEnter={() => onActive(item.id, "list")}
      onMouseLeave={() => onActive(null, "list")}
      onFocus={() => onActive(item.id, "list")}
      onBlur={() => onActive(null, "list")}
      className={cn(
        "focus-ring rounded-lg px-3 py-2 outline-none",
        active && "bg-surface-2 shadow-[inset_2px_0_0_var(--violet)]",
      )}
    >
      {item.kind === "api" ? (
        <p className="flex items-baseline gap-2.5 font-mono text-[10.5px]">
          <b className="min-w-[46px] font-semibold text-[var(--impact-endpoint)]">
            {item.method ?? "ANY"}
          </b>
          <span className="break-all">{item.path}</span>
        </p>
      ) : (
        <>
          <p className="font-mono text-[10.5px] leading-snug break-all">
            <span className="text-subtle">{dir}</span>
            <span className="font-semibold text-foreground">{name}</span>
            {item.kind === "test" ? (
              <span className="ml-2 inline-block rounded-[5px] border border-border-strong px-1.5 align-[1px] font-sans text-[9px] font-semibold text-subtle">
                test &middot; not scored
              </span>
            ) : null}
          </p>
          {item.why ? (
            <p className="mt-0.5 text-[10.5px] text-muted-foreground">
              <Rich text={item.why} subtle />
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

function ReachList({
  reach,
  active,
  onActive,
}: {
  reach: Reach;
  active: Active;
  onActive: (id: string | null, from: "list") => void;
}) {
  const files = reach.items.filter((i) => i.kind !== "api");
  const apis = reach.items.filter((i) => i.kind === "api");
  const hops = Array.from(new Set(files.map((f) => f.ring))).sort((a, b) => a - b);
  return (
    <div role="list" className="pb-3">
      {hops.map((h) => {
        const group = files.filter((f) => f.ring === h);
        return (
          <div key={h}>
            <GroupHeader
              title={h === 1 ? "Imports a changed file" : `${h} hops away`}
              count={plural(group.length, "file")}
            />
            {group.map((item) => (
              <ReachRow
                key={item.id}
                item={item}
                active={active.id === item.id}
                onActive={onActive}
              />
            ))}
          </div>
        );
      })}
      {apis.length > 0 ? (
        <div>
          <GroupHeader title="Endpoints reached" count={plural(apis.length, "endpoint")} />
          {apis.map((item) => (
            <ReachRow
              key={item.id}
              item={item}
              active={active.id === item.id}
              onActive={onActive}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function LegendDot({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <i className={cn("inline-block size-[9px] rounded-full", className)} />
      {label}
    </span>
  );
}

/** The blast-radius map beside the list of everything it reaches. Hovering or
 * focusing a row lights its point on the map and the other way round. */
function ImpactReach({ data }: { data: ImpactRead }) {
  const reach = useMemo(() => buildReach(data), [data]);
  const [active, setActive] = useState<Active>({ id: null, from: null });
  const listRef = useRef<HTMLDivElement>(null);
  const none = reach.items.length === 0;
  const fileCount = data.direct.length + data.indirect.length;
  const activeItem = active.id ? reach.items.find((i) => i.id === active.id) : undefined;

  // A point hovered on the map scrolls its row into view inside the list only
  // (never the page), keeping it clear of the sticky group header.
  useEffect(() => {
    if (active.from !== "map" || !active.id) return;
    const list = listRef.current;
    const row = list?.querySelector<HTMLElement>(`[data-row-id="${CSS.escape(active.id)}"]`);
    if (!list || !row) return;
    const top = row.offsetTop;
    const bottom = top + row.offsetHeight;
    if (top - 40 < list.scrollTop) list.scrollTop = Math.max(0, top - 40);
    else if (bottom > list.scrollTop + list.clientHeight)
      list.scrollTop = bottom - list.clientHeight;
  }, [active]);

  return (
    <section className="grid gap-5 lg:grid-cols-2 lg:items-stretch">
      <Panel className="min-w-0">
        <header className="flex items-baseline justify-between gap-3 px-5 pt-4">
          <h2 className="text-[12px] font-bold text-foreground">Blast radius</h2>
          <span className="text-[10.5px] text-subtle">
            {none
              ? "nothing reached"
              : `${plural(fileCount, "file")}, ${plural(data.affected_apis.length, "endpoint")}`}
          </span>
        </header>
        <div className="px-3 pt-1">
          <ImpactMap
            key={data.analysis_run_id}
            reach={reach}
            commitSha={data.commit_sha}
            activeId={active.id}
            onActive={(id) => setActive({ id, from: "map" })}
          />
        </div>
        <p className="min-h-12 px-5 pt-2 text-[10.5px] text-subtle">
          {activeItem ? (
            <>
              <span className="font-mono break-all text-foreground">
                {activeItem.method ? `${activeItem.method} ` : ""}
                {activeItem.path}
              </span>{" "}
              &middot; {hopsText(activeItem)}
            </>
          ) : none ? (
            "No file or endpoint sits outside the commit."
          ) : (
            "Hover a point, or a row beside the map, to see which file it is."
          )}
        </p>
        <div className="mt-2 flex flex-wrap gap-x-[18px] gap-y-1.5 border-t border-border px-5 pt-3 pb-4 text-[10.5px] text-muted-foreground">
          <LegendDot className="bg-violet" label="This commit" />
          <LegendDot className="bg-violet-soft" label="Imports it directly" />
          <LegendDot className="bg-muted-foreground" label="Further away" />
          <span className="inline-flex items-center gap-2">
            <i className="inline-block size-[9px] rounded-full border-[1.5px] border-muted-foreground" />
            Test file
          </span>
          <span className="inline-flex items-center gap-2">
            <i className="inline-block size-2 rotate-45 rounded-[2px] bg-[var(--impact-endpoint)]" />
            Endpoint
          </span>
        </div>
      </Panel>

      {none ? (
        <EmptyPanel
          icon={<Check className="size-5 text-success" />}
          title="Safe to merge from a dependency standpoint"
          description="The files this commit changed aren't imported anywhere else yet, so no other file or endpoint can break because of it."
        />
      ) : (
        <Panel className="flex min-w-0 flex-col">
          <header className="flex items-baseline justify-between gap-3 px-5 pt-4 pb-1">
            <h2 className="text-[12px] font-bold text-foreground">What it reaches</h2>
            <span className="text-[10.5px] text-subtle">closest first</span>
          </header>
          <div ref={listRef} className="relative max-h-[640px] min-h-0 flex-1 overflow-y-auto px-2">
            <ReachList
              reach={reach}
              active={active}
              onActive={(id, from) => setActive({ id, from })}
            />
          </div>
        </Panel>
      )}
    </section>
  );
}

function ImpactSkeleton() {
  return (
    <div className="space-y-5">
      <div className="h-64 animate-pulse rounded-lg bg-surface-2" />
      <div className="h-16 animate-pulse rounded-lg bg-surface-2" />
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="h-96 animate-pulse rounded-lg bg-surface-2" />
        <div className="h-96 animate-pulse rounded-lg bg-surface-2" />
      </div>
    </div>
  );
}

function Impact() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const runsQuery = useAnalysisRuns(activeRepositoryId);
  const runs = completedRuns(runsQuery.data);
  const search = Route.useSearch();

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  // `?run=` deep-links a specific run (History's per-row "Impact" link); an
  // unknown/stale id falls back to the newest completed run.
  useEffect(() => {
    if (runs.length > 0 && selectedRunId === null) {
      setSelectedRunId(runs.find((r) => r.id === search.run)?.id ?? runs[0]!.id);
    }
  }, [runs, selectedRunId, search.run]);

  const impact = useImpact(activeRepositoryId, selectedRunId);
  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;
  const impactData = impact.data;
  const previousRun = impactData
    ? (runs.find((r) => r.id === impactData.previous_analysis_run_id) ?? null)
    : null;

  const title = (
    <h1 className="mb-3 text-[19px] font-semibold tracking-tight text-foreground">
      Impact Analysis
    </h1>
  );

  return (
    <div className="mx-auto max-w-[1440px] p-4">
      {repositories.isLoading ? (
        <>
          {title}
          <Panel>
            <p className="p-8 text-center text-[13px] text-subtle">Loading&hellip;</p>
          </Panel>
        </>
      ) : activeRepositoryId === null ? (
        <>
          {title}
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
        </>
      ) : runsQuery.isLoading ? (
        <>
          {title}
          <Panel>
            <p className="p-8 text-center text-[13px] text-subtle">
              Loading analysis history&hellip;
            </p>
          </Panel>
        </>
      ) : runsQuery.isError ? (
        <>
          {title}
          <Panel>
            <p className="p-8 text-center text-[13px] text-danger">
              Failed to load analysis history.
            </p>
          </Panel>
        </>
      ) : runs.length === 0 ? (
        <>
          {title}
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
        </>
      ) : (
        <div className="grid gap-5 xl:grid-cols-[236px_minmax(0,1fr)]">
          <RunTimeline runs={runs} selectedRunId={selectedRunId} onSelect={setSelectedRunId} />

          <div className="min-w-0 space-y-5">
            {selectedRun ? <ImpactHeader run={selectedRun} previousRun={previousRun} /> : null}

            {impact.isLoading ? (
              <ImpactSkeleton />
            ) : impact.isError ? (
              impact.error instanceof ApiError && impact.error.status === 409 ? (
                <EmptyPanel
                  icon={<Flag className="size-5" />}
                  title={`First analyzed commit on ${selectedRun?.branch ?? "this branch"}`}
                  description={`There's nothing earlier on ${selectedRun?.branch ?? "this branch"} to compare ${
                    selectedRun ? shortSha(selectedRun.commit_sha) : "this commit"
                  } against yet. Impact starts showing from the next analyzed commit onward.`}
                  action={
                    <Link
                      to="/app/history"
                      className="focus-ring mt-2 inline-flex rounded-lg border border-border-strong bg-surface px-3.5 py-2 text-[11px] font-semibold text-foreground hover:bg-surface-2"
                    >
                      Open history
                    </Link>
                  }
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
            ) : impactData ? (
              <>
                <ImpactVerdict data={impactData} />
                <AiSummary text={impactData.explanation} />
                <ImpactReach data={impactData} />
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
