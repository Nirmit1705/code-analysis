/** Turns Phase 6.5's real `ArchitectureRead` payload into the props
 * `ArchGraph` (`components/arch-graph.tsx`) actually renders. The backend
 * deliberately never computes layout (`docs/PHASE_6_PLAN.md` 6.5's own
 * design decision — Architecture is built from real facts, not a
 * rendering engine), so this is a small, documented MVP layout the
 * frontend owns entirely: fixed column-x per kind, formalizing the mock's
 * own existing arrangement (`repo-data.ts`'s hand-placed `graphNodes`),
 * with nodes stacked top-to-bottom within a column, largest-by-file-count
 * first. Not a real graph-layout algorithm — no edge-crossing
 * minimization, no force simulation — matching this project's existing
 * preference for the simplest thing that reads clearly at realistic node
 * counts (Phase 6.5's own `MAX_MODULE_GROUPS = 24` cap). */
import type { ArchGraphEdge, ModuleKind } from "@/components/arch-graph";
import type { ArchitectureGraphEdge } from "@/lib/queries/architecture";

const NODE_W = 152;
const NODE_H = 52;
const COLUMN_GAP = 88;
const ROW_GAP = 66;
const MARGIN_X = 40;
const MARGIN_Y = 32;

const COLUMN_BY_KIND: Record<ModuleKind, number> = {
  frontend: 0,
  gateway: 1,
  service: 2,
  data: 3,
  cache: 3,
  external: 3,
};

export interface LayoutableNode {
  id: string;
  kind: ModuleKind;
  files: number;
}

/** Assigns `x`/`y` to every node — returns new objects (every original
 * field is preserved via `T`, so this can run on the full
 * `ArchitectureGraphNode[]` and the result still carries `loc`/`apis`/etc.
 * for a detail panel, not just the fields `ArchGraph` itself reads). */
export function layoutNodes<T extends LayoutableNode>(
  nodes: T[],
): (T & { x: number; y: number })[] {
  const byColumn = new Map<number, T[]>();
  for (const node of nodes) {
    const col = COLUMN_BY_KIND[node.kind];
    const bucket = byColumn.get(col) ?? [];
    bucket.push(node);
    byColumn.set(col, bucket);
  }

  const positioned: (T & { x: number; y: number })[] = [];
  for (const [col, bucket] of byColumn) {
    const sorted = [...bucket].sort((a, b) => b.files - a.files);
    sorted.forEach((node, row) => {
      positioned.push({
        ...node,
        x: MARGIN_X + col * (NODE_W + COLUMN_GAP),
        y: MARGIN_Y + row * (NODE_H + ROW_GAP),
      });
    });
  }
  return positioned;
}

/** Maps the backend's `{source, target, weight, circular}` edge shape
 * onto `ArchGraph`'s generic `{from, to, label?, risk?}` rendering props.
 * `circular` (Phase 4.2's real cycle detection) becomes the same
 * dashed-red "risk" styling the mock used for its own hand-picked risk
 * edges; a `weight > 1` becomes a plain "×N" label so the dedup Phase 6.5
 * already performs is visible rather than silently dropped. There is no
 * "med" risk tier in real data — the backend only ever reports a boolean. */
export function toArchGraphEdges(edges: ArchitectureGraphEdge[]): ArchGraphEdge[] {
  return edges.map((e) => ({
    from: e.source,
    to: e.target,
    ...(e.weight > 1 ? { label: `×${e.weight}` } : {}),
    ...(e.circular ? { risk: "high" as const } : {}),
  }));
}
