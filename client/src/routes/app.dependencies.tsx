import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState, type ReactNode } from "react";
import { GitBranch, RefreshCcw, PackageX, AlertOctagon, Layers } from "lucide-react";

import { Panel, PanelHeader, ScoreBar, Tag, severityTone, toneColor } from "@/components/ui-kit";
import { useActiveRepositoryId } from "@/lib/active-repo";
import type { ModuleKind } from "@/lib/queries/architecture";
import { useArchitecture } from "@/lib/queries/architecture";
import type { CircularGroup } from "@/lib/queries/dependencies";
import { useDependencies } from "@/lib/queries/dependencies";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/dependencies")({
  head: () => ({
    meta: [
      { title: "Dependency Analysis — CodeLens AI" },
      {
        name: "description",
        content:
          "Dependency graph, circular dependencies, coupling hotspots, orphaned modules and outdated external packages.",
      },
      { property: "og:title", content: "Dependency Analysis — CodeLens AI" },
      {
        property: "og:description",
        content: "Understand internal coupling and external package risk in one view.",
      },
    ],
  }),
  component: Dependencies,
});

const filters = ["All", "Frontend", "Backend", "Services", "Libraries", "External APIs"];

// Tabs map onto the real `ModuleKind` set the architecture endpoint
// classifies — except "Libraries", which has no architectural-kind
// counterpart (see below) and is deliberately absent here rather than
// forced onto an unrelated kind.
const tabKinds: Partial<Record<string, ModuleKind[]>> = {
  Frontend: ["frontend"],
  Backend: ["gateway", "data", "cache"],
  Services: ["service"],
  "External APIs": ["external"],
};

// Reconstructs `module_classification.py::_compute_group_key`'s real
// grouping key from a raw dependency-analysis path, so the tab filter
// below can classify `data.coupled[].module` / `data.orphans[]` /
// `data.circular[].files[]` — which carry no kind of their own — against
// the real per-node `kind` the architecture endpoint already computed.
// Mirrors the backend's role-keyword vocabulary exactly (controllers/
// services/middleware), with one deliberate exception: the backend's real
// "routes" role comes from a file actually owning an `ApiEndpoint` row (a
// DB fact this page's raw path lists don't carry), so a literal `routes`
// path segment is used here as an honest proxy for that one role, not a
// claim of the same precision. A path whose reconstructed key doesn't
// match any real node id classifies as `null` (unclassified) rather than
// guessing further — filtered views exclude it instead of showing it
// under a possibly-wrong category.
const CONTROLLER_KEYWORDS = new Set(["controllers", "controller", "handlers", "handler"]);
const SERVICES_KEYWORDS = new Set(["services", "service"]);
const MIDDLEWARE_KEYWORDS = new Set(["middleware", "middlewares"]);

function moduleGroupKey(modulePath: string): string {
  const segments = modulePath.split("/").filter((s) => s !== "" && s !== ".");
  if (segments.length === 0) return ".";
  const top = segments[0]!;
  const lowered = segments.map((s) => s.toLowerCase());
  if (lowered.some((s) => CONTROLLER_KEYWORDS.has(s))) return `${top}/controllers`;
  if (lowered.some((s) => SERVICES_KEYWORDS.has(s))) return `${top}/services`;
  if (lowered.some((s) => MIDDLEWARE_KEYWORDS.has(s))) return `${top}/middleware`;
  if (lowered.includes("routes")) return `${top}/routes`;
  return segments.slice(0, 2).join("/");
}

function dirnameOf(filePath: string): string {
  const idx = filePath.lastIndexOf("/");
  return idx === -1 ? "." : filePath.slice(0, idx);
}

// `pathIsModule`: `data.coupled[].module` is already a directory-level
// module string; `data.orphans[]`/`data.circular[].files[]` are individual
// file paths, so their containing directory is classified instead — the
// same distinction the backend's own `File.module` (directory) vs.
// `File.path` (file) draws.
function classifyPath(
  path: string,
  pathIsModule: boolean,
  nodeKindById: Map<string, ModuleKind>,
): ModuleKind | null {
  const modulePath = pathIsModule ? path : dirnameOf(path);
  return nodeKindById.get(moduleGroupKey(modulePath)) ?? null;
}

