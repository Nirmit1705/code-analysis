/** TanStack Query hook for `server/app/api/v1/impact.py` (Phase 9.3,
 * already built and signed off) — wired into `app.impact.tsx` (Phase 9.4).
 * Response shape mirrors `server/app/schemas/impact.py` field-for-field,
 * snake_case throughout (this codebase's established convention). */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type ImpactRisk = "LOW" | "MEDIUM" | "HIGH";

export interface ImpactedFile {
  path: string;
  why: string | null;
  hops: number | null;
}

/** Mirrors `ImpactRead`. `explanation` is always `null` until Phase 9.7
 * wires up the AI narrative. */
export interface ImpactRead {
  analysis_run_id: string;
  previous_analysis_run_id: string;
  branch: string;
  commit_sha: string;
  risk: ImpactRisk;
  score: number;
  direct: ImpactedFile[];
  indirect: ImpactedFile[];
  affected_apis: string[];
  explanation: string | null;
}

/** `GET /repos/{id}/impact/{analysis_run_id}` — resolves the run's
 * immediate predecessor on the same branch, diffs the two, and computes the
 * downstream blast radius. `null` `analysisRunId` keeps this disabled. A
 * `409` (no earlier completed run on this branch, or the target run isn't
 * `COMPLETED`) or `404` surfaces through `error` as a real `ApiError` —
 * same convention `useCompare` uses. */
export function useImpact(repositoryId: string | null, analysisRunId: string | null) {
  return useQuery({
    queryKey: ["impact", repositoryId, analysisRunId],
    queryFn: () => apiFetch<ImpactRead>(`/repos/${repositoryId}/impact/${analysisRunId}`),
    enabled: repositoryId !== null && analysisRunId !== null,
    retry: false,
  });
}
