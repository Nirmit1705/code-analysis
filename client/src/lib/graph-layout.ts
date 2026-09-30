/** Layout + edge routing for `ArchGraph` (`components/arch-graph.tsx`).
 *
 * The backend deliberately never computes layout (`docs/PHASE_6_PLAN.md`
 * 6.5's own design decision — Architecture is built from real facts, not a
 * rendering engine), so this module owns it entirely. It is a small layered
 * ("Sugiyama-lite") layout, chosen so a newcomer can read the graph:
 *
 *  1. **Layers → columns.** Nodes sit in architectural layers left to right
 *     (frontend → gateway → services → data/cache/external). Empty layers are
 *     dropped, so there are never blank columns.
 *  2. **Ordering.** Within a column, nodes are grouped by kind and then ordered
 *     by the barycenter of their neighbours (a few sweeps), which removes most
 *     avoidable edge crossings. Deterministic — same input, same picture.
 *  3. **Routing.** Every column shares one row grid, so the gaps *between* rows
 *     are free horizontal corridors in every column at once. Each edge leaves a
 *     node sideways into the gutter beside its column, travels vertically in
 *     that gutter, crosses over any columns in between along a corridor, and
 *     enters the target sideways. By construction an edge can never pass
 *     through a node. Parallel edges are given their own lanes (interval
 *     colouring) and endpoints are spread along a node's side, so edges never
 *     stack on one line or one pixel.
 *
 * Edge direction is "source depends on target" (source imports target). */
import type { ModuleKind } from "@/components/arch-graph";
import type { ArchitectureGraphEdge } from "@/lib/queries/architecture";
import type { ArchGraphEdge } from "@/components/arch-graph";

export const NODE_W = 216;
export const NODE_H = 64;
export const COLUMN_GAP = 136;
export const ROW_GAP = 60;
/** Room above the first row for the column titles. */
export const HEADER_H = 44;

/** Most nodes stacked in one column before a layer wraps to a second one. */
const MAX_ROWS = 6;
const PITCH = NODE_H + ROW_GAP;
/** Horizontal margin; must exceed half a gutter so the outermost gutters
 * (used by same-column and backward edges) stay inside the canvas. */
const MARGIN_X = COLUMN_GAP / 2 + 28;
const MARGIN_Y = HEADER_H + ROW_GAP / 2 + 24;
const MARGIN_BOTTOM = ROW_GAP / 2 + 28;
const CORNER_RADIUS = 9;
const PORT_PAD = 12;

const LAYER_BY_KIND: Record<ModuleKind, number> = {
  frontend: 0,
  gateway: 1,
  service: 2,
  data: 3,
  cache: 3,
  external: 3,
};

/** Order of kinds inside a shared column (only the last layer mixes kinds). */
const KIND_RANK: Record<ModuleKind, number> = {
  frontend: 0,
  gateway: 0,
  service: 0,
  data: 0,
  cache: 1,
  external: 2,
};

const SINGLE_KIND_LABEL: Record<ModuleKind, string> = {
  frontend: "Frontend",
  gateway: "Gateway / API",
  service: "Services",
  data: "Data",
  cache: "Cache",
  external: "External",
};

export interface LayoutableNode {
  id: string;
  kind: ModuleKind;
  files: number;
}

export interface LayoutEdge {
  from: string;
  to: string;
  weight?: number;
}

/** One architectural layer (which may span several sub-columns). */
export interface LayoutColumn {
  x: number;
  width: number;
  label: string;
  count: number;
}

export interface EdgeRoute {
  /** SVG path data, rounded corners, ends at the target's edge. */
  d: string;
  /** A point on the route's longest segment — where a label belongs. */
  mid: { x: number; y: number };
}

export interface GraphLayout<T> {
  nodes: (T & { x: number; y: number })[];
  columns: LayoutColumn[];
  /** Parallel to the `edges` argument; `null` where an endpoint is missing. */
  routes: (EdgeRoute | null)[];
  width: number;
  height: number;
}

interface Pt {
  x: number;
  y: number;
}

/** Greedy interval colouring: items whose [lo, hi] ranges overlap get
 * different lanes. Returns each item's lane and the lane count. */
function assignLanes(items: { lo: number; hi: number }[]): { lanes: number[]; count: number } {
  const order = items.map((_, i) => i).sort((a, b) => items[a]!.lo - items[b]!.lo);
  const laneEnds: number[] = [];
  const lanes: number[] = new Array(items.length).fill(0);
  for (const i of order) {
    const { lo, hi } = items[i]!;
    let lane = laneEnds.findIndex((end) => end + 4 < lo);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(hi);
    } else {
      laneEnds[lane] = hi;
    }
    lanes[i] = lane;
  }
  return { lanes, count: laneEnds.length };
}

