import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Check, ExternalLink } from "lucide-react";

import { Panel } from "@/components/ui-kit";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { useComponentInsight, type ModuleKind } from "@/lib/queries/architecture";
import {
  useOnboarding,
  useSetStepDone,
  type OnboardingRead,
  type OnboardingStep,
  type ReadFirstFile,
  type StartingPoint,
} from "@/lib/queries/onboarding";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/onboarding")({
  head: () => ({
    meta: [
      { title: "Developer Onboarding — CodeLens AI" },
      { name: "description", content: "A reading order through the codebase, foundations first, with the files to open at each step." },
      { property: "og:title", content: "Developer Onboarding — CodeLens AI" },
      { property: "og:description", content: "Learn a repository in the order its parts depend on each other." },
    ],
  }),
  component: Onboarding,
});

// Kind colours match the Architecture graph (`arch-graph.tsx` KIND_META), except
// service: gold here instead of purple.
const KIND: Record<ModuleKind, { label: string; color: string }> = {
  data: { label: "Data", color: "oklch(0.76 0.15 155)" },
  cache: { label: "Cache", color: "oklch(0.8 0.11 205)" },
  service: { label: "Service", color: "oklch(0.83 0.12 90)" },
  gateway: { label: "API", color: "oklch(0.77 0.17 52)" },
  frontend: { label: "Frontend", color: "oklch(0.76 0.12 250)" },
  external: { label: "External", color: "oklch(0.75 0.16 350)" },
};

const fmt = (n: number) => n.toLocaleString("en-US");
const plural = (n: number, one: string, many = `${one}s`) => `${fmt(n)} ${n === 1 ? one : many}`;

function joinNames(names: string[]): string {
  return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** Nothing imports it, it imports nothing on the path, it serves no endpoints:
 * the backend places such parts last (typically build/lint configuration). */
function isTooling(s: OnboardingStep): boolean {
  return (
    s.kind !== "data" &&
    s.kind !== "cache" &&
    s.builds_on.length === 0 &&
    s.depended_on_by === 0 &&
    s.endpoint_count === 0 &&
    s.in_cycle_with.length === 0
  );
}

function sectionOf(s: OnboardingStep): string {
  if (isTooling(s)) return "Tooling and config";
  return s.kind === "frontend" ? "Frontend" : "Backend";
}

function reasonText(r: ReadFirstFile): string {
  switch (r.reason) {
    case "endpoints":
      return `Declares ${plural(r.count, "endpoint")}`;
    case "imported_by":
      return `Imported by ${plural(r.count, "file")}`;
    case "complexity":
      return `Most complex here, complexity ${r.count} per function on average`;
    case "largest":
      return plural(r.count, "line");
  }
}

function startText(p: StartingPoint): string {
  switch (p.reason) {
    case "readme":
      return "The project's own introduction";
    case "entry_point":
      return `Entry point, reaches ${plural(p.count ?? 0, "file")}`;
    case "imported_by":
      return `Imported by ${plural(p.count ?? 0, "file")}`;
  }
}

function splitPath(path: string): [string, string] {
  const i = path.lastIndexOf("/");
  return i < 0 ? ["", path] : [path.slice(0, i + 1), path.slice(i + 1)];
}

function Onboarding() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const onboarding = useOnboarding(activeRepositoryId);
  const data = onboarding.data;

  return (
    <div className="mx-auto max-w-[1440px] p-4">
      {repositories.isLoading ? (
        <Message>Loading…</Message>
      ) : activeRepositoryId === null ? (
        <Message link={{ to: "/onboarding", label: "Connect a repository" }}>No repository connected yet.</Message>
      ) : onboarding.isLoading ? (
        <Message>Loading the onboarding path…</Message>
      ) : onboarding.isError || !data ? (
        <Message danger>Failed to load the onboarding path.</Message>
      ) : !data.analyzed ? (
        <Message link={{ to: "/onboarding", label: "Run the first analysis" }}>
          This repository hasn't been analyzed yet.
        </Message>
      ) : data.steps.length === 0 ? (
        <Message>
          The analysis found no source code it could read in this repository, so there is no reading order to follow.
        </Message>
      ) : (
        <OnboardingPath
          data={data}
          repositoryId={activeRepositoryId}
          repoName={repositories.data?.find((r) => r.id === activeRepositoryId)?.name ?? "this repository"}
        />
      )}
    </div>
  );
}

