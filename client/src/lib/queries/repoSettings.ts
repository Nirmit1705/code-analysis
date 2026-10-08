/** TanStack Query hooks for `server/app/api/v1/repository_settings.py`
 * (Phase 10.11): `GET`/`PATCH /repos/{id}/settings`, `DELETE /repos/{id}`
 * and `POST /repos/{id}/purge`. Types mirror
 * `server/app/schemas/repository_settings.py` field for field (snake_case). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { AnalysisRunRead } from "@/lib/queries/analysis";
import type { RepositoryAnalysisStatus } from "@/lib/queries/repos";

export type AnalysisMode = "on_push" | "manual";

/** Live answer from GitHub, never a stored flag (see the schema's comment). */
export type InstallationStatus = "connected" | "suspended" | "not_installed" | "reinstalled" | "unknown";

export interface LastAnalysis {
  analysis_run_id: string;
  branch: string;
  commit_sha: string;
  completed_at: string | null;
  file_count: number;
}

export interface RunInFlight {
  analysis_run_id: string;
  status: string;
  started_at: string;
}

export interface RepositorySettingsRead {
  repository_id: string;
  owner: string;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
  selected_branch: string | null;
  installation: InstallationStatus;
  analysis_status: RepositoryAnalysisStatus;
  last_analysis: LastAnalysis | null;
  analysis_run_count: number;
  /** Set while a run is live; disconnect and purge answer 409 meanwhile. */
  run_in_flight: RunInFlight | null;
  analysis_mode: AnalysisMode;
  ignore_patterns: string[];
  default_ignore_patterns: string[];
  max_ignore_patterns: number;
  max_ignore_pattern_length: number;
}

export interface RepositorySettingsUpdate {
  analysis_mode?: AnalysisMode;
  /** Raw lines; the server trims them, drops blank/`#` lines and validates. */
  ignore_patterns?: string[];
}

export interface RepositorySettingsUpdateRead {
  analysis_mode: AnalysisMode;
  ignore_patterns: string[];
}

export interface PurgeRead {
  status: "queued";
  deleted_run_count: number;
  analysis_run: AnalysisRunRead;
}

const settingsKey = (repositoryId: string | null) => ["repo-settings", repositoryId] as const;

/** Polls every 4 s while a run is in flight, so the locked actions unlock on
 * their own when it finishes. Each fetch also re-checks GitHub access. */
export function useRepoSettings(repositoryId: string | null) {
  return useQuery({
    queryKey: settingsKey(repositoryId),
    queryFn: () => apiFetch<RepositorySettingsRead>(`/repos/${repositoryId}/settings`),
    enabled: repositoryId !== null,
    retry: false,
    refetchInterval: (query) => (query.state.data?.run_in_flight ? 4000 : false),
  });
}

/** The response carries only the two editable fields; they are merged into
 * the cached settings so the screen updates without asking GitHub again. */
export function useUpdateRepoSettings(repositoryId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (update: RepositorySettingsUpdate) =>
      apiFetch<RepositorySettingsUpdateRead>(`/repos/${repositoryId}/settings`, { method: "PATCH", body: update }),
    onSuccess: (saved) => {
      queryClient.setQueryData<RepositorySettingsRead>(settingsKey(repositoryId), (prev) =>
        prev ? { ...prev, ...saved } : prev,
      );
    },
  });
}

/** `DELETE /repos/{id}` — 204, empty body. The caller decides where to go next. */
export function useDisconnectRepository(repositoryId: string) {
  return useMutation({
    mutationFn: () => apiFetch<null>(`/repos/${repositoryId}`, { method: "DELETE" }),
  });
}

/** `POST /repos/{id}/purge` — deletes every analysis and queues a fresh one.
 * Every cached screen for this repository is stale afterwards. */
export function usePurgeRepository(repositoryId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<PurgeRead>(`/repos/${repositoryId}/purge`, { method: "POST" }),
    onSuccess: () => {
      void queryClient.invalidateQueries();
    },
  });
}