/** Drops repeated points and middle points of straight runs. */
function simplify(points: Pt[]): Pt[] {
  const out: Pt[] = [];
  for (const p of points) {
    const last = out[out.length - 1];
    if (last && Math.abs(last.x - p.x) < 0.01 && Math.abs(last.y - p.y) < 0.01) continue;
    out.push(p);
  }
  return out.filter((p, i) => {
    if (i === 0 || i === out.length - 1) return true;
    const a = out[i - 1]!;
    const b = out[i + 1]!;
    const sameX = Math.abs(a.x - p.x) < 0.01 && Math.abs(p.x - b.x) < 0.01;
    const sameY = Math.abs(a.y - p.y) < 0.01 && Math.abs(p.y - b.y) < 0.01;
    return !(sameX || sameY);
  });
}

function roundedPath(points: Pt[], radius: number): string {
  const first = points[0]!;
  let d = `M${first.x},${first.y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const p0 = points[i - 1]!;
    const p1 = points[i]!;
    const p2 = points[i + 1]!;
    const l1 = Math.hypot(p1.x - p0.x, p1.y - p0.y);
    const l2 = Math.hypot(p2.x - p1.x, p2.y - p1.y);
    const r = Math.min(radius, l1 / 2, l2 / 2);
    const a = { x: p1.x - ((p1.x - p0.x) / l1) * r, y: p1.y - ((p1.y - p0.y) / l1) * r };
    const b = { x: p1.x + ((p2.x - p1.x) / l2) * r, y: p1.y + ((p2.y - p1.y) / l2) * r };
    d += ` L${a.x},${a.y} Q${p1.x},${p1.y} ${b.x},${b.y}`;
  }
  const last = points[points.length - 1]!;
  return `${d} L${last.x},${last.y}`;
}

function longestSegmentMid(points: Pt[]): Pt {
  let best = { len: -1, mid: points[0]! };
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i]!;
    const b = points[i + 1]!;
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    if (len > best.len) best = { len, mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
  }
  return best.mid;
}

export function layoutGraph<T extends LayoutableNode>(
  nodes: T[],
  edges: LayoutEdge[],
): GraphLayout<T> {
  if (nodes.length === 0) {
    return { nodes: [], columns: [], routes: edges.map(() => null), width: 0, height: 0 };
  }

  // 1. Layers -> consecutive columns. A layer with more than `MAX_ROWS`
  //    nodes wraps into several side-by-side sub-columns (dealt out in
  //    file-count order), so one crowded layer makes the graph wider — which
  //    the panel has room for — instead of so tall that everything shrinks.
  const layers = [...new Set(nodes.map((n) => LAYER_BY_KIND[n.kind]))].sort((a, b) => a - b);
  const columns: T[][] = [];
  const layerCols: number[][] = [];
  for (const layer of layers) {
    const members = nodes
      .filter((n) => LAYER_BY_KIND[n.kind] === layer)
      .sort((a, b) => KIND_RANK[a.kind] - KIND_RANK[b.kind] || b.files - a.files);
    const parts = Math.max(1, Math.ceil(members.length / MAX_ROWS));
    const first = columns.length;
    for (let s = 0; s < parts; s++) columns.push([]);
    members.forEach((n, i) => columns[first + (i % parts)]!.push(n));
    layerCols.push(Array.from({ length: parts }, (_, s) => first + s));
  }
  const colCount = columns.length;
  const colOf = new Map<string, number>();
  columns.forEach((column, c) => column.forEach((n) => colOf.set(n.id, c)));

  // 2. Barycenter ordering to cut crossings.
  const neighbours = new Map<string, string[]>();
  for (const e of edges) {
    if (e.from === e.to || !colOf.has(e.from) || !colOf.has(e.to)) continue;
    (neighbours.get(e.from) ?? neighbours.set(e.from, []).get(e.from)!).push(e.to);
    (neighbours.get(e.to) ?? neighbours.set(e.to, []).get(e.to)!).push(e.from);
  }
  const rowOf = new Map<string, number>();
  const refreshRows = () =>
    columns.forEach((column) => column.forEach((n, i) => rowOf.set(n.id, i)));
  refreshRows();
  const sweep = (c: number, forward: boolean) => {
    const column = columns[c]!;
    const bary = new Map<string, number>();
    column.forEach((n, i) => {
      const ref = (neighbours.get(n.id) ?? []).filter((id) => {
        const oc = colOf.get(id)!;
        return forward ? oc < c : oc > c;
      });
      bary.set(n.id, ref.length ? ref.reduce((s, id) => s + rowOf.get(id)!, 0) / ref.length : i);
    });
    column.sort(
      (a, b) =>
        KIND_RANK[a.kind] - KIND_RANK[b.kind] ||
        bary.get(a.id)! - bary.get(b.id)! ||
        b.files - a.files,
    );
    refreshRows();
  };
  for (let iter = 0; iter < 4; iter++) {
    for (let c = 1; c < colCount; c++) sweep(c, true);
    for (let c = colCount - 2; c >= 0; c--) sweep(c, false);
  }

  // 3. Coordinates on the shared grid.
  const maxRows = Math.max(...columns.map((c) => c.length));
  const colX = (c: number) => MARGIN_X + c * (NODE_W + COLUMN_GAP);
  const rowY = (r: number) => MARGIN_Y + r * PITCH;
  /** Centre x of the gutter to the right of column `k` (`-1` = left of col 0). */
  const gutterX = (k: number) => colX(k) + NODE_W + COLUMN_GAP / 2;
  /** y of the corridor in the gap above row `g` (`g = maxRows` = below the last). */
  const corridorY = (g: number) => rowY(g) - ROW_GAP / 2;

  const placed = new Map<string, T & { x: number; y: number }>();
  for (const [c, column] of columns.entries()) {
    column.forEach((n, r) => placed.set(n.id, { ...n, x: colX(c), y: rowY(r) }));
  }

  // 4. Ports: spread an edge's endpoint along the side of its node.
  interface Attach {
    edge: number;
    end: "a" | "b";
    otherCy: number;
  }
  const sideOf: { a: "L" | "R"; b: "L" | "R"; gs: number; ge: number }[] = [];
  // Keyed by node + side. The node id is stored, not parsed back out of the
  // key — real ids can contain ":" (e.g. external-service nodes).
  const attachments = new Map<string, { nodeId: string; list: Attach[] }>();
  edges.forEach((e, i) => {
    const a = placed.get(e.from);
    const b = placed.get(e.to);
    if (!a || !b || e.from === e.to) {
      sideOf.push({ a: "R", b: "L", gs: 0, ge: 0 });
      return;
    }
    const ca = colOf.get(e.from)!;
    const cb = colOf.get(e.to)!;
    const sides =
      ca < cb
        ? { a: "R" as const, b: "L" as const, gs: ca, ge: cb - 1 }
        : ca > cb
          ? { a: "L" as const, b: "R" as const, gs: ca - 1, ge: cb }
          : { a: "R" as const, b: "R" as const, gs: ca, ge: ca };
    sideOf.push(sides);
    const push = (nodeId: string, side: "L" | "R", att: Attach) => {
      const key = `${nodeId}\u0000${side}`;
      (attachments.get(key) ?? attachments.set(key, { nodeId, list: [] }).get(key)!).list.push(att);
    };
    push(e.from, sides.a, { edge: i, end: "a", otherCy: b.y + NODE_H / 2 });
    push(e.to, sides.b, { edge: i, end: "b", otherCy: a.y + NODE_H / 2 });
  });
  const portY = new Map<string, number>(); // `${edge}:${a|b}` -> y
  for (const { nodeId, list } of attachments.values()) {
    const node = placed.get(nodeId)!;
    list.sort((p, q) => p.otherCy - q.otherCy || p.edge - q.edge);
    list.forEach((att, i) => {
      const y = node.y + PORT_PAD + ((NODE_H - PORT_PAD * 2) * (i + 1)) / (list.length + 1);
      portY.set(`${att.edge}:${att.end}`, y);
    });
  }

  // 5. Route every edge through gutters and row corridors.
  interface Plan {
    edge: number;
    a: Pt;
    b: Pt;
    gs: number;
    ge: number;
    corridor: number | null; // corridor y, or null when gs === ge
    corridorGap: number;
  }
  const plans: Plan[] = [];
  edges.forEach((e, i) => {
    const a = placed.get(e.from);
    const b = placed.get(e.to);
    if (!a || !b || e.from === e.to) return;
    const s = sideOf[i]!;
    const pa: Pt = { x: s.a === "R" ? a.x + NODE_W : a.x, y: portY.get(`${i}:a`)! };
    const pb: Pt = { x: s.b === "R" ? b.x + NODE_W : b.x, y: portY.get(`${i}:b`)! };
    let corridor: number | null = null;
    let corridorGap = 0;
    if (s.gs !== s.ge) {
      const target = (pa.y + pb.y) / 2;
      corridorGap = Math.min(
        maxRows,
        Math.max(0, Math.round((target - MARGIN_Y + ROW_GAP / 2) / PITCH)),
      );
      corridor = corridorY(corridorGap);
    }
    plans.push({ edge: i, a: pa, b: pb, gs: s.gs, ge: s.ge, corridor, corridorGap });
  });

  // Vertical runs per gutter, horizontal runs per corridor -> lanes.
  interface VRun {
    plan: number;
    end: "start" | "end";
    gutter: number;
    lo: number;
    hi: number;
  }
  const vruns: VRun[] = [];
  plans.forEach((p, pi) => {
    if (p.corridor === null) {
      vruns.push({
        plan: pi,
        end: "start",
        gutter: p.gs,
        lo: Math.min(p.a.y, p.b.y),
        hi: Math.max(p.a.y, p.b.y),
      });
    } else {
      vruns.push({
        plan: pi,
        end: "start",
        gutter: p.gs,
        lo: Math.min(p.a.y, p.corridor),
        hi: Math.max(p.a.y, p.corridor),
      });
      vruns.push({
        plan: pi,
        end: "end",
        gutter: p.ge,
        lo: Math.min(p.b.y, p.corridor),
        hi: Math.max(p.b.y, p.corridor),
      });
    }
  });
  const laneX = new Map<string, number>(); // `${plan}:${start|end}` -> x
  const byGutter = new Map<number, VRun[]>();
  for (const r of vruns)
    (byGutter.get(r.gutter) ?? byGutter.set(r.gutter, []).get(r.gutter)!).push(r);
  for (const [gutter, runs] of byGutter) {
    const { lanes, count } = assignLanes(runs);
    const spacing = Math.min(10, (COLUMN_GAP - 48) / Math.max(1, count - 1));
    runs.forEach((r, i) =>
      laneX.set(`${r.plan}:${r.end}`, gutterX(gutter) + (lanes[i]! - (count - 1) / 2) * spacing),
    );
  }

  interface HRun {
    plan: number;
    gap: number;
    lo: number;
    hi: number;
  }
  const hruns: HRun[] = [];
  plans.forEach((p, pi) => {
    if (p.corridor === null) return;
    hruns.push({
      plan: pi,
      gap: p.corridorGap,
      lo: Math.min(gutterX(p.gs), gutterX(p.ge)),
      hi: Math.max(gutterX(p.gs), gutterX(p.ge)),
    });
  });
  const laneY = new Map<number, number>(); // plan -> y
  const byGap = new Map<number, HRun[]>();
  for (const r of hruns) (byGap.get(r.gap) ?? byGap.set(r.gap, []).get(r.gap)!).push(r);
  for (const [gap, runs] of byGap) {
    const { lanes, count } = assignLanes(runs);
    const spacing = Math.min(9, (ROW_GAP - 20) / Math.max(1, count - 1));
    runs.forEach((r, i) =>
      laneY.set(r.plan, corridorY(gap) + (lanes[i]! - (count - 1) / 2) * spacing),
    );
  }

  const routes: (EdgeRoute | null)[] = edges.map(() => null);
  plans.forEach((p, pi) => {
    const x1 = laneX.get(`${pi}:start`)!;
    const pts: Pt[] =
      p.corridor === null
        ? [p.a, { x: x1, y: p.a.y }, { x: x1, y: p.b.y }, p.b]
        : [
            p.a,
            { x: x1, y: p.a.y },
            { x: x1, y: laneY.get(pi)! },
            { x: laneX.get(`${pi}:end`)!, y: laneY.get(pi)! },
            { x: laneX.get(`${pi}:end`)!, y: p.b.y },
            p.b,
          ];
    const clean = simplify(pts);
    routes[p.edge] = { d: roundedPath(clean, CORNER_RADIUS), mid: longestSegmentMid(clean) };
  });

  const width = MARGIN_X * 2 + colCount * NODE_W + (colCount - 1) * COLUMN_GAP;
  const height = MARGIN_Y + maxRows * PITCH - ROW_GAP + MARGIN_BOTTOM;

  return {
    nodes: nodes.map((n) => placed.get(n.id)!),
    columns: layerCols.map((cols) => {
      const members = cols.flatMap((c) => columns[c]!);
      const kinds = new Set(members.map((n) => n.kind));
      const label = kinds.size === 1 ? SINGLE_KIND_LABEL[members[0]!.kind] : "Data & external";
      const x = colX(cols[0]!);
      return { x, width: colX(cols[cols.length - 1]!) + NODE_W - x, label, count: members.length };
    }),
    routes,
    width,
    height,
  };
}

/** Maps the backend's `{source, target, weight, circular}` edge shape onto
 * `ArchGraph`'s generic edge props. `circular` (Phase 4.2's real cycle
 * detection) becomes the dashed-red "risk" styling; `weight` (the deduped
 * number of file-to-file imports crossing the two nodes' boundary) drives the
 * line thickness and the "×N" label. There is no "med" risk tier in real data
 * — the backend only ever reports a boolean. */
export function toArchGraphEdges(edges: ArchitectureGraphEdge[]): ArchGraphEdge[] {
  return edges.map((e) => ({
    from: e.source,
    to: e.target,
    weight: e.weight,
    ...(e.circular ? { risk: "high" as const } : {}),
  }));
}
