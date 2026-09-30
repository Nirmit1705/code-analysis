import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  AlertTriangle,
  Boxes,
  Cloud,
  Database,
  Maximize2,
  Minus,
  Monitor,
  Plus,
  Webhook,
  Zap,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { HEADER_H, NODE_H, NODE_W, layoutGraph } from "@/lib/graph-layout";
import {
  graphEdges as mockGraphEdges,
  graphNodes as mockGraphNodes,
  type GraphNode,
} from "@/lib/repo-data";

/** The six module kinds Phase 6.5's backend classifier assigns
 * (`server/app/services/module_classification.py::ModuleKind`) — the
 * single source of truth for this union, imported by every module that
 * needs to talk about a node's kind (`graph-layout.ts`,
 * `queries/architecture.ts`). */
export type ModuleKind = GraphNode["kind"];

/** What `ArchGraph` needs to draw one node. Position is *not* part of it —
 * the graph lays itself out (`graph-layout.ts`). `loc` and `health` are
 * optional: real Architecture data supplies `loc` but deliberately never a
 * per-node `health` (no per-module formula was ever specified), so that dot
 * is simply omitted rather than fabricated. */
export interface ArchGraphNode {
  id: string;
  label: string;
  kind: ModuleKind;
  files: number;
  loc?: number;
  /** Files of this node that sit in a real dependency cycle. Lets the graph
   * flag a cycle that lives entirely *inside* one node, which no edge can. */
  circular_files?: number;
  health?: number;
}

/** An edge reads "`from` depends on `to`" (`from` imports `to`). `weight` is
 * the number of file-to-file imports behind it; `risk` marks a circular one. */
export interface ArchGraphEdge {
  from: string;
  to: string;
  label?: string;
  weight?: number;
  risk?: "high" | "med";
}

/** One distinct hue per kind, so the kind is readable at a glance (the
 * app's own theme tokens for these are near-identical oranges/greys, which
 * is why the old graph's kinds all looked alike). Shared with the legend and
 * the kind filter chips. */
export const KIND_META: Record<ModuleKind, { label: string; color: string; icon: LucideIcon }> = {
  frontend: { label: "Frontend", color: "oklch(0.76 0.12 250)", icon: Monitor },
  gateway: { label: "API / Gateway", color: "oklch(0.77 0.17 52)", icon: Webhook },
  service: { label: "Service", color: "oklch(0.74 0.15 305)", icon: Boxes },
  data: { label: "Data", color: "oklch(0.76 0.15 155)", icon: Database },
  cache: { label: "Cache", color: "oklch(0.8 0.11 205)", icon: Zap },
  external: { label: "External", color: "oklch(0.75 0.16 350)", icon: Cloud },
};

const EDGE_NEUTRAL = "oklch(0.74 0.012 60)";
const EDGE_OUT = "var(--violet-soft)";
const EDGE_IN = "oklch(0.78 0.12 235)";
const EDGE_RISK = "var(--danger)";
const EDGE_WARN = "var(--warning)";

const MIN_ZOOM = 0.2;
const MAX_ZOOM = 2.2;

function formatLoc(loc: number): string {
  return loc >= 10_000
    ? `${Math.round(loc / 1000)}k`
    : loc >= 1000
      ? `${(loc / 1000).toFixed(1)}k`
      : `${loc}`;
}

function edgeWidth(weight: number | undefined): number {
  return 1.25 + Math.min(2, Math.log2(Math.max(1, weight ?? 1)) * 0.55);
}