function matchesKinds(kind: ModuleKind | null, kinds: ModuleKind[]): boolean {
  return kind !== null && kinds.includes(kind);
}

const kindOrder: ModuleKind[] = ["frontend", "gateway", "service", "data", "cache", "external"];
const kindLabel: Record<ModuleKind, string> = {
  frontend: "Frontend",
  gateway: "Gateway",
  service: "Service",
  data: "Data",
  cache: "Cache",
  external: "External",
};
// Same semantic mapping `arch-graph.tsx::kindStyle` already uses for these
// exact kinds elsewhere in the app — kept in sync by eye since both are
// small per-file constants (this codebase's established convention), not
// worth a shared export for six color lookups.
const kindAccent: Record<ModuleKind, string> = {
  frontend: "var(--info)",
  gateway: "var(--violet)",
  service: "var(--violet-soft)",
  data: "var(--success)",
  cache: "var(--cyan)",
  external: "var(--magenta)",
};

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

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] ?? path;
}

function Readout({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: ReactNode;
  sub: string;
  accent: string;
}) {
  return (
    <div className="bg-surface p-3.5" style={{ borderLeft: `3px solid ${accent}` }}>
      <p className="label-xs">{label}</p>
      <p className="mt-1.5 font-mono text-[22px] font-semibold tabular-nums text-foreground">
        {value}
      </p>
      <p className="mt-1 text-[11px] text-subtle">{sub}</p>
    </div>
  );
}

/** An illustrative ring diagram for one cycle: nodes placed evenly around a
 * circle, a ring edge between consecutive files plus a few chord edges
 * (count derived from `edge_count`) to suggest real density. This is
 * deliberately not a literal edge-accurate rendering — `CircularGroup`
 * gives us the cycle's member files and total edge count, not the
 * individual edge pairs, so the arrangement is illustrative and the panel
 * caption says so. Only rendered for cycles of 3+ files (see call site). */
// Ring nodes carry the full filename below the diagram already (the
// numbered file list) — the label on the diagram itself is cosmetic, so a
// long real filename (`AssessmentFlow.tsx`) is truncated rather than
// forced to fit, keeping the ring's footprint predictable regardless of
// cycle size.
function truncateLabel(name: string, max = 16): string {
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}

