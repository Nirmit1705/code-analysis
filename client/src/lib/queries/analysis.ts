/** TanStack Query + raw-`WebSocket` hooks for `server/app/api/v1/analysis.py`
 * and `server/app/api/ws/analysis_progress.py` (Phase 5.4, both already
 * built and signed off) — wired into `onboarding.tsx`'s Analysis step
 * (Phase 6.2). Response shapes mirror `server/app/schemas/analysis.py`. */
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { wsBaseUrl } from "@/lib/ws";

/** Mirrors `AnalysisRunStatus`'s non-terminal members, in the real declared
 * order (`server/app/models/analysis_run.py`). `AnalysisStep` renders its
 * stage checklist directly from this array — not a hand-picked subset —
 * so the UI never drifts from what the backend can actually report. As of
 * Phase 7.5, `indexing` is a real stage the Celery pipeline emits
 * (`app/rag/indexing.py::index_analysis_run`). `generating_insights` is
 * still reserved for Phase 8 — a real run today still jumps straight from
 * `indexing` to `completed`, so that one checklist row will visibly skip
 * from "pending" to "done" rather than ever showing "running". That's the
 * real pipeline's actual behavior today, not a frontend bug. */
export const ANALYSIS_RUN_STAGES = [
  "queued",
  "cloning",
  "parsing",
  "analyzing",
  "building_graph",
  "indexing",
  "generating_insights",
] as const;

export type AnalysisRunStatus = (typeof ANALYSIS_RUN_STAGES)[number] | "completed" | "failed";

/** Mirrors `AnalysisRunTriggerSource` (Phase 9.5): a user-initiated `POST`
 * vs. a GitHub `push` webhook delivery. */
export type AnalysisRunTriggerSource = "manual" | "webhook";

/** Mirrors `AnalysisRunRead` (`server/app/schemas/analysis.py`). */
export interface AnalysisRunRead {
  id: string;
  repository_id: string;
  branch: string;
  commit_sha: string;
  status: AnalysisRunStatus;
  trigger_source: AnalysisRunTriggerSource;
  started_at: string;
  completed_at: string | null;
  file_count: number;
  module_count: number;
  api_count: number;
  dependency_count: number;
  debt_score: number;
  error_message: string | null;
  created_at: string;
}

/** Mirrors `AnalysisProgressEvent` (`server/app/schemas/analysis.py`) — the
 * minimal `{id, status, timestamp, error_message}` shape relayed verbatim
 * over the WebSocket, distinct from `AnalysisRunRead`'s full row snapshot. */
export interface AnalysisProgressEvent {
  analysis_run_id: string;
  status: AnalysisRunStatus;
  timestamp: string;
  error_message: string | null;
}

/** `POST /repos/{id}/analysis-runs` — queues a run against the repository's
 * currently-selected branch (resolved server-side; there's no branch param
 * here) and returns the created `QUEUED` row immediately. The real pipeline
 * runs in a Celery worker; this call is not what tracks its progress. */
export function useCreateAnalysisRun(repositoryId: string) {
  return useMutation({
    mutationFn: () =>
      apiFetch<AnalysisRunRead>(`/repos/${repositoryId}/analysis-runs`, { method: "POST" }),
  });
}

/** `GET /analysis-runs/{id}` — REST snapshot/fallback alongside
 * `useAnalysisProgressSocket` below. Polls at a modest interval while the
 * run is still in flight, so progress keeps advancing even if the
 * WebSocket never connects (e.g. a proxy blocking `Upgrade`); stops once a
 * terminal status lands. Also the only source of the real final counts —
 * the WS event itself never carries them (see `AnalysisProgressEvent`). */
export function useAnalysisRun(analysisRunId: string | null) {
  return useQuery({
    queryKey: ["analysis-run", analysisRunId],
    queryFn: () => apiFetch<AnalysisRunRead>(`/analysis-runs/${analysisRunId}`),
    enabled: analysisRunId !== null,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "completed" || status === "failed" ? false : 4000;
    },
  });
}

export type SocketConnectionState = "idle" | "connecting" | "open" | "closed";

/** Raw `WebSocket` subscription to `WS /ws/analysis-runs/{id}` — a plain
 * hook, not a TanStack Query one, since this is a live subscription rather
 * than a request/response fetch. (Re)connects whenever `analysisRunId` or
 * `token` change; always closes the socket on cleanup. Auth is the `?token=`
 * query param the backend handler expects (a real `WebSocket` can't set an
 * `Authorization` header) — a snapshot of the access token at connect time,
 * not re-read if it's refreshed mid-connection (the REST fallback above
 * re-attaches a fresh token on every poll via `apiFetch`, so status keeps
 * advancing even if this socket's own token later goes stale). */
export function useAnalysisProgressSocket(
  analysisRunId: string | null,
  token: string | null,
): { lastEvent: AnalysisProgressEvent | null; connectionState: SocketConnectionState } {
  const [lastEvent, setLastEvent] = useState<AnalysisProgressEvent | null>(null);
  const [connectionState, setConnectionState] = useState<SocketConnectionState>("idle");

  useEffect(() => {
    setLastEvent(null);
    if (!analysisRunId || !token) {
      setConnectionState("idle");
      return;
    }

    setConnectionState("connecting");
    const socket = new WebSocket(
      `${wsBaseUrl()}/ws/analysis-runs/${analysisRunId}?token=${encodeURIComponent(token)}`,
    );

    socket.onopen = () => setConnectionState("open");
    socket.onmessage = (message) => {
      try {
        setLastEvent(JSON.parse(message.data as string) as AnalysisProgressEvent);
      } catch {
        // Malformed frame — ignore; the REST fallback above still advances.
      }
    };
    socket.onclose = () => setConnectionState("closed");
    socket.onerror = () => setConnectionState("closed");

    return () => socket.close();
  }, [analysisRunId, token]);

  return { lastEvent, connectionState };
}