function Message({
  children,
  link,
  danger,
}: {
  children: React.ReactNode;
  link?: { to: "/onboarding"; label: string };
  danger?: boolean;
}) {
  return (
    <Panel>
      <div className="p-10 text-center">
        <p className={cn("text-[13px]", danger ? "text-danger" : "text-subtle")}>{children}</p>
        {link ? (
          <Link to={link.to} className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline">
            {link.label}
          </Link>
        ) : null}
      </div>
    </Panel>
  );
}

function OnboardingPath({ data, repositoryId, repoName }: { data: OnboardingRead; repositoryId: string; repoName: string }) {
  const steps = data.steps;
  const [chosen, setChosen] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const setDone = useSetStepDone(repositoryId);

  // Opens on the first unread step; a choice that left the path (re-analysis) falls back the same way.
  const selectedId =
    chosen !== null && steps.some((s) => s.id === chosen) ? chosen : (steps.find((s) => !s.done) ?? steps[0]!).id;
  const selected = steps.find((s) => s.id === selectedId)!;

  const advanceTimer = useRef<number | null>(null);
  useEffect(() => () => {
    if (advanceTimer.current !== null) window.clearTimeout(advanceTimer.current);
  }, []);

  const toggleDone = async (step: OnboardingStep) => {
    const done = !step.done;
    try {
      await setDone.mutateAsync({ stepId: step.id, done });
    } catch {
      return; // the cache was rolled back; the button simply shows the old state
    }
    if (done) {
      const index = steps.findIndex((s) => s.id === step.id);
      const next = steps.slice(index + 1).find((s) => !s.done && s.id !== step.id);
      if (next) advanceTimer.current = window.setTimeout(() => setChosen(next.id), 450);
    }
  };

  const doneCount = steps.filter((s) => s.done).length;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-x-10 gap-y-4 pt-2">
        <div className="min-w-0">
          <h1 className="text-[28px] leading-[1.1] font-semibold tracking-[-0.025em] text-balance">
            Find your way around {repoName}
          </h1>
          <p className="mt-2 max-w-[60ch] text-[12.5px] text-muted-foreground">
            A reading order through the codebase, foundations first. Each step comes after the parts of the code it
            uses, so nothing you open leans on something you haven't seen.
          </p>
        </div>
        <div className="grid w-full gap-2 sm:ml-auto sm:w-[340px]">
          <div className="flex items-baseline justify-between text-[11px] text-muted-foreground">
            <span>
              <strong className="text-[14.5px] font-semibold text-foreground tabular-nums">{doneCount}</strong> of{" "}
              <span className="tabular-nums">{steps.length}</span> steps read
            </span>
            {doneCount === steps.length ? <span className="text-success">All read</span> : null}
          </div>
          <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>
            {steps.map((s, i) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setChosen(s.id)}
                aria-label={`Step ${i + 1}: ${s.title}${s.done ? ", read" : ""}`}
                className={cn(
                  "focus-ring relative h-2 rounded-[3px] transition-colors duration-300",
                  !s.done && "bg-surface-3",
                  s.id === selectedId && "after:absolute after:-inset-x-px after:-inset-y-1 after:rounded-[5px] after:ring-[1.5px] after:ring-violet",
                )}
                style={s.done ? { background: KIND[s.kind].color } : undefined}
              />
            ))}
          </div>
        </div>
      </header>

      {data.starting_points.length > 0 ? <StartStrip points={data.starting_points} /> : null}

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <section aria-labelledby="route-title" className="min-w-0">
          <div className="mb-2.5 flex items-baseline justify-between gap-3">
            <h2 id="route-title" className="text-[11px] font-semibold text-secondary-foreground">
              The route
            </h2>
            <span className="text-[10px] text-subtle">
              {data.total_components > steps.length
                ? `${steps.length} most connected of ${data.total_components} parts`
                : plural(steps.length, "part") + " of the code"}
            </span>
          </div>
          <RouteList steps={steps} selectedId={selectedId} hovered={hovered} onHover={setHovered} onSelect={setChosen} />
          <p className="mt-4 max-w-[52ch] pl-[76px] text-[10px] leading-relaxed text-subtle max-sm:pl-12">
            The order: data and shared code first, then the logic built on them, the API, and the frontend that calls
            it. Tooling and config come last. Arcs show which earlier steps a step imports.
          </p>
        </section>

        <StepPanel
          key={selected.id}
          step={selected}
          steps={steps}
          repositoryId={repositoryId}
          analysisRunId={data.analysis_run_id}
          pending={setDone.isPending && setDone.variables?.stepId === selected.id}
          onToggle={() => void toggleDone(selected)}
          onGo={setChosen}
        />
      </div>
    </div>
  );
}

