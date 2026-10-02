/** TanStack Query hooks for `server/app/api/v1/tracer.py` (Phase 10.6) — wired
 * into `app.tracer.tsx`. Response shapes mirror `server/app/schemas/tracer.py`
 * field-for-field, kept snake_case (no camelCase translation) like every other
 * query module here.
 *
 * The trace is built from file imports (Known Issues #7/#42): a hop is a file a
 * request is expected to pass through, never a function call. The only function
 * name is the route's handler on the start hop (`fn`). */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type TraceLayer = "route" | "controller" | "service" | "repository" | "model" | "other";

export interface TraceStart {
  kind: "endpoint" | "file";
  /** `"GET /api/auth/google"` for an endpoint start, the file path for a file. */
  label: string;
  file_path: string;
  endpoint_id: string | null;
  method: string | null;
  path: string | null;
  /** Endpoints declared in the start file. The trace is per *file*, so each of
   * them gets this same trace. */
  endpoints_in_file: number;
}

export interface TraceExternal {
  service_id: string;
  label: string;
  /** `data` | `cache` | `external` | … from the Architecture service table. */
  kind: string;
  packages: string[];
  /** Pass-through files between the hop and the importer (last = importer);
   * empty when the hop imports the package itself. */
  via: string[];
}

export interface TraceHop {
  /** Breadth-first order — nest by `parent_index`, not by list position. */
  index: number;
  depth: number;
  parent_index: number | null;
  layer: TraceLayer;
  file_path: string;
  /** The route's handler name; start hop of an endpoint trace only. */
  fn: string | null;
  /** Pass-through (`other`) files between the parent hop and this one. */
  via: string[];
  /** This file's own direct internal imports, including ones not followed. */
  deps: string[];
  external_services: TraceExternal[];
  loc: number;
  symbols: string[];
  symbol_count: number;
}

/** Mirrors `TracerRead`. `analyzed: false` is the full "not analyzed yet"
 * response. */
export interface TracerRead {
  analyzed: boolean;
  analysis_run_id: string | null;
  commit_sha: string | null;
  branch: string | null;
  start: TraceStart | null;
  hops: TraceHop[];
  layers_reached: TraceLayer[];
  reaches_data: boolean;
  /** A depth or hop cap cut the trace off. */
  truncated: boolean;
  max_depth: number;
  max_hops: number;
}

export interface TraceNote {
  index: number;
  note: string;
}

/** `notes` is `null` when generation failed or produced nothing usable; a hop
 * the model didn't answer for simply has no entry. */
export interface TraceNotesRead {
  analyzed: boolean;
  analysis_run_id: string | null;
  notes: TraceNote[] | null;
}

/** Exactly one of the two, like the backend. Ids belong to the latest run, so
 * callers resolve them from a current list rather than storing them. */
export type TraceTarget = { endpointId: string } | { fileId: string };

function targetQuery(target: TraceTarget): string {
  return "endpointId" in target ? `endpoint_id=${target.endpointId}` : `file_id=${target.fileId}`;
}

function targetKey(target: TraceTarget | null): string | null {
  if (target === null) return null;
  return "endpointId" in target ? `endpoint:${target.endpointId}` : `file:${target.fileId}`;
}

/** `GET /repos/{id}/tracer` — deterministic and instant. `null` target (the
 * list it is resolved from hasn't loaded, or nothing is selected) keeps it idle. */
export function useTrace(repositoryId: string | null, target: TraceTarget | null) {
  return useQuery({
    queryKey: ["tracer", repositoryId, targetKey(target)],
    queryFn: () => apiFetch<TracerRead>(`/repos/${repositoryId}/tracer?${targetQuery(target!)}`),
    enabled: repositoryId !== null && target !== null,
    retry: false,
  });
}

/** `GET /repos/{id}/tracer/notes` — a model call, so it only fires once the
 * structure has rendered (`enabled`). Real notes are kept for the session; a
 * `null` is stale immediately so a retry asks again. */
export function useTraceNotes(repositoryId: string | null, target: TraceTarget | null, enabled: boolean) {
  return useQuery({
    queryKey: ["tracer", repositoryId, targetKey(target), "notes"],
    queryFn: () => apiFetch<TraceNotesRead>(`/repos/${repositoryId}/tracer/notes?${targetQuery(target!)}`),
    enabled: repositoryId !== null && target !== null && enabled,
    retry: false,
    staleTime: (query) => (query.state.data?.notes ? Infinity : 0),
  });
}

export const LAYER_LABEL: Record<TraceLayer, string> = {
  route: "Route",
  controller: "Controller",
  service: "Service",
  repository: "Repository",
  model: "Model",
  other: "Other",
};

/** The five layers a request path moves through, outermost first (`other` is
 * a pass-through bucket, not a stage). */
export const LAYER_ORDER: readonly TraceLayer[] = ["route", "controller", "service", "repository", "model"];
