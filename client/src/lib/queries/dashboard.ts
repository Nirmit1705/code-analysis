/** TanStack Query hook for `server/app/api/v1/dashboard.py` (Phase 6.3,
 * already built and signed off) — wired into `app.index.tsx`'s Dashboard
 * screen (Phase 6.4). Response shape mirrors `server/app/schemas/dashboard.py`. */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type HealthMetricKey = "architecture" | "quality" | "dependency" | "docs" | "debt";
export type HealthTone = "success" | "warning" | "danger";
export type CommitImpact = "high" | "medium" | "low";

export interface RepoStats {
  files: number;
  /** Total LOC across every ingested file, not just the ones in `languages`
   * below — see that field's own docstring for why the two totals differ. */
  loc: number;
  modules: number;
  apis: number;
  dependencies: number;
  contributors: number;
}

/** One `File.language` group's share of the repository, by LOC. Only
 * covers files Phase 3 could classify — a real repo can have a large gap
 * between `languages[].loc` summed and `RepoStats.loc` (lockfiles,
 * markdown, config, etc. count toward the latter but never appear here).
 * No `color` field — the backend deliberately doesn't assign display
 * colors (matches Architecture's own stance), so this module picks them. */
export interface LanguageBreakdown {
  name: string;
  loc: number;
  pct: number;
}

export interface HealthMetric {
  key: HealthMetricKey;
  label: string;
  value: number;
  delta: number;
  tone: HealthTone;
  note: string;
}

export interface RecentCommit {
  sha: string;
  message: string;
  author: string;
  components: string[];
  impact: CommitImpact;
  date: string;
}

/** Mirrors `DashboardRead` (`server/app/schemas/dashboard.py`). `analyzed:
 * false` is the full "not analyzed yet" response — every other field is
 * `null`/`[]` rather than a broken empty aggregate. */
export interface DashboardRead {
  analyzed: boolean;
  analysis_run_id: string | null;
  branch: string | null;
  commit_sha: string | null;
  completed_at: string | null;
  stats: RepoStats | null;
  languages: LanguageBreakdown[];
  health_metrics: HealthMetric[];
  recent_commits: RecentCommit[];
  /** Always `[]` until Phase 8 exists to generate real insights — a hard
   * rule on the backend, not a placeholder that happens to be empty today. */
  ai_insights: string[];
}

/** `GET /repos/{id}/dashboard` — the latest completed run's counts, computed
 * health metrics, real recent commits, and the always-empty `ai_insights`
 * placeholder. `null` while the active repository hasn't resolved yet. */
export function useDashboard(repositoryId: string | null) {
  return useQuery({
    queryKey: ["dashboard", repositoryId],
    queryFn: () => apiFetch<DashboardRead>(`/repos/${repositoryId}/dashboard`),
    enabled: repositoryId !== null,
    retry: false,
  });
}
