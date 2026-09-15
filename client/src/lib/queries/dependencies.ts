/** TanStack Query hook for `server/app/api/v1/dependencies.py` (Phase 6.9,
 * already built and signed off) — wired into `app.dependencies.tsx` (Phase
 * 6.10). Response shape mirrors `server/app/schemas/dependencies.py`
 * field-for-field, kept snake_case (no camelCase translation) matching this
 * codebase's actual established convention (`queries/dashboard.ts`,
 * `queries/architecture.ts`). */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type DependencySeverity = "low" | "medium" | "high" | "critical";

/** One real strongly-connected component (Phase 4.2's `CycleResult`) — not
 * a pairwise `{a, b}` shape, since a real cycle routinely spans more than
 * two files (HireSight's own real cycle spans 14). `severity` is derived
 * from cycle size, computed fresh at read time (see the backend's own
 * docstring) — not read back from a persisted debt-issue row. */
export interface CircularGroup {
  files: string[];
  edge_count: number;
  severity: DependencySeverity;
}

/** One module's fan-in/fan-out ranking (Phase 4.2's `CouplingScore`,
 * verbatim) — the full ranking, not gated by whether it crosses
 * `analysis/debt.py`'s own coupling threshold. No `severity` field: the
 * backend deliberately never invents one for every module regardless of
 * threshold-crossing (would be fabricated signal). */
export interface CoupledModule {
  module: string;
  fan_in: number;
  fan_out: number;
  score: number;
}

/** One persisted `DebtIssue` row of type `OUTDATED_DEPENDENCY` — the
 * *only* real "this package looks outdated" fact anywhere in this
 * codebase (a small hand-maintained package table, no CVE lookup, no
 * registry call). No `version`/`latest`/`cve` fields exist here at all —
 * `description` is the ready-made sentence the backend already built,
 * `used_by` is a real distinct-importing-file count. */
export interface ExternalDependencyFinding {
  name: string;
  description: string;
  severity: DependencySeverity;
  majors_behind: number;
  used_by: number;
}

/** Mirrors `DependenciesRead`. `analyzed: false` is the full "not analyzed
 * yet" response, matching every other Phase 6 screen endpoint's convention. */
export interface DependenciesRead {
  analyzed: boolean;
  analysis_run_id: string | null;
  circular: CircularGroup[];
  coupled: CoupledModule[];
  orphans: string[];
  external: ExternalDependencyFinding[];
}

/** `GET /repos/{id}/dependencies` — real re-derived circular/coupled/orphans
 * plus honestly-scoped external-package findings. `null` while the active
 * repository hasn't resolved yet. */
export function useDependencies(repositoryId: string | null) {
  return useQuery({
    queryKey: ["dependencies", repositoryId],
    queryFn: () => apiFetch<DependenciesRead>(`/repos/${repositoryId}/dependencies`),
    enabled: repositoryId !== null,
    retry: false,
  });
}