function StartStrip({ points }: { points: StartingPoint[] }) {
  return (
    <section
      aria-labelledby="start-title"
      className="grid items-center gap-x-5 gap-y-2.5 rounded-xl border border-border bg-surface px-4 py-3.5 md:grid-cols-[auto_minmax(0,1fr)]"
    >
      <h2 id="start-title" className="text-[11px] font-semibold whitespace-nowrap text-secondary-foreground">
        Before you start
      </h2>
      <ul className="flex flex-wrap gap-2">
        {points.map((p) => (
          <li key={p.path} className="min-w-0">
            <Link
              to="/app/codebase"
              search={{ file: p.path }}
              className="focus-ring grid max-w-full gap-px rounded-lg border border-transparent bg-surface-2 px-3 py-1.5 transition-colors hover:border-border-strong"
            >
              <span className="truncate font-mono text-[10px] text-foreground">{p.path}</span>
              <span className="text-[10px] text-muted-foreground">{startText(p)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

type Point = { x: number; y: number };

function RouteList({
  steps,
  selectedId,
  hovered,
  onHover,
  onSelect,
}: {
  steps: OnboardingStep[];
  selectedId: string;
  hovered: string | null;
  onHover: (id: string | null) => void;
  onSelect: (id: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stationRefs = useRef(new Map<string, HTMLSpanElement>());
  const [points, setPoints] = useState<Record<string, Point>>({});

  const measure = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const box = container.getBoundingClientRect();
    const next: Record<string, Point> = {};
    stationRefs.current.forEach((el, id) => {
      const r = el.getBoundingClientRect();
      next[id] = { x: r.left + r.width / 2 - box.left, y: r.top + r.height / 2 - box.top };
    });
    setPoints((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
  }, []);

  useLayoutEffect(measure, [measure, steps]);
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    void document.fonts?.ready.then(measure);
    return () => observer.disconnect();
  }, [measure]);

  const indexOf = useMemo(() => new Map(steps.map((s, i) => [s.id, i])), [steps]);
  const focus = hovered ?? selectedId;
  const sections = new Set(steps.map(sectionOf));

  // Each step to the earlier steps it builds on; the focused step's arcs drawn last (on top).
  const arcs = steps
    .flatMap((s) => s.builds_on.map((d) => ({ from: s.id, to: d })))
    .sort((a, b) => Number(a.from === focus || a.to === focus) - Number(b.from === focus || b.to === focus));

  let lastSection: string | null = null;

  return (
    <div ref={containerRef} className="relative" onMouseLeave={() => onHover(null)}>
      <svg aria-hidden className="pointer-events-none absolute inset-0 h-full w-full overflow-visible">
        {steps.slice(0, -1).map((s, i) => {
          const following = steps[i + 1]!;
          const a = points[s.id];
          const b = points[following.id];
          if (!a || !b) return null;
          const lit = s.done && following.done;
          return (
            <line
              key={s.id}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              strokeWidth={2}
              className="transition-[stroke] duration-300"
              stroke={lit ? "var(--success)" : "var(--border-strong)"}
            />
          );
        })}
        {arcs.map(({ from, to }) => {
          const a = points[from];
          const b = points[to];
          if (!a || !b) return null;
          const span = Math.abs((indexOf.get(from) ?? 0) - (indexOf.get(to) ?? 0));
          const x = a.x - 14;
          const cx = x - Math.min(a.x - 6, 12 + span * 7);
          const on = from === focus;
          const dependent = to === focus;
          return (
            <g key={`${from}->${to}`}>
              <path
                d={`M ${x} ${a.y} C ${cx} ${a.y}, ${cx} ${b.y}, ${x} ${b.y}`}
                fill="none"
                strokeLinecap="round"
                stroke={on ? "var(--violet)" : dependent ? "var(--secondary-foreground)" : "var(--surface-3)"}
                strokeWidth={on ? 2 : dependent ? 1.4 : 1.2}
                strokeDasharray={dependent && !on ? "3 3" : undefined}
              />
              {on ? <circle cx={x} cy={b.y} r={2.6} fill="var(--violet)" /> : null}
            </g>
          );
        })}
      </svg>

      {steps.map((s, i) => {
        const section = sectionOf(s);
        const showSection = sections.size > 1 && section !== lastSection;
        lastSection = section;
        const isSelected = s.id === selectedId;
        const color = KIND[s.kind].color;
        return (
          <div key={s.id}>
            {showSection ? (
              <p className={cn("pb-1.5 pl-[76px] text-[10px] text-subtle max-sm:pl-12", i > 0 && "pt-3.5")}>{section}</p>
            ) : null}
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              onMouseEnter={() => onHover(s.id)}
              onFocus={() => onHover(s.id)}
              onBlur={() => onHover(null)}
              aria-pressed={isSelected}
              className="group grid w-full grid-cols-[76px_minmax(0,1fr)] items-center text-left focus-visible:outline-none max-sm:grid-cols-[48px_minmax(0,1fr)]"
            >
              <span
                ref={(el) => {
                  if (el) stationRefs.current.set(s.id, el);
                  else stationRefs.current.delete(s.id);
                }}
                className={cn(
                  "relative z-10 grid size-[22px] place-items-center justify-self-center rounded-full border-2 bg-background transition-[background-color,transform] duration-300",
                  isSelected && "scale-[1.18] shadow-[0_0_0_5px_color-mix(in_oklch,var(--violet)_22%,transparent)]",
                )}
                style={{ borderColor: color, background: s.done ? color : undefined }}
              >
                <Check
                  className={cn("size-3 text-background transition-opacity", s.done ? "opacity-100" : "opacity-0")}
                  strokeWidth={3}
                />
              </span>
              <span
                className={cn(
                  "my-1 grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-0.5 rounded-[10px] border px-3.5 py-2.5 transition-colors group-focus-visible:ring-2 group-focus-visible:ring-violet",
                  isSelected ? "border-border-strong bg-surface-2" : "border-transparent group-hover:bg-surface",
                )}
              >
                <span className="text-[10px] text-subtle tabular-nums">{i + 1}</span>
                <span className={cn("truncate text-[12.5px] font-semibold", s.done && "text-secondary-foreground")}>
                  {s.title}
                </span>
                <span className="text-[11px] font-medium whitespace-nowrap" style={{ color }}>
                  {KIND[s.kind].label}
                </span>
                <span className="col-start-2 col-end-4 flex flex-wrap gap-x-2.5 text-[10px] text-muted-foreground">
                  {s.title !== s.id ? <span className="truncate font-mono text-subtle">{s.id}</span> : null}
                  <span className="tabular-nums">{plural(s.file_count, "file")}</span>
                  <span className="tabular-nums">{plural(s.loc, "line")}</span>
                  {s.endpoint_count ? <span className="tabular-nums">{plural(s.endpoint_count, "endpoint")}</span> : null}
                </span>
              </span>
            </button>
          </div>
        );
      })}
    </div>
  );
}

function whyText(step: OnboardingStep, titles: Map<string, string>): React.ReactNode[] {
  const names = (ids: string[]) => joinNames(ids.map((id) => titles.get(id) ?? id));
  const parts: React.ReactNode[] = [];
  if (step.builds_on.length) {
    parts.push(
      <span key="after">
        Comes after <b className="font-semibold text-foreground">{names(step.builds_on)}</b>, which it imports.
      </span>,
    );
  } else if (step.kind === "data" || step.kind === "cache") {
    parts.push(<span key="after">A foundation: the data the rest of the code works with.</span>);
  } else if (isTooling(step)) {
    parts.push(<span key="after">Nothing in the code imports this, and it imports none of it, so it comes last.</span>);
  } else {
    parts.push(<span key="after">Imports nothing else on this route, so it can be read on its own.</span>);
  }
  if (step.in_cycle_with.length) {
    parts.push(
      <span key="cycle">
        {" "}
        It and <b className="font-semibold text-foreground">{names(step.in_cycle_with)}</b> import each other, so read
        them together.
      </span>,
    );
  }
  if (step.depended_on_by) {
    parts.push(
      <span key="used">
        {" "}
        {plural(step.depended_on_by, "part")} of the code {step.depended_on_by === 1 ? "imports" : "import"} it.
      </span>,
    );
  }
  return parts;
}

function StepPanel({
  step,
  steps,
  repositoryId,
  analysisRunId,
  pending,
  onToggle,
  onGo,
}: {
  step: OnboardingStep;
  steps: OnboardingStep[];
  repositoryId: string;
  analysisRunId: string | null;
  pending: boolean;
  onToggle: () => void;
  onGo: (id: string) => void;
}) {
  const insight = useComponentInsight(repositoryId, analysisRunId, step.id);
  const index = steps.findIndex((s) => s.id === step.id);
  const next = steps.slice(index + 1).find((s) => !s.done) ?? steps[index + 1];
  const titles = useMemo(() => new Map(steps.map((s) => [s.id, s.title])), [steps]);
  const color = KIND[step.kind].color;

  return (
    <aside
      aria-live="polite"
      className="min-w-0 overflow-hidden rounded-[14px] border border-border bg-surface lg:sticky lg:top-[4.25rem]"
    >
      <div
        className="grid gap-1.5 border-b border-border px-[22px] pt-5 pb-[18px]"
        style={{ background: `linear-gradient(180deg, color-mix(in oklch, ${color} 9%, var(--surface)) 0%, var(--surface) 100%)` }}
      >
        <div className="flex flex-wrap items-center gap-2.5 text-[10px] text-muted-foreground">
          <span className="tabular-nums">
            Step {index + 1} of {steps.length}
          </span>
          <KindChip color={color}>{KIND[step.kind].label}</KindChip>
          {step.done ? <KindChip color="var(--success)">Read</KindChip> : null}
        </div>
        <h3 className="text-[19px] font-semibold tracking-[-0.02em] break-words">{step.title}</h3>
        {step.title !== step.id ? <p className="font-mono text-[10px] text-subtle">{step.id}</p> : null}
        <p className="mt-1.5 max-w-[62ch] text-[11px] text-secondary-foreground">{whyText(step, titles)}</p>
        <div className="mt-2 flex flex-wrap gap-x-[22px] gap-y-1.5 text-[11px] text-muted-foreground">
          <Fact value={step.file_count} label={step.file_count === 1 ? "file" : "files"} />
          <Fact value={step.loc} label="lines" />
          {step.endpoint_count ? <Fact value={step.endpoint_count} label="endpoints" /> : null}
        </div>
      </div>

      <div className="grid gap-[22px] px-[22px] pt-[18px] pb-[22px]">
        {step.read_first.length > 0 ? (
          <div>
            <h4 className="mb-2.5 text-[11px] font-semibold text-secondary-foreground">Open these first</h4>
            <ol className="grid gap-0.5">
              {step.read_first.map((r, i) => {
                const [dir, base] = splitPath(r.path);
                return (
                  <li key={r.path}>
                    <Link
                      to="/app/codebase"
                      search={{ file: r.path }}
                      className="focus-ring group grid grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-x-2.5 gap-y-0.5 rounded-lg px-2.5 py-2 hover:bg-surface-2"
                    >
                      <span className="row-span-2 self-start pt-px text-[10px] text-subtle tabular-nums">{i + 1}</span>
                      <span className="truncate font-mono text-[10.5px] text-muted-foreground" title={r.path}>
                        {dir}
                        <span className="font-medium text-foreground">{base}</span>
                      </span>
                      <ExternalLink className="row-span-2 size-3.5 text-violet-soft opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
                      <span className="text-[10px] text-subtle">{reasonText(r)}</span>
                    </Link>
                  </li>
                );
              })}
            </ol>
          </div>
        ) : null}

        {step.sample_apis.length > 0 ? (
          <div>
            <h4 className="mb-2.5 text-[11px] font-semibold text-secondary-foreground">Endpoints it serves</h4>
            <div className="flex flex-wrap gap-1.5">
              {step.sample_apis.map((api) => {
                const [method, ...rest] = api.split(" ");
                return (
                  <span key={api} className="rounded-[5px] bg-surface-2 px-2 py-1 font-mono text-[10.5px] text-secondary-foreground">
                    <span className="mr-1.5" style={{ color: KIND.gateway.color }}>
                      {method}
                    </span>
                    {rest.join(" ")}
                  </span>
                );
              })}
              {step.endpoint_count > step.sample_apis.length ? (
                <Link
                  to="/app/api"
                  className="focus-ring rounded-[5px] bg-surface-2 px-2 py-1 font-mono text-[10.5px] text-violet-soft hover:underline"
                >
                  +{step.endpoint_count - step.sample_apis.length} more
                </Link>
              ) : null}
            </div>
          </div>
        ) : null}

        <div>
          <h4 className="mb-2.5 text-[11px] font-semibold text-secondary-foreground">What this part does</h4>
          {insight.isLoading ? (
            <div className="space-y-2" aria-label="Writing a summary">
              <div className="h-3 w-[92%] animate-pulse rounded bg-surface-2" />
              <div className="h-3 w-[84%] animate-pulse rounded bg-surface-2" />
              <div className="h-3 w-[60%] animate-pulse rounded bg-surface-2" />
            </div>
          ) : insight.data?.insight ? (
            <>
              <p className="max-w-[64ch] text-[12.5px] leading-[1.65] text-secondary-foreground">{insight.data.insight}</p>
              <p className="mt-2 text-[10px] text-subtle">AI summary, written from this part's analysis facts.</p>
            </>
          ) : (
            <p className="text-[11px] text-subtle">No AI summary is available for this part right now.</p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2.5 border-t border-border pt-4">
          {step.done ? (
            <>
              <button
                type="button"
                onClick={onToggle}
                disabled={pending}
                className="focus-ring rounded-lg bg-success/15 px-4 py-2 text-[11px] font-semibold text-success transition-colors hover:bg-success/20 disabled:opacity-60"
              >
                Read
              </button>
              <span className="text-[10px] text-muted-foreground">
                Marked as read{step.completed_at ? ` on ${new Date(step.completed_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}` : ""}. Click to undo.
              </span>
            </>
          ) : (
            <button
              type="button"
              onClick={onToggle}
              disabled={pending}
              className="focus-ring rounded-lg bg-violet px-4 py-2 text-[11px] font-semibold text-primary-foreground transition-colors hover:bg-violet-soft disabled:opacity-60"
            >
              Mark as read
            </button>
          )}
          {next && next.id !== step.id ? (
            <button
              type="button"
              onClick={() => onGo(next.id)}
              className="focus-ring ml-auto px-1 py-1.5 text-[11px] text-muted-foreground hover:[&_b]:text-violet-soft"
            >
              Next: <b className="font-semibold text-foreground transition-colors">{next.title}</b>
            </button>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

function KindChip({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium"
      style={{ color, background: `color-mix(in oklch, ${color} 14%, transparent)` }}
    >
      <span className="size-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}

function Fact({ value, label }: { value: number; label: string }) {
  return (
    <span>
      <strong className="mr-1 font-semibold text-foreground tabular-nums">{fmt(value)}</strong>
      {label}
    </span>
  );
}
