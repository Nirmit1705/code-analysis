/** TanStack Query hooks for `server/app/api/v1/onboarding.py` (Phase 10.10).
 * Response shapes mirror `server/app/schemas/onboarding.py` field-for-field,
 * snake_case like every other query module here.
 *
 * Every number is a count the analysis recorded (files, lines, endpoints,
 * importers, complexity). There are no time estimates. A step's prose is the
 * existing Architecture insight (`useComponentInsight`), fetched when the step
 * is opened. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { ModuleKind } from "@/lib/queries/architecture";

/** `count` is: endpoints the file declares / distinct files importing it /
 * its average function complexity / its line count. */
export type ReadFirstReason = "endpoints" | "imported_by" | "complexity" | "largest";

export interface ReadFirstFile {
  path: string;
  reason: ReadFirstReason;
  count: number;
}

/** `count` is null for a README, else source files reached / distinct importers. */
export interface StartingPoint {
  path: string;
  reason: "readme" | "entry_point" | "imported_by";
  count: number | null;
}

export interface OnboardingStep {
  /** The Architecture component id; also the progress key. */
  id: string;
  title: string;
  kind: ModuleKind;
  file_count: number;
  loc: number;
  endpoint_count: number;
  sample_apis: string[];
  /** Distinct path components that import this one. */
  depended_on_by: number;
  /** Earlier steps this one imports directly, in path order. */
  builds_on: string[];
  /** Steps that import this one and are imported by it: read them together. */
  in_cycle_with: string[];
  read_first: ReadFirstFile[];
  done: boolean;
  completed_at: string | null;
}

/** Mirrors `OnboardingRead`. `analyzed: false` is the full "not analyzed yet" response. */
export interface OnboardingRead {
  analyzed: boolean;
  analysis_run_id: string | null;
  steps: OnboardingStep[];
  starting_points: StartingPoint[];
  completed_count: number;
  /** Components that qualified before the 10-step cap. */
  total_components: number;
}

export interface OnboardingProgressRead {
  step_id: string;
  done: boolean;
  completed_at: string | null;
  completed_count: number;
  step_count: number;
}

const onboardingKey = (repositoryId: string | null) => ["onboarding", repositoryId] as const;

/** `GET /repos/{id}/onboarding`. Idle while the active repository hasn't resolved. */
export function useOnboarding(repositoryId: string | null) {
  return useQuery({
    queryKey: onboardingKey(repositoryId),
    queryFn: () => apiFetch<OnboardingRead>(`/repos/${repositoryId}/onboarding`),
    enabled: repositoryId !== null,
    retry: false,
  });
}

function withStep(data: OnboardingRead, stepId: string, done: boolean, completedAt: string | null): OnboardingRead {
  const steps = data.steps.map((s) => (s.id === stepId ? { ...s, done, completed_at: completedAt } : s));
  return { ...data, steps, completed_count: steps.filter((s) => s.done).length };
}

/** `POST /repos/{id}/onboarding/progress`. Updates the cached path at once
 * (rolled back if the request fails), then stores the server's timestamp.
 * Call with `mutateAsync` (see server/CLAUDE.md, Phase 6.2). */
export function useSetStepDone(repositoryId: string | null) {
  const queryClient = useQueryClient();
  const key = onboardingKey(repositoryId);
  return useMutation({
    mutationFn: ({ stepId, done }: { stepId: string; done: boolean }) =>
      apiFetch<OnboardingProgressRead>(`/repos/${repositoryId}/onboarding/progress`, {
        method: "POST",
        body: { step_id: stepId, done },
      }),
    onMutate: async ({ stepId, done }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<OnboardingRead>(key);
      if (previous) queryClient.setQueryData(key, withStep(previous, stepId, done, done ? new Date().toISOString() : null));
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(key, context.previous);
    },
    onSuccess: (result) => {
      const current = queryClient.getQueryData<OnboardingRead>(key);
      if (current) queryClient.setQueryData(key, withStep(current, result.step_id, result.done, result.completed_at));
    },
  });
}
