/** TanStack Query hook for `server/app/api/v1/compare.py` (Phase 9.3,
 * already built and signed off) — wired into `app.compare.tsx` (Phase 9.4).
 * Response shape mirrors `server/app/schemas/compare.py` field-for-field,
 * snake_case throughout (this codebase's established convention). */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type DependencyRisk = "low" | "medium" | "high";

export interface FileChange {
  path: string;
  note: string;
}

export interface DependencyChange {
  edge: string;
  change: string;
  risk: DependencyRisk;
}

/** Mirrors `CompareRead`. No `modified_files` field — the redesigned
 * Compare screen only ever renders added/removed files and dependency
 * changes (`docs/PHASE_9_PLAN.md` design decisions #2-#4); `explanation`
 * is always `null` until Phase 9.7 wires up the AI narrative. */
export interface CompareRead {
  base_run_id: string;
  head_run_id: string;
  base_branch: string;
  base_commit_sha: string;
  head_branch: string;
  head_commit_sha: string;
  added_files: FileChange[];
  removed_files: FileChange[];
  changed_dependencies: DependencyChange[];
  added_apis: string[];
  removed_apis: string[];
  file_count_delta: number;
  module_count_delta: number;
  api_count_delta: number;
  dependency_count_delta: number;
  debt_score_delta: number;
  explanation: string | null;
}

/** `GET /repos/{id}/compare?base_run_id=&head_run_id=` — diffs two
 * already-completed analysis runs, never a live git patch. `null` for
 * either run id keeps this disabled (same gating idiom `useDependencies`
 * already uses for `repositoryId`) — there's no sensible partial request.
 * A `409`/`404` surfaces through `error` as a real `ApiError`, not a
 * fabricated empty payload — callers branch on `error.status` the same way
 * `app.assistant.tsx` already does for its own `ApiError` catch. */
export function useCompare(
  repositoryId: string | null,
  baseRunId: string | null,
  headRunId: string | null,
) {
  return useQuery({
    queryKey: ["compare", repositoryId, baseRunId, headRunId],
    queryFn: () =>
      apiFetch<CompareRead>(
        `/repos/${repositoryId}/compare?base_run_id=${baseRunId}&head_run_id=${headRunId}`,
      ),
    enabled: repositoryId !== null && baseRunId !== null && headRunId !== null,
    retry: false,
  });
}
