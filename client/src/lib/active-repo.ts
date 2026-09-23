/**
 * "Which repository is `/app/*` currently showing" — a single active
 * repository per browser, tracked in `localStorage` (mirrors `auth.ts`'s
 * token-storage pattern). Phase 6.1: nothing before this phase needed this
 * concept at all — every mock `/app/*` screen just imported hardcoded data
 * from `repo-data.ts` with no repo id anywhere.
 *
 * Deliberately a single global choice, not a `/app/$repositoryId/...` URL
 * param — the app has no multi-repo-switching UI yet (the `TopBar` dropdown
 * stays a non-functional visual placeholder), so a URL restructuring would
 * be scope beyond what any Phase 6 screen actually needs. Revisit if
 * multi-repo support is ever prioritized.
 */
import { useEffect, useState } from "react";

import { useRepositories } from "@/lib/queries/repos";

const ACTIVE_REPOSITORY_ID_KEY = "substrate.active_repository_id";

export function getActiveRepositoryId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_REPOSITORY_ID_KEY);
  } catch {
    return null;
  }
}

export function setActiveRepositoryId(repositoryId: string): void {
  try {
    localStorage.setItem(ACTIVE_REPOSITORY_ID_KEY, repositoryId);
  } catch {
    // localStorage unavailable (private browsing, disabled storage, SSR) —
    // the choice just won't persist across reloads.
  }
}

export function clearActiveRepositoryId(): void {
  try {
    localStorage.removeItem(ACTIVE_REPOSITORY_ID_KEY);
  } catch {
    // ignore
  }
}

/**
 * Resolves the active repository id: whatever's already stored, or — on a
 * fresh browser / cleared storage — the user's most-recently-connected
 * repository (`GET /repos` is already newest-first), persisting that choice
 * so subsequent loads don't need the fallback again.
 *
 * Returns `null` while nothing is resolvable yet: no stored id and either
 * the repositories list hasn't loaded (or errored — e.g. not signed in) or
 * the user has no connected repositories at all. Callers should treat `null`
 * as "nothing to show yet", not necessarily an error.
 *
 * State always *starts* `null`, even when `localStorage` already has a
 * value — every `/app/*` route is SSR'd (TanStack Start) and the server has
 * no `localStorage`, so a lazy initializer that read it synchronously here
 * would make the client's first render disagree with the server's on every
 * hard reload/direct nav to a repo-scoped screen for any returning user,
 * a guaranteed React hydration-mismatch (confirmed live during Phase 8.6
 * verification). The real stored id is only read inside the effect below,
 * which runs client-only, after hydration — one extra render, not a
 * mismatch.
 */
export function useActiveRepositoryId(): string | null {
  const [id, setId] = useState<string | null>(null);
  const repositories = useRepositories();

  useEffect(() => {
    if (id !== null) return;
    const stored = getActiveRepositoryId();
    if (stored) {
      setId(stored);
      return;
    }
    const fallback = repositories.data?.[0]?.id;
    if (fallback) {
      setActiveRepositoryId(fallback);
      setId(fallback);
    }
  }, [id, repositories.data]);

  return id;
}
