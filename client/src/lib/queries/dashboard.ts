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

export type AiInsightTone = "danger" | "warning" | "info";

/** Mirrors `AiInsightSchema` (Phase 8.4) — one real, LLM-generated insight
 * grounded in that run's real debt/coupling/doc-coverage facts, cached on
 * `AnalysisRun.ai_insights`. `tone` values match `Tone`'s own keys
 * (`client/src/components/ui-kit.tsx`) directly — no translation needed. */
export interface AiInsight {
  tone: AiInsightTone;
  title: string;
  body: string;
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
  /** Real, LLM-generated insights as of Phase 8.4 — `[]` is now a genuinely
   * honest outcome (insight generation failed non-fatally, or the LLM found
   * nothing worth surfacing for this run), not a placeholder rule anymore. */
  ai_insights: AiInsight[];
}

/** `GET /repos/{id}/dashboard` — the latest completed run's counts, computed
 * health metrics, real recent commits, and — as of Phase 8.4 — real
 * `ai_insights`. `null` while the active repository hasn't resolved yet. */
export function useDashboard(repositoryId: string | null) {
  return useQuery({
    queryKey: ["dashboard", repositoryId],
    queryFn: () => apiFetch<DashboardRead>(`/repos/${repositoryId}/dashboard`),
    enabled: repositoryId !== null,
    retry: false,
  });
}
