/** TanStack Query hook for `GET /repos/{id}/analysis-runs` (Phase 5.4,
 * already built and signed off) — the repository's run history, newest
 * first. Named for its primary consumer, `app.history.tsx` (Phase 9.6), but
 * created in Phase 9.4 since Compare's and Impact's run pickers needed
 * "pick from already-analyzed runs" first — this file is the shared source
 * all three screens consume, not a second fetch of the same list.
 *
 * `AnalysisRunRead` is re-exported from `queries/analysis.ts` rather than
 * duplicated, matching `queries/repos.ts`'s own precedent of importing a
 * type across query modules (`TokenResponse` from `queries/auth`). */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { AnalysisRunRead } from "@/lib/queries/analysis";

export type { AnalysisRunRead };

/** A run that hasn't reached `completed`/`failed` yet. */
export function isRunInFlight(run: AnalysisRunRead): boolean {
  return run.status !== "completed" && run.status !== "failed";
}

/** `GET /repos/{id}/analysis-runs` — every run for this repository, newest
 * first (the backend's own ordering — `created_at desc`). `null` while the
 * active repository hasn't resolved yet. Polls while any run is still in
 * flight (a manual re-analysis from History, or a push-triggered one), so a
 * row's status advances live and the list settles on its own once
 * everything is terminal. */
export function useAnalysisRuns(repositoryId: string | null) {
  return useQuery({
    queryKey: ["analysis-runs", repositoryId],
    queryFn: () => apiFetch<AnalysisRunRead[]>(`/repos/${repositoryId}/analysis-runs`),
    enabled: repositoryId !== null,
    retry: false,
    refetchInterval: (query) => (query.state.data?.some(isRunInFlight) ? 4000 : false),
    // Keep polling in a background tab too — an analysis takes minutes, and
    // otherwise a user who switches away sees a stale `queued`/`indexing`
    // row until they refocus. Only ever active while a run is in flight.
    refetchIntervalInBackground: true,
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
