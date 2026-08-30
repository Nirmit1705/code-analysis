import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { graphEdges, graphNodes, type GraphNode } from "@/lib/repo-data";

const kindStyle: Record<GraphNode["kind"], { fill: string; stroke: string; glow: string }> = {
  frontend: { fill: "oklch(0.735 0.03 250 / 0.14)", stroke: "var(--info)", glow: "var(--info)" },
  gateway: { fill: "oklch(0.703 0.198 42 / 0.18)", stroke: "var(--violet)", glow: "var(--violet)" },
  service: { fill: "oklch(0.703 0.198 42 / 0.12)", stroke: "var(--violet-soft)", glow: "var(--violet)" },
  data: { fill: "oklch(0.72 0.16 152 / 0.14)", stroke: "var(--success)", glow: "var(--success)" },
  cache: { fill: "oklch(0.63 0.02 255 / 0.16)", stroke: "var(--cyan)", glow: "var(--cyan)" },
  external: { fill: "oklch(0.648 0.192 41 / 0.16)", stroke: "var(--magenta)", glow: "var(--magenta)" },
};

const NODE_W = 152;
const NODE_H = 52;

function edgePath(a: GraphNode, b: GraphNode) {
  const x1 = a.x + NODE_W;
  const y1 = a.y + NODE_H / 2;
  const x2 = b.x;
  const y2 = b.y + NODE_H / 2;
  const mid = (x1 + x2) / 2;
  return `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`;
}

export function ArchGraph({
  selectedId,
  onSelect,
  interactive = true,
  staticView = false,
  filterKinds,
  query = "",
  className,
  compact,
}: {
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  interactive?: boolean;
  staticView?: boolean;
  filterKinds?: GraphNode["kind"][];
  query?: string;
  className?: string;
  compact?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(compact ? (staticView ? 0.62 : 0.62) : 0.9);
  const [offset, setOffset] = useState({ x: compact ? (staticView ? 4 : 8) : 24, y: compact ? (staticView ? 8 : 6) : 12 });
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const stateRef = useRef({ zoom, offset });
  stateRef.current = { zoom, offset };

  const wheel = useCallback((e: WheelEvent) => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1);
    const { zoom: z, offset: o } = stateRef.current;
    const next = Math.min(2.2, Math.max(0.35, z * Math.exp(-dy * 0.0015)));
    const k = next / z;
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

  const visible = (n: GraphNode) =>
    (!filterKinds || filterKinds.length === 0 || filterKinds.includes(n.kind)) &&
    (query.trim() === "" || n.label.toLowerCase().includes(query.trim().toLowerCase()));

  const nodeMap = Object.fromEntries(graphNodes.map((n) => [n.id, n]));

  return (
    <div
      ref={ref}
      className={cn(
        "relative overflow-hidden rounded-lg border border-border bg-background grid-field",
        interactive && "cursor-grab active:cursor-grabbing",
        className,
      )}
      onPointerDown={(e) => {
        if (!interactive) return;
        drag.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y };
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        setOffset({
          x: drag.current.ox + (e.clientX - drag.current.x),
          y: drag.current.oy + (e.clientY - drag.current.y),
        });
      }}
      onPointerUp={() => (drag.current = null)}
      onPointerLeave={() => (drag.current = null)}
    >
      <div className="pointer-events-none absolute inset-0 ambient-panel" />
      <svg className="absolute inset-0 h-full w-full">
        <g transform={`translate(${offset.x},${offset.y}) scale(${zoom})`}>
          {graphEdges.map((e, i) => {
            const a = nodeMap[e.from];
            const b = nodeMap[e.to];
            if (!a || !b || !visible(a) || !visible(b)) return null;
            const active = !staticView && (hover === e.from || hover === e.to || selectedId === e.from || selectedId === e.to);
            const stroke =
              e.risk === "high" ? "var(--danger)" : e.risk === "med" ? "var(--warning)" : "var(--border-strong)";
            return (
              <g key={i}>
                <path
                  d={edgePath(a, b)}
                  fill="none"
                  stroke={active ? "var(--violet)" : stroke}
                  strokeWidth={active ? 1.6 : 1.1}
                  strokeDasharray={e.risk ? "4 4" : undefined}
                  className={cn(active && e.risk && "dash-flow")}
                  opacity={active ? 1 : 0.65}
                />
                {e.label && !compact ? (
                  <text
                    x={(a.x + NODE_W + b.x) / 2}
                    y={(a.y + b.y) / 2 + NODE_H / 2 - 6}
                    textAnchor="middle"
                    className="fill-subtle font-mono"
                    style={{ fontSize: 9 }}
                  >
                    {e.label}
                  </text>
                ) : null}
              </g>
            );
          })}

          {graphNodes.filter(visible).map((n) => {
            const s = kindStyle[n.kind];
            const isSel = selectedId === n.id;
            const tone = n.health >= 80 ? "var(--success)" : n.health >= 70 ? "var(--warning)" : "var(--danger)";
            return (
              <g
                key={n.id}
                transform={`translate(${n.x},${n.y})`}
                onMouseEnter={staticView ? undefined : () => setHover(n.id)}
                onMouseLeave={staticView ? undefined : () => setHover(null)}
                onClick={staticView ? undefined : () => onSelect?.(n.id)}
                className={cn(!staticView && "cursor-pointer")}
              >
                {isSel ? (
                  <rect
                    x={-5}
                    y={-5}
                    width={NODE_W + 10}
                    height={NODE_H + 10}
                    rx={12}
                    fill="none"
                    stroke={s.glow}
                    strokeWidth={1}
                    opacity={0.5}
                  />
                ) : null}
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={8}
                  fill={s.fill}
                  stroke={isSel || hover === n.id ? s.stroke : "var(--border-strong)"}
                  strokeWidth={1}
                  style={{ backdropFilter: "blur(2px)" }}
                />
                <rect width={3} height={NODE_H} rx={1.5} fill={s.stroke} opacity={0.9} />
                <text x={14} y={21} className="fill-foreground" style={{ fontSize: 12, fontWeight: 550 }}>
                  {n.label}
                </text>
                <text
                  x={14}
                  y={37}
                  className="fill-subtle"
                  style={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
                >
                  {n.files} files
                </text>
                <circle cx={NODE_W - 14} cy={NODE_H - 14} r={3} fill={tone} />
              </g>
            );
          })}
        </g>
      </svg>

      {interactive ? (
        <div className="absolute right-2 bottom-2 flex items-center gap-1 rounded-md border border-border bg-surface/90 p-1 backdrop-blur">
          <button
            className="focus-ring size-6 rounded-sm text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            onClick={() => setZoom((z) => Math.max(0.35, z / 1.25))}
            aria-label="Zoom out"
          >
            −
          </button>
          <span className="w-10 text-center font-mono text-[10px] text-subtle">
            {Math.round(zoom * 100)}%
          </span>
          <button
            className="focus-ring size-6 rounded-sm text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            onClick={() => setZoom((z) => Math.min(2.2, z * 1.25))}
            aria-label="Zoom in"
          >
            +
          </button>
          <button
            className="focus-ring ml-1 rounded-sm px-1.5 text-[10px] text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            onClick={() => {
              setZoom(0.9);
              setOffset({ x: 24, y: 12 });
            }}
          >
            reset
          </button>
        </div>
      ) : null}
    </div>
  );
}

export const nodeById = (id: string) => graphNodes.find((n) => n.id === id);
