/** TanStack Query hook for `server/app/api/v1/debt.py` (Phase 10.2) — wired
 * into `app.debt.tsx` (Phase 10.3). Response shapes mirror
 * `server/app/schemas/debt.py` field-for-field, kept snake_case (no camelCase
 * translation) like every other query module here.
 *
 * Everything is a real persisted finding or a number derived from them. There
 * is deliberately no 0-100 category score, CVE count, "resolved in 30 days" or
 * duplication category: none is computed anywhere (docs/PHASE_10_PLAN.md
 * decision #2). `debt_score` is the severity-weighted sum of the findings. */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type DebtSeverity = "low" | "medium" | "high" | "critical";
export type DebtIssueType =
  | "high_complexity"
  | "large_file"
  | "large_module"
  | "excessive_coupling"
  | "circular_dependency"
  | "missing_documentation"
  | "outdated_dependency";

export interface SeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

/** One of the seven real debt types — all seven are always returned (a type
 * with no findings has `count: 0`, `worst_severity: null`). `weighted_score`
 * is this category's share of `debt_score` (severity weights 1/2/3/5). */
export interface DebtCategory {
  issue_type: DebtIssueType;
  label: string;
  count: number;
  worst_severity: DebtSeverity | null;
  weighted_score: number;
}

export interface DebtIssueRead {
  id: string;
  /** Type prefix + 6 hex of the row id. Stable for a row, not across analysis runs. */
  display_id: string;
  severity: DebtSeverity;
  issue_type: DebtIssueType;
  category: string;
  title: string;
  file_path: string | null;
  module: string | null;
  metric_value: number;
  /** The rule's own sentence, shown as-is. */
  description: string;
  /** Fixed per-type copy about what this *class* of debt costs — not AI. */
  why: string;
}

export interface DebtTrendPoint {
  analysis_run_id: string;
  commit_sha: string;
  completed_at: string;
  debt_score: number;
}

/** The run the deltas are measured against: newest completed run on the same
 * branch at a *different commit*. */
export interface DebtPrevious {
  analysis_run_id: string;
  commit_sha: string;
  completed_at: string;
}

/** Mirrors `DebtRead`. `analyzed: false` is the full "not analyzed yet" response. */
export interface DebtRead {
  analyzed: boolean;
  analysis_run_id: string | null;
  branch: string | null;
  commit_sha: string | null;
  debt_score: number;
  previous: DebtPrevious | null;
  previous_debt_score: number | null;
  score_delta: number | null;
  total_issues: number;
  severity_counts: SeverityCounts;
  categories: DebtCategory[];
  /** Most-severe first, capped at 500 (`truncated`); counts above are over all. */
  issues: DebtIssueRead[];
  truncated: boolean;
  /** Distinct finding keys now-but-not-before / before-but-not-now. `null` when
   * there is no `previous`. Approximate: findings sharing a key count once. */
  new_since_previous: number | null;
  resolved_since_previous: number | null;
  trend_branch: string | null;
  trend: DebtTrendPoint[];
}

/** `GET /repos/{id}/debt`. `null` while the active repository hasn't resolved yet. */
export function useDebt(repositoryId: string | null) {
  return useQuery({
    queryKey: ["debt", repositoryId],
    queryFn: () => apiFetch<DebtRead>(`/repos/${repositoryId}/debt`),
    enabled: repositoryId !== null,
    retry: false,
  });
}

/** Severity weights `debt_score` is summed from — shown in the header so the
 * number is explainable. Duplicated from the backend (`analysis/debt.py`). */
export const SEVERITY_WEIGHT: Record<DebtSeverity, number> = { critical: 5, high: 3, medium: 2, low: 1 };

export const SEVERITIES: DebtSeverity[] = ["critical", "high", "medium", "low"];