export function ArchGraph({
  nodes = mockGraphNodes,
  edges = mockGraphEdges,
  selectedId,
  onSelect,
  interactive = true,
  staticView = false,
  filterKinds,
  query = "",
  className,
  compact,
}: {
  /** Defaults to the mock graph — the landing page (`routes/index.tsx`)
   * still renders this component with no `nodes`/`edges` at all; real
   * callers (`app.architecture.tsx`, `app.index.tsx`) always pass their own. */
  nodes?: ArchGraphNode[];
  edges?: ArchGraphEdge[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  interactive?: boolean;
  staticView?: boolean;
  filterKinds?: ModuleKind[];
  query?: string;
  className?: string;
  compact?: boolean;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [zoom, setZoom] = useState(0.8);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [smooth, setSmooth] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const autoFit = useRef(true);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number; moved: number } | null>(null);
  const stateRef = useRef({ zoom, offset });
  stateRef.current = { zoom, offset };

  // Layout covers *all* nodes, so filtering/searching hides things without
  // moving anything.
  const layout = useMemo(
    () =>
      layoutGraph(
        nodes,
        edges.map((e) => ({
          from: e.from,
          to: e.to,
          ...(e.weight != null ? { weight: e.weight } : {}),
        })),
      ),
    [nodes, edges],
  );
  const nodeById = useMemo(() => new Map(layout.nodes.map((n) => [n.id, n])), [layout]);

  const fit = useCallback(() => {
    if (!size.w || !size.h || !layout.width) return;
    const pad = compact ? 6 : 28;
    const z = Math.min(
      1.15,
      Math.max(
        MIN_ZOOM,
        Math.min((size.w - pad * 2) / layout.width, (size.h - pad * 2) / layout.height),
      ),
    );
    setZoom(z);
    setOffset({ x: (size.w - layout.width * z) / 2, y: (size.h - layout.height * z) / 2 });
  }, [size.w, size.h, layout.width, layout.height, compact]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setSize({ w: entry.contentRect.width, h: entry.contentRect.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (autoFit.current) fit();
  }, [fit]);

  const wheel = useCallback((e: WheelEvent) => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1);
    const { zoom: z, offset: o } = stateRef.current;
    const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * Math.exp(-dy * 0.0015)));
    const k = next / z;
    autoFit.current = false;
    setSmooth(false);
    setZoom(next);
    setOffset({ x: px - (px - o.x) * k, y: py - (py - o.y) * k });
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el || !interactive) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      wheel(e);
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [interactive, wheel]);

  const zoomBy = (factor: number) => {
    const { zoom: z, offset: o } = stateRef.current;
    const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * factor));
    const k = next / z;
    const cx = size.w / 2;
    const cy = size.h / 2;
    autoFit.current = false;
    setSmooth(true);
    setZoom(next);
    setOffset({ x: cx - (cx - o.x) * k, y: cy - (cy - o.y) * k });
  };

  const visible = (n: { kind: ModuleKind; label: string }) =>
    (!filterKinds || filterKinds.length === 0 || filterKinds.includes(n.kind)) &&
    (query.trim() === "" || n.label.toLowerCase().includes(query.trim().toLowerCase()));

  const focusId = staticView ? null : (hover ?? selectedId ?? null);

  const cyclic = useMemo(() => {
    const s = new Set<string>();
    for (const e of edges) if (e.risk === "high") (s.add(e.from), s.add(e.to));
    for (const n of nodes) if ((n.circular_files ?? 0) > 0) s.add(n.id);
    return s;
  }, [edges, nodes]);

  const connected = useMemo(() => {
    const s = new Set<string>();
    if (focusId) {
      for (const e of edges) {
        if (e.from === focusId) s.add(e.to);
        if (e.to === focusId) s.add(e.from);
      }
    }
    return s;
  }, [edges, focusId]);

  type EdgeState = "base" | "out" | "in" | "dim";
  const drawn = edges
    .map((e, i) => {
      const a = nodeById.get(e.from);
      const b = nodeById.get(e.to);
      const route = layout.routes[i];
      if (!a || !b || !route || !visible(a) || !visible(b)) return null;
      const state: EdgeState = !focusId
        ? "base"
        : e.from === focusId
          ? "out"
          : e.to === focusId
            ? "in"
            : "dim";
      return { e, a, b, route, state, i };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
    // Paint highlighted edges last so they sit on top of the rest.
    .sort(
      (p, q) =>
        Number(p.state === "out" || p.state === "in") -
        Number(q.state === "out" || q.state === "in"),
    );

  const colorFor = (state: EdgeState, risk?: ArchGraphEdge["risk"]) =>
    state === "out"
      ? EDGE_OUT
      : state === "in"
        ? EDGE_IN
        : risk === "high"
          ? EDGE_RISK
          : risk === "med"
            ? EDGE_WARN
            : EDGE_NEUTRAL;
  const markerName = (state: EdgeState, risk?: ArchGraphEdge["risk"]) =>
    state === "out"
      ? "out"
      : state === "in"
        ? "in"
        : risk === "high"
          ? "risk"
          : risk === "med"
            ? "warn"
            : "base";

  return (
    <div
      ref={ref}
      className={cn(
        "relative touch-none overflow-hidden rounded-lg border border-border bg-background grid-field select-none",
        interactive && "cursor-grab active:cursor-grabbing",
        className,
      )}
      onPointerDown={(e: ReactPointerEvent) => {
        if (!interactive) return;
        autoFit.current = false;
        setSmooth(false);
        drag.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y, moved: 0 };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        d.moved = Math.max(d.moved, Math.hypot(e.clientX - d.x, e.clientY - d.y));
        setOffset({ x: d.ox + (e.clientX - d.x), y: d.oy + (e.clientY - d.y) });
      }}
      onPointerUp={() => {
        // Keep `moved` readable for the click that follows a drag.
        window.setTimeout(() => (drag.current = null), 0);
      }}
      onPointerLeave={() => (drag.current = null)}
    >
      <div className="pointer-events-none absolute inset-0 ambient-panel" />

      <div
        className={cn(
          "absolute top-0 left-0 origin-top-left",
          smooth && "transition-transform duration-300 ease-out",
        )}
        style={{
          width: layout.width,
          height: layout.height,
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`,
        }}
      >
        {/* Layer lanes: make the left-to-right architecture layering explicit. */}
        {layout.columns.map((c) => (
          <div
            key={c.x}
            className="absolute rounded-[20px]"
            style={{
              left: c.x - 16,
              width: c.width + 32,
              top: 10,
              height: layout.height - 20,
              background: "linear-gradient(180deg, oklch(1 0 0 / 0.035), oklch(1 0 0 / 0.012))",
              border: "1px solid oklch(1 0 0 / 0.05)",
            }}
          >
            <div
              className="absolute inset-x-0 flex items-center justify-center gap-2"
              style={{ top: 14, height: HEADER_H - 22 }}
            >
              <span
                className="font-semibold uppercase"
                style={{ fontSize: 12, letterSpacing: "0.1em", color: "var(--muted-foreground)" }}
              >
                {c.label}
              </span>
              <span
                className="rounded-full font-mono"
                style={{
                  fontSize: 11,
                  padding: "1px 7px",
                  background: "oklch(1 0 0 / 0.06)",
                  color: "var(--subtle)",
                }}
              >
                {c.count}
              </span>
            </div>
          </div>
        ))}

        <svg
          width={layout.width}
          height={layout.height}
          className="pointer-events-none absolute top-0 left-0 overflow-visible"
        >
          <defs>
            {(
              [
                ["base", EDGE_NEUTRAL],
                ["out", EDGE_OUT],
                ["in", EDGE_IN],
                ["risk", EDGE_RISK],
                ["warn", EDGE_WARN],
              ] as const
            ).map(([name, color]) => (
              <marker
                key={name}
                id={`${uid}-${name}`}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="10"
                markerHeight="10"
                orient="auto"
                markerUnits="userSpaceOnUse"
              >
                <path d="M1,1.2 L9.2,5 L1,8.8 Z" fill={color} />
              </marker>
            ))}
          </defs>

          {drawn.map(({ e, a, b, route, state, i }) => {
            const highlighted = state === "out" || state === "in";
            const w = edgeWidth(e.weight);
            const text = e.label ?? (e.weight && e.weight > 1 ? `×${e.weight}` : null);
            return (
              <g
                key={i}
                opacity={
                  state === "dim" ? (e.risk === "high" ? 0.22 : 0.07) : highlighted ? 1 : 0.4
                }
              >
                <path
                  d={route.d}
                  fill="none"
                  stroke={colorFor(state, e.risk)}
                  strokeWidth={highlighted ? w + 0.8 : w}
                  strokeLinecap="round"
                  strokeDasharray={e.risk === "high" ? "6 5" : undefined}
                  markerEnd={`url(#${uid}-${markerName(state, e.risk)})`}
                  className={cn(e.risk === "high" && "dash-flow")}
                />
                {!staticView ? (
                  <path
                    d={route.d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={14}
                    style={{ pointerEvents: "stroke" }}
                  >
                    <title>{`${a.label} depends on ${b.label}${e.weight ? ` · ${e.weight} file-level import${e.weight === 1 ? "" : "s"}` : ""}${e.risk === "high" ? " · part of a circular dependency" : ""}`}</title>
                  </path>
                ) : null}
                {highlighted && text ? (
                  <g transform={`translate(${route.mid.x},${route.mid.y})`}>
                    <rect
                      x={-(text.length * 3.6 + 9)}
                      y={-11}
                      width={text.length * 7.2 + 18}
                      height={22}
                      rx={11}
                      fill="var(--surface-3)"
                      stroke={colorFor(state, e.risk)}
                      strokeWidth={1}
                    />
                    <text
                      textAnchor="middle"
                      dy="4.2"
                      className="font-mono"
                      fill="var(--foreground)"
                      style={{ fontSize: 12, fontWeight: 600 }}
                    >
                      {text}
                    </text>
                  </g>
                ) : null}
              </g>
            );
          })}
        </svg>

        {layout.nodes.filter(visible).map((n) => {
          const meta = KIND_META[n.kind];
          const Icon = meta.icon;
          const c = meta.color;
          const isSel = selectedId === n.id;
          const isFocus = focusId === n.id;
          const dimmed = focusId !== null && !isFocus && !connected.has(n.id);
          const tone =
            n.health == null
              ? null
              : n.health >= 80
                ? "var(--success)"
                : n.health >= 70
                  ? "var(--warning)"
                  : "var(--danger)";
          const lines = [
            `${n.files.toLocaleString()} ${n.files === 1 ? "file" : "files"}`,
            ...(n.loc != null ? [`${formatLoc(n.loc)} loc`] : []),
          ];
          return (
            <div
              key={n.id}
              role={staticView ? undefined : "button"}
              tabIndex={staticView ? undefined : 0}
              aria-label={`${n.label}, ${meta.label}`}
              onMouseEnter={staticView ? undefined : () => setHover(n.id)}
              onMouseLeave={staticView ? undefined : () => setHover(null)}
              onClick={
                staticView
                  ? undefined
                  : () => {
                      if ((drag.current?.moved ?? 0) > 4) return;
                      onSelect?.(n.id);
                    }
              }
              onKeyDown={
                staticView
                  ? undefined
                  : (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect?.(n.id);
                      }
                    }
              }
              className={cn(
                "absolute flex items-center gap-3 rounded-[14px] px-3 transition-[opacity,box-shadow,border-color,transform] duration-150 outline-none",
                !staticView && "cursor-pointer",
                !staticView && isFocus && "-translate-y-px",
              )}
              style={{
                left: n.x,
                top: n.y,
                width: NODE_W,
                height: NODE_H,
                opacity: dimmed ? 0.35 : 1,
                background: `linear-gradient(135deg, color-mix(in oklab, ${c} 17%, var(--surface)) 0%, var(--surface) 72%)`,
                border: `1px solid color-mix(in oklab, ${c} ${isSel ? 80 : isFocus ? 60 : 28}%, var(--border))`,
                boxShadow: isSel
                  ? `0 0 0 3px color-mix(in oklab, ${c} 30%, transparent), 0 14px 30px -12px color-mix(in oklab, ${c} 55%, black)`
                  : isFocus
                    ? `0 0 0 3px color-mix(in oklab, ${c} 16%, transparent), 0 12px 26px -12px rgba(0,0,0,0.85)`
                    : "0 8px 18px -10px rgba(0,0,0,0.85), inset 0 1px 0 oklch(1 0 0 / 0.05)",
              }}
            >
              <span
                className="grid shrink-0 place-items-center"
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 12,
                  background: `color-mix(in oklab, ${c} 20%, transparent)`,
                  border: `1px solid color-mix(in oklab, ${c} 38%, transparent)`,
                  color: c,
                }}
              >
                <Icon style={{ width: 20, height: 20 }} strokeWidth={1.9} />
              </span>
              <span className="min-w-0 flex-1">
                <span
                  className="block truncate text-foreground"
                  style={{
                    fontSize: 15,
                    fontWeight: 650,
                    letterSpacing: "-0.01em",
                    lineHeight: 1.25,
                  }}
                  title={n.label}
                >
                  {n.label}
                </span>
                <span
                  className="mt-0.5 block truncate font-mono text-subtle"
                  style={{ fontSize: 12, lineHeight: 1.3 }}
                >
                  {lines.join(" · ")}
                </span>
              </span>
              {cyclic.has(n.id) ? (
                <span
                  className="absolute grid place-items-center rounded-full"
                  style={{
                    top: -8,
                    right: -8,
                    width: 22,
                    height: 22,
                    background: "var(--danger)",
                    color: "white",
                    boxShadow: "0 0 0 3px var(--background)",
                  }}
                  title={
                    (n.circular_files ?? 0) > 0
                      ? `${n.circular_files} of ${n.files} files are in a circular dependency`
                      : "Part of a circular dependency"
                  }
                >
                  <AlertTriangle style={{ width: 12, height: 12 }} strokeWidth={2.4} />
                </span>
              ) : null}
              {tone ? (
                <span
                  className="absolute rounded-full"
                  style={{ right: 10, bottom: 10, width: 7, height: 7, background: tone }}
                />
              ) : null}
            </div>
          );
        })}
      </div>

      {interactive && !compact ? (
        // How to read the edges. The kind colours are keyed by the filter
        // chips above the graph (and by the dashboard's own legend), so they
        // are deliberately not repeated here.
        <div className="pointer-events-none absolute bottom-3 left-3 flex max-w-[calc(100%-9rem)] flex-col gap-1.5 rounded-lg border border-border bg-surface/90 px-3 py-2 backdrop-blur">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <svg width="24" height="8" aria-hidden>
                <path d="M0,4 H16" stroke={EDGE_NEUTRAL} strokeWidth="1.6" opacity="0.85" />
                <path d="M15,0.6 L23,4 L15,7.4 Z" fill={EDGE_NEUTRAL} opacity="0.85" />
              </svg>
              depends on
            </span>
            <span className="flex items-center gap-1.5">
              <svg width="24" height="8" aria-hidden>
                <path d="M0,4 H24" stroke={EDGE_NEUTRAL} strokeWidth="3.4" opacity="0.85" />
              </svg>
              thicker = more imports
            </span>
            <span className="flex items-center gap-1.5">
              <svg width="24" height="8" aria-hidden>
                <path d="M0,4 H24" stroke={EDGE_RISK} strokeWidth="1.8" strokeDasharray="4 3" />
              </svg>
              circular
            </span>
          </div>
          <p className="text-[11px] text-subtle">
            Hover a component to trace it: <span style={{ color: EDGE_OUT }}>orange</span> = what it
            depends on, <span style={{ color: EDGE_IN }}>blue</span> = what depends on it.
          </p>
        </div>
      ) : null}

      {interactive ? (
        <div className="absolute right-3 bottom-3 flex items-center gap-0.5 rounded-lg border border-border bg-surface/90 p-1 backdrop-blur">
          <button
            className="focus-ring grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            onClick={() => zoomBy(1 / 1.25)}
            aria-label="Zoom out"
          >
            <Minus className="size-3.5" />
          </button>
          <span className="w-11 text-center font-mono text-[11px] text-subtle">
            {Math.round(zoom * 100)}%
          </span>
          <button
            className="focus-ring grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            onClick={() => zoomBy(1.25)}
            aria-label="Zoom in"
          >
            <Plus className="size-3.5" />
          </button>
          <button
            className="focus-ring ml-0.5 grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            onClick={() => {
              autoFit.current = true;
              setSmooth(true);
              fit();
            }}
            aria-label="Fit graph to view"
            title="Fit to view"
          >
            <Maximize2 className="size-3.5" />
          </button>
        </div>
      ) : null}
    </div>
  );
}

export const nodeById = <T extends { id: string }>(nodes: T[], id: string): T | undefined =>
  nodes.find((n) => n.id === id);

// Re-exported for callers that size things relative to a node card.
export { NODE_H, NODE_W };
