/** TanStack Query hooks for `server/app/api/v1/github.py`'s GitHub-connect,
 * repository, and branch endpoints (Phase 2.2–2.4) — wired into
 * `onboarding.tsx`'s GitHub/Repository/Branch steps (Phase 2.5). Response
 * shapes mirror the backend's Pydantic schemas in `server/app/schemas/{github,repository}.py`. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { setTokens } from "@/lib/auth";
import type { TokenResponse } from "@/lib/queries/auth";

export interface GitHubRepoSummary {
  github_repo_id: string;
  owner: string;
  name: string;
  full_name: string;
  private: boolean;
  language: string | null;
  default_branch: string;
  stargazers_count: number;
  size_kb: number;
  pushed_at: string | null;
}

export type RepositoryAnalysisStatus =
  "not_analyzed" | "queued" | "analyzing" | "completed" | "failed";

export interface RepositoryRead {
  id: string;
  owner: string;
  name: string;
  full_name: string;
  github_repo_id: string;
  default_branch: string;
  private: boolean;
  analysis_status: RepositoryAnalysisStatus;
  last_analyzed_commit_sha: string | null;
  created_at: string;
}

/** `RepositoryRead` plus one computed field, returned only by `GET /repos`
 * (Phase 6.1) — when this repository's most recently *finished* analysis run
 * completed, distinct from `analysis_status` (the *outcome* of the most
 * recent run, which may be a later FAILED one). `null` if it's never had a
 * completed run. */
export interface RepositoryListItem extends RepositoryRead {
  last_completed_analysis_at: string | null;
}

export interface BranchRead {
  id: string;
  name: string;
  latest_commit_sha: string;
  /** GitHub's actual default branch for the repo — a fact, not a user choice. */
  is_default: boolean;
  /** The branch currently selected to analyze. Independent of `is_default`. */
  is_selected: boolean;
}

/** `GET /auth/github/install` — a mutation (not a query) since it's fired by
 * an explicit "Authorize GitHub" click, not rendered reactively. Returns
 * JSON rather than a redirect (bearer-only auth, see the backend docstring)
 * — the caller does `window.location.href = install_url` itself. */
export function useGithubInstall() {
  return useMutation({
    mutationFn: () => apiFetch<{ install_url: string }>("/auth/github/install"),
  });
}

/** `POST /auth/github/exchange` — trades the callback's one-time
 * `exchange_code` (Phase 2.5) for a real token pair and stores it. */
export function useGithubExchange() {
  return useMutation({
    mutationFn: (code: string) =>
      apiFetch<TokenResponse>("/auth/github/exchange", {
        method: "POST",
        body: { code },
        auth: false,
      }),
    onSuccess: (tokens) => setTokens(tokens.access_token, tokens.refresh_token),
  });
}

/** `GET /github/repos` — live from GitHub via the connected installation,
 * not yet persisted. Only meaningful once GitHub is connected, so callers
 * pass `enabled`. */
export function useGithubRepos(enabled: boolean) {
  return useQuery({
    queryKey: ["github-repos"],
    queryFn: () => apiFetch<GitHubRepoSummary[]>("/github/repos"),
    enabled,
    retry: false,
  });
}

/** `POST /repos` — persists the user's chosen repo as a `Repository` row. */
export function useSelectRepo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (repo: GitHubRepoSummary) =>
      apiFetch<RepositoryRead>("/repos", {
        method: "POST",
        body: {
          github_repo_id: repo.github_repo_id,
          owner: repo.owner,
          name: repo.name,
          full_name: repo.full_name,
          default_branch: repo.default_branch,
          private: repo.private,
        },
      }),
    onSuccess: (repository) => {
      void queryClient.invalidateQueries({ queryKey: ["repo-branches", repository.id] });
      void queryClient.invalidateQueries({ queryKey: ["repositories"] });
    },
  });
}

/** `GET /repos` — the current user's already-connected repositories, newest
 * first. New in Phase 6.1: backs the "which repository is `/app/*` currently
 * showing" active-repository plumbing (`@/lib/active-repo`) — nothing before
 * this phase needed to list back what a user had already connected. */
export function useRepositories() {
  return useQuery({
    queryKey: ["repositories"],
    queryFn: () => apiFetch<RepositoryListItem[]>("/repos"),
    retry: false,
  });
}

/** `GET /repos/{id}/branches` — syncs live GitHub branches into `Branch`
 * rows and returns them, each tagged with both `is_default` (GitHub's
 * actual default) and `is_selected` (branch currently chosen to analyze). */
export function useRepoBranches(repositoryId: string | null) {
  return useQuery({
    queryKey: ["repo-branches", repositoryId],
    queryFn: () => apiFetch<BranchRead[]>(`/repos/${repositoryId}/branches`),
    enabled: repositoryId !== null,
    retry: false,
  });
}

/** `POST /repos/{id}/branches/{branch}/select` — marks a branch as the one
 * to analyze (`is_selected`), leaving `is_default` untouched. */
export function useSelectBranch(repositoryId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (branchName: string) =>
      apiFetch<BranchRead>(
        `/repos/${repositoryId}/branches/${encodeURIComponent(branchName)}/select`,
        { method: "POST" },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["repo-branches", repositoryId] });
    },
  });
}