function CycleRing({ cycle }: { cycle: CircularGroup }) {
  const n = cycle.files.length;
  // The viewBox is deliberately wide (not square): the wrapper below sets
  // its CSS `aspectRatio` to match this exact width/height, so
  // `preserveAspectRatio="xMidYMid meet"` fills the box edge-to-edge with
  // no letterboxing, instead of a wide panel squeezing a square diagram
  // down to a small centered box (what happened before this ratio match).
  // Radius/label-offset margins are sized so even a 14-file cycle's
  // truncated labels stay inside the viewBox instead of relying on
  // `overflow: visible` to spill past it (that's what previously let long
  // labels overlap the rest of the page for a real, wide cycle).
  const width = 520;
  const height = 320;
  const cx = width / 2;
  const cy = height / 2;
  const r = Math.min(115, 48 + n * 5);
  const stroke = toneColor(severityTone(cycle.severity));

  const points = Array.from({ length: n }, (_, i) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    const x = cx + r * Math.cos(a);
    const y = cy + r * Math.sin(a);
    const lx = cx + (r + 24) * Math.cos(a);
    const ly = cy + (r + 24) * Math.sin(a);
    const anchor = lx < cx - 5 ? "end" : lx > cx + 5 ? "start" : "middle";
    return { x, y, lx, ly, anchor: anchor as "end" | "start" | "middle" };
  });

  function edgePath(i: number, j: number) {
    const p1 = points[i]!;
    const p2 = points[j]!;
    const mx = (p1.x + p2.x) / 2;
    const my = (p1.y + p2.y) / 2;
    const dx = p2.x - mx;
    const dy = p2.y - my;
    const bend = 0.28;
    const cxp = mx - dy * bend;
    const cyp = my + dx * bend;
    const rTarget = 16;
    const ang = Math.atan2(p2.y - cyp, p2.x - cxp);
    const ex = p2.x - Math.cos(ang) * rTarget;
    const ey = p2.y - Math.sin(ang) * rTarget;
    return `M${p1.x.toFixed(1)},${p1.y.toFixed(1)} Q${cxp.toFixed(1)},${cyp.toFixed(1)} ${ex.toFixed(1)},${ey.toFixed(1)}`;
  }

  const ringEdges: Array<[number, number]> = points.map((_, i) => [i, (i + 1) % n]);
  const extraChords = Math.max(0, Math.min(4, cycle.edge_count - n));
  const chordEdges: Array<[number, number]> = Array.from(
    { length: extraChords },
    (_, i): [number, number] => [0, Math.max(2, Math.round((n / (extraChords + 1)) * (i + 1))) % n],
  ).filter(([, j]) => j !== 0);

  return (
    <div className="w-full overflow-hidden" style={{ aspectRatio: `${width} / ${height}` }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height="100%"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <marker
            id="cycle-arrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L10,5 L0,10 z" fill={stroke} />
          </marker>
        </defs>
        {[...ringEdges, ...chordEdges].map(([i, j], idx) => (
          <path
            key={idx}
            d={edgePath(i, j)}
            fill="none"
            stroke={stroke}
            strokeWidth={1.4}
            strokeDasharray="4 3"
            opacity={0.7}
            markerEnd="url(#cycle-arrow)"
          />
        ))}
        {points.map((p, i) => (
          <g key={i}>
            <circle
              cx={p.x}
              cy={p.y}
              r={15}
              fill="var(--surface)"
              stroke={i === 0 ? "var(--violet-soft)" : stroke}
              strokeWidth={1.8}
            />
            <text
              x={p.x}
              y={p.y + 4}
              textAnchor="middle"
              className="fill-foreground font-mono"
              style={{ fontSize: 12.5 }}
            >
              {i + 1}
            </text>
            <text
              x={p.lx}
              y={p.ly + 3.5}
              textAnchor={p.anchor}
              className="fill-subtle font-mono"
              style={{ fontSize: 11.5 }}
            >
              {truncateLabel(basename(cycle.files[i]!))}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function Dependencies() {
  const [activeTab, setActiveTab] = useState<string>(filters[0]!);
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const dependencies = useDependencies(activeRepositoryId);
  const architecture = useArchitecture(activeRepositoryId);

  const data = dependencies.data;
  const archData = architecture.data;
  const activeRepo = repositories.data?.find((r) => r.id === activeRepositoryId) ?? null;

  const nodeKindById = useMemo(() => {
    const map = new Map<string, ModuleKind>();
    for (const n of archData?.nodes ?? []) map.set(n.id, n.kind);
    return map;
  }, [archData]);

  // Only classify once real architecture data exists — otherwise every
  // path would look "unclassified" and a real tab would misleadingly
  // render as empty rather than falling back to unfiltered.
  const canClassify = Boolean(archData?.analyzed) && nodeKindById.size > 0;
  const activeKinds = activeTab === "All" ? null : (tabKinds[activeTab] ?? []);
  const kindFilterActive = canClassify && activeKinds !== null;
  // "Libraries" has an empty `activeKinds` (no ModuleKind maps to it), so
  // every path-derived panel below naturally filters to nothing for it —
  // it instead surfaces `data.external` (real outdated-package findings,
  // see `showExternalFindings`), which isn't attributable to a kind at all.
  const showExternalFindings = activeTab === "All" || activeTab === "Libraries";

  const filteredCircular = kindFilterActive
    ? (data?.circular.filter((c) =>
        c.files.some((f) => matchesKinds(classifyPath(f, false, nodeKindById), activeKinds!)),
      ) ?? [])
    : (data?.circular ?? []);
  const filteredCoupled = kindFilterActive
    ? (data?.coupled.filter((m) => matchesKinds(classifyPath(m.module, true, nodeKindById), activeKinds!)) ??
      [])
    : (data?.coupled ?? []);
  const filteredOrphans = kindFilterActive
    ? (data?.orphans.filter((o) => matchesKinds(classifyPath(o, false, nodeKindById), activeKinds!)) ?? [])
    : (data?.orphans ?? []);
  const filteredExternal = showExternalFindings ? (data?.external ?? []) : [];
  const visibleKinds = kindFilterActive ? kindOrder.filter((k) => activeKinds!.includes(k)) : kindOrder;

  const maxCoupledScore = filteredCoupled.length ? Math.max(1, ...filteredCoupled.map((m) => m.score)) : 1;
  const topCycle = filteredCircular[0];

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      {/* TITLE BLOCK */}
      <div className="panel ambient-panel flex flex-wrap items-end justify-between gap-4 px-5 py-4">
        <div>
          <p className="label-xs mb-1.5">Dependency Schematic · Architecture Series</p>
          <h1 className="text-[22px] font-semibold tracking-tight text-foreground">
            Dependency Analysis
          </h1>
          {activeRepo ? (
            <p className="mt-1 font-mono text-[12.5px] text-muted-foreground">
              <span className="text-foreground">{activeRepo.full_name}</span>
              {" · branch "}
              <span className="text-foreground">{activeRepo.default_branch}</span>
            </p>
          ) : null}
        </div>
        {activeRepo ? (
          <div className="flex flex-col items-end gap-1 font-mono text-[11.5px] text-muted-foreground">
            {activeRepo.last_analyzed_commit_sha ? (
              <span>
                commit{" "}
                <span className="rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 text-foreground">
                  {activeRepo.last_analyzed_commit_sha.slice(0, 7)}
                </span>
              </span>
            ) : null}
            <span>{archData?.nodes.length ?? 0} components mapped</span>
          </div>
        ) : null}
      </div>

      {/* TABS */}
      <nav className="flex gap-1 overflow-x-auto border-b border-border" aria-label="Dependency scope filter">

        {filters.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setActiveTab(f)}
            className={cn(
              "focus-ring border-b-2 px-3 py-2 text-[12.5px] font-medium whitespace-nowrap",
              f === activeTab
                ? "border-violet text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {f}
          </button>
        ))}
      </nav>

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
          {/* READOUT STRIP */}
          <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
            <Readout
              label="Circular dependencies"
              value={filteredCircular.length}
              sub={topCycle ? describeCycle(topCycle) : "no cycles detected"}
              accent={filteredCircular.length > 0 ? "var(--danger)" : "var(--success)"}
            />
            <Readout
              label="Coupled modules"
              value={filteredCoupled.length}
              sub={
                filteredCoupled[0]
                  ? `top: ${filteredCoupled[0].module} (score ${filteredCoupled[0].score})`
                  : "no internal edges"
              }
              accent="var(--violet-soft)"
            />
            <Readout
              label="Orphan files"
              value={filteredOrphans.length}
              sub="no internal imports, either direction"
              accent={filteredOrphans.length > 0 ? "var(--warning)" : "var(--success)"}
            />
            <Readout
              label="Outdated packages"
              value={filteredExternal.length}
              sub={
                filteredExternal.length > 0
                  ? `${filteredExternal.reduce((sum, e) => sum + e.used_by, 0)} usage site(s)`
                  : showExternalFindings
                    ? "none flagged"
                    : "not attributed to a layer — see Libraries"
              }
              accent={filteredExternal.length > 0 ? "var(--warning)" : "var(--success)"}
            />
          </div>

          {/* CIRCULAR DEPENDENCY SCHEMATIC + COUPLING LEADERBOARD */}
          <div className="grid gap-3 xl:grid-cols-[1.2fr_1fr]">
            <Panel>
              <PanelHeader
                title="Circular dependency"
                subtitle={
                  topCycle
                    ? `${topCycle.edge_count} edges · illustrative arrangement`
                    : "no cycles detected"
                }
                icon={<RefreshCcw className="size-3.5" />}
              />
              <div className="p-4">
                {topCycle ? (
                  <>
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <Tag tone={severityTone(topCycle.severity)}>{topCycle.severity}</Tag>
                      <span className="font-mono text-[11.5px] text-subtle">
                        {topCycle.files.length} files, mutually reachable — no acyclic import order
                        exists
                      </span>
                    </div>
                    {topCycle.files.length >= 3 ? (
                      <CycleRing cycle={topCycle} />
                    ) : (
                      <p className="font-mono text-[12.5px] text-foreground">
                        {describeCycle(topCycle)}
                      </p>
                    )}
                    <p className="mt-3 border-t border-border pt-3 text-[11.5px] leading-relaxed text-subtle">
                      Every file here imports, directly or transitively, back to itself — extracting
                      the shared surface into its own module is the usual fix. Node arrangement is
                      illustrative: the API returns each cycle's member files and edge count, not
                      the individual edge pairs.
                    </p>
                    <div className="mt-2.5 flex flex-wrap gap-x-1 gap-y-1 font-mono text-[11px]">
                      {topCycle.files.map((f, i) => (
                        <span key={f}>
                          <span className="text-subtle">{i + 1}</span>{" "}
                          <span className="text-muted-foreground">{f}</span>
                          {i < topCycle.files.length - 1 ? " ·" : ""}
                        </span>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="text-[12px] text-subtle">
                    {kindFilterActive && (data?.circular.length ?? 0) > 0
                      ? "No circular dependencies in this category."
                      : "No circular dependencies detected."}
                  </p>
                )}
              </div>
            </Panel>

            <Panel>
              <PanelHeader
                title="Highly coupled modules"
                subtitle="fan-in + fan-out, relative to this repo's most-coupled module"
              />
              {filteredCoupled.length ? (
                <>
                  {/* The backend returns the full ranking, not a fixed
                      top-N (per the plan's own "leaderboard" intent) —
                      real repos can have more entries than fit this
                      column gracefully (HireSight has 18), so this panel
                      shows the top 8 by score (already sorted score-desc
                      by the backend) with an explicit "+N more" count
                      rather than silently truncating. */}
                  <ul className="space-y-3 p-4">
                    {filteredCoupled.slice(0, 8).map((m) => (
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
                  {filteredCoupled.length > 8 ? (
                    <p className="px-4 pb-3 text-[11px] text-subtle">
                      +{filteredCoupled.length - 8} more coupled module
                      {filteredCoupled.length - 8 === 1 ? "" : "s"}
                    </p>
                  ) : null}
                </>
              ) : (
                <p className="p-4 text-[12px] text-subtle">
                  {kindFilterActive && (data?.coupled.length ?? 0) > 0
                    ? "No coupled modules in this category."
                    : "No internal coupling detected."}
                </p>
              )}
            </Panel>
          </div>

          {/* ALL CYCLES + ORPHANS */}
          <div className="grid gap-3 sm:grid-cols-2">
            <Panel>
              <PanelHeader
                title="All circular dependency groups"
                icon={<GitBranch className="size-3.5" />}
              />
              {filteredCircular.length ? (
                <ul className="divide-y divide-border">
                  {filteredCircular.map((c, i) => (
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
                <p className="p-4 text-[12px] text-subtle">
                  {kindFilterActive && (data?.circular.length ?? 0) > 0
                    ? "No circular dependencies in this category."
                    : "No circular dependencies detected."}
                </p>
              )}
            </Panel>

            <Panel>
              <PanelHeader
                title="Orphan files"
                subtitle="no internal imports either direction"
                icon={<PackageX className="size-3.5" />}
              />
              {filteredOrphans.length ? (
                <ul className="divide-y divide-border">
                  {filteredOrphans.map((o) => (
                    <li key={o} className="flex items-center gap-2.5 px-4 py-2.5">
                      <span className="size-2 shrink-0 rounded-full border border-subtle" />
                      <span className="break-all font-mono text-[11.5px] text-muted-foreground">
                        {o}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="p-4 text-[12px] text-subtle">
                  {kindFilterActive && (data?.orphans.length ?? 0) > 0
                    ? "No orphan files in this category."
                    : "No orphan files detected."}
                </p>
              )}
            </Panel>
          </div>

          {/* EXTERNAL DEPENDENCIES */}
          <Panel>
            <PanelHeader
              title="External dependencies"
              subtitle={
                filteredExternal.length
                  ? `${filteredExternal.length} outdated package${filteredExternal.length === 1 ? "" : "s"} flagged`
                  : showExternalFindings
                    ? "no outdated packages flagged"
                    : "not attributed to a layer"
              }
              icon={<AlertOctagon className="size-3.5" />}
            />
            {filteredExternal.length ? (
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
                    {filteredExternal.map((d) => (
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
            ) : showExternalFindings ? (
              <p className="p-4 text-[12px] text-subtle">
                No outdated packages flagged by the current heuristic (a small hand-maintained table
                of well-known packages, not a full registry/CVE scan).
              </p>
            ) : (
              <p className="p-4 text-[12px] text-subtle">
                Package findings aren't attributed to a specific architectural layer — switch to the{" "}
                <span className="text-foreground">Libraries</span> tab (or <span className="text-foreground">All</span>)
                to see them.
              </p>
            )}
          </Panel>

          {/* DEPENDENCY MAP (lanes grouped by real classified role) */}
          <Panel>
            <PanelHeader
              title="Dependency map"
              subtitle="components grouped by classified role"
              icon={<Layers className="size-3.5" />}
            />
            <div className="p-4">
              <div className="mb-3 flex flex-wrap gap-3 font-mono text-[10.5px] text-muted-foreground">
                {visibleKinds.map((k) => (
                  <span key={k} className="inline-flex items-center gap-1.5">
                    <i
                      className="inline-block size-2 rounded-sm"
                      style={{ background: kindAccent[k] }}
                    />
                    {kindLabel[k]}
                  </span>
                ))}
              </div>
              {architecture.isLoading ? (
                <p className="p-4 text-center text-[12px] text-subtle">Loading architecture…</p>
              ) : !archData?.analyzed || archData.nodes.length === 0 ? (
                <p className="p-4 text-center text-[12px] text-subtle">
                  Architecture snapshot unavailable.
                </p>
              ) : visibleKinds.length === 0 ? (
                <p className="p-4 text-center text-[12px] text-subtle">
                  No architectural role maps to "Libraries" — see the External dependencies panel
                  above for real package-level findings.
                </p>
              ) : (
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
                  {visibleKinds.map((kind) => {
                    const nodesOfKind = archData.nodes.filter((n) => n.kind === kind);
                    if (nodesOfKind.length === 0) return null;
                    return (
                      <div key={kind}>
                        <p className="label-xs mb-2 border-b border-border-strong pb-1.5">
                          {kindLabel[kind]}
                        </p>
                        <div className="space-y-1.5">
                          {nodesOfKind.map((n) => (
                            <div
                              key={n.id}
                              className="rounded-sm border border-border bg-surface-2 px-2 py-1.5"
                              style={{ borderLeft: `2px solid ${kindAccent[kind]}` }}
                            >
                              <p className="truncate font-mono text-[11.5px] text-foreground">
                                {n.label}
                              </p>
                              <p className="font-mono text-[10px] text-subtle">
                                {n.files} file{n.files === 1 ? "" : "s"}
                              </p>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </Panel>

          <p className="rounded-md border border-dashed border-border bg-surface-2/30 p-3 text-center text-[11.5px] text-subtle">
            Derived fresh from the dependency graph at read time — not a cached snapshot.
            AI-generated insights will appear here once the assistant is wired up.
          </p>
        </>
      )}
    </div>
  );
}
