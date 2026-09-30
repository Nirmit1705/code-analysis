/** TanStack Query hook for `server/app/api/v1/architecture.py` (Phase 6.5,
 * already built and signed off) — wired into `app.architecture.tsx` and
 * `app.index.tsx`'s embedded snapshot panel (Phase 6.6). Response shape
 * mirrors `server/app/schemas/architecture.py` field-for-field, kept
 * snake_case (no camelCase translation) matching this codebase's actual
 * established convention (`queries/dashboard.ts`, `queries/analysis.ts`). */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { ModuleKind } from "@/components/arch-graph";

export type { ModuleKind };

/** One module/component node. No `x`/`y` (the backend never computes
 * layout — `graph-layout.ts::layoutNodes` adds it) and no `health` (no
 * per-module health formula was ever specified, unlike Dashboard's fully
 * specified one — see `server/app/schemas/architecture.py`'s own
 * docstring for why that's a deliberate omission, not a gap). `insight` is
 * always `""` — the real per-component AI text comes from `useComponentInsight`
 * below, on demand, not from this field. */
export interface ArchitectureGraphNode {
  id: string;
  label: string;
  kind: ModuleKind;
  files: number;
  loc: number;
  key_files: string[];
  apis: string[];
  insight: string;
  /** How many of this node's files are in a real dependency cycle. Separate
   * from `ArchitectureGraphEdge.circular` because a cycle entirely *inside*
   * one node has no inter-node edge to carry a flag. */
  circular_files: number;
}

/** `weight` is the number of distinct file-pairs crossing these two
 * nodes' boundary (deduped, not a raw import-statement count — see
 * `analysis/graph.py`'s own coupling dedup). `circular` reuses Phase
 * 4.2's real cycle detection directly. */
export interface ArchitectureGraphEdge {
  source: string;
  target: string;
  weight: number;
  circular: boolean;
}

/** Mirrors `ArchitectureRead`. `analyzed: false` is the full "not analyzed
 * yet" response, matching `DashboardRead`'s own convention. */
export interface ArchitectureRead {
  analyzed: boolean;
  analysis_run_id: string | null;
  nodes: ArchitectureGraphNode[];
  edges: ArchitectureGraphEdge[];
  /** Same count as `DependenciesRead.circular.length`. */
  cycle_count: number;
}

/** "1 circular dependency (14 files)" / "no circular dependencies" — one
 * phrasing shared by every screen that summarises this, so Architecture,
 * Overview and Dependencies can't disagree in wording or number. */
export function circularSummary(data: ArchitectureRead): string {
  if (data.cycle_count === 0) return "no circular dependencies";
  const files = data.nodes.reduce((sum, n) => sum + n.circular_files, 0);
  return `${data.cycle_count} circular ${data.cycle_count === 1 ? "dependency" : "dependencies"} (${files} ${files === 1 ? "file" : "files"})`;
}

/** Mirrors `ComponentInsightRead`. `insight` is `null` when the model call
 * failed or returned nothing usable — the panel then says so instead of
 * showing anything made up. */
export interface ComponentInsightRead {
  node_id: string;
  insight: string | null;
}

/** `GET /repos/{id}/architecture/insight?node_id=...` — an AI insight for one
 * component, generated on demand (the backend caches repeats), so this only
 * fires once a node is actually selected. The analysis run id is part of the
 * key so a re-analysis refreshes it. Kept fresh for a long time: the text only
 * changes when the underlying analysis does. */
export function useComponentInsight(
  repositoryId: string | null,
  analysisRunId: string | null,
  nodeId: string | null,
) {
  return useQuery({
    queryKey: ["architecture-insight", repositoryId, analysisRunId, nodeId],
    queryFn: () =>
      apiFetch<ComponentInsightRead>(
        `/repos/${repositoryId}/architecture/insight?node_id=${encodeURIComponent(nodeId ?? "")}`,
      ),
    enabled: repositoryId !== null && analysisRunId !== null && nodeId !== null,
    retry: false,
    staleTime: 30 * 60_000,
  });
}

/** `GET /repos/{id}/architecture` — real module nodes/edges derived from
 * the latest completed run. `null` while the active repository hasn't
 * resolved yet. */
export function useArchitecture(repositoryId: string | null) {
  return useQuery({
    queryKey: ["architecture", repositoryId],
    queryFn: () => apiFetch<ArchitectureRead>(`/repos/${repositoryId}/architecture`),
    enabled: repositoryId !== null,
    retry: false,
  });
}
