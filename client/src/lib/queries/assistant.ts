/** TanStack Query hooks for `server/app/api/v1/assistant.py` (Phase 8.3,
 * already built and signed off) — wired into `app.assistant.tsx` (Phase 8.5).
 * Response shapes mirror `server/app/schemas/assistant.py` field-for-field,
 * snake_case (this codebase's established convention, matching
 * `queries/dashboard.ts`/`queries/dependencies.ts`). */
import { useMutation, useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

/** Mirrors `ReferenceSchema` — a citation attached to an `AssistantAnswer`,
 * always built server-side from the real retrieval results, never parsed
 * out of the LLM's own text (design decision #4). */
export interface Reference {
  file_path: string;
  symbol_name: string | null;
  line_start: number;
  line_end: number;
}

/** Mirrors `AssistantMessageResponse`. `insufficient_context` is a real,
 * honest signal (design decision #5) — when true, `references` is `[]` and
 * `answer` already explains there wasn't enough grounded context, not
 * something the UI needs to detect and reword itself. */
export interface AssistantAnswer {
  answer: string;
  references: Reference[];
  follow_up_questions: string[];
  insufficient_context: boolean;
}

/** `POST /repos/{id}/assistant/messages` — one real Groq call plus a real
 * retrieval pass per question (design decision #1: stateless, single-turn —
 * no conversation history is ever sent). Returns `409` (surfaced as a real
 * `ApiError`) if the repository has no completed analysis run yet. */
export function useSendAssistantMessage(repositoryId: string) {
  return useMutation({
    mutationFn: (question: string) =>
      apiFetch<AssistantAnswer>(`/repos/${repositoryId}/assistant/messages`, {
        method: "POST",
        body: { question },
      }),
  });
}

export interface SuggestedQuestionsRead {
  questions: string[];
}

/** `GET /repos/{id}/assistant/suggested-questions` — a deterministic
 * heuristic over already-persisted data, never LLM-generated (design
 * decision #6). `[]` for a not-yet-analyzed repository, not an error. */
export function useSuggestedQuestions(repositoryId: string | null) {
  return useQuery({
    queryKey: ["assistant-suggested-questions", repositoryId],
    queryFn: () =>
      apiFetch<SuggestedQuestionsRead>(`/repos/${repositoryId}/assistant/suggested-questions`),
    enabled: repositoryId !== null,
    retry: false,
  });
}
