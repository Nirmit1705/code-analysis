/** TanStack Query hook for `GET /repos/{id}/analysis-runs` (Phase 5.4,
 * already built and signed off) — the repository's run history, newest
 * first. Named for its eventual primary consumer, `app.history.tsx`
 * (Phase 9.6), but created here in Phase 9.4 since Compare's and Impact's
 * run pickers need "pick from already-analyzed runs" first — per
 * `docs/PHASE_9_PLAN.md`'s own 9.6 steps, this file is the shared source
 * both sub-phases consume, not a second fetch of the same list.
 *
 * `AnalysisRunRead` is re-exported from `queries/analysis.ts` rather than
 * duplicated, matching `queries/repos.ts`'s own precedent of importing a
 * type across query modules (`TokenResponse` from `queries/auth`). */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { AnalysisRunRead } from "@/lib/queries/analysis";

export type { AnalysisRunRead };

/** `GET /repos/{id}/analysis-runs` — every run for this repository, newest
 * first (the backend's own ordering — `created_at desc`). `null` while the
 * active repository hasn't resolved yet. */
export function useAnalysisRuns(repositoryId: string | null) {
  return useQuery({
    queryKey: ["analysis-runs", repositoryId],
    queryFn: () => apiFetch<AnalysisRunRead[]>(`/repos/${repositoryId}/analysis-runs`),
    enabled: repositoryId !== null,
    retry: false,
  });
}

/** Only a `COMPLETED` run has persisted facts to diff or diff against — a
 * `QUEUED`/`FAILED`/in-progress one gets a real `409` from `compare.py`/
 * `impact.py`'s own `_completed_run_or_error` (Phase 9.3). Exported so both
 * pickers filter identically rather than each re-deriving the same
 * predicate. */
export function completedRuns(runs: AnalysisRunRead[] | undefined): AnalysisRunRead[] {
  return (runs ?? []).filter((r) => r.status === "completed");
}
