/** TanStack Query hooks for `server/app/api/v1/api_reference.py` (Phase 10.1)
 * — wired into `app.api.tsx`. Response shapes mirror
 * `server/app/schemas/api_reference.py` field-for-field, kept snake_case (no
 * camelCase translation) like every other query module here.
 *
 * Deliberately no calls/body/response/auth fields: none is extracted by the
 * backend (docs/PHASE_10_PLAN.md decision #2), so none is invented. */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type ApiFramework = "express" | "spring_boot" | "fastapi" | "flask";

/** `resolved: true` -> the extractor linked the route to a real symbol, so the
 * three location fields locate the handler function. `resolved: false` -> only
 * the raw reference text survived (`<inline>` when it isn't a named reference)
 * and the location fields are `null` — there is nothing honest to point at. */
export interface ApiHandler {
  name: string;
  resolved: boolean;
  file_path: string | null;
  line_start: number | null;
  line_end: number | null;
}

/** `type` only when the path syntax declares one (Flask `<int:id>`). */
export interface ApiPathParam {
  name: string;
  type: string | null;
}

export interface ApiEndpointRead {
  id: string;
  method: string;
  path: string;
  framework: ApiFramework;
  /** The Architecture group label for the declaring file (e.g. "API Routes"). */
  service: string;
  /** Resource prefix derived from the path (`/api/auth`) — the list's grouping. */
  group: string;
  module: string;
  file_path: string;
  /** First/last line of the route declaration; `null` for runs analyzed before
   * those were recorded. */
  line: number | null;
  line_end: number | null;
  handler: ApiHandler | null;
  path_params: ApiPathParam[];
  /** Names between the path and the handler on the route call itself, in
   * order. `null` = not recorded (non-Express, or an older run); `[]` = none.
   * Never an auth claim; `app.use(...)` middleware is not included. */
  middleware: string[] | null;
  /** `"new"` = no endpoint with this method+path in the baseline run. */
  change: "new" | null;
}

export interface ApiRemovedEndpoint {
  method: string;
  path: string;
  file_path: string;
}

/** The earlier completed run (same branch, different commit) that
 * `change`/`removed` are measured against. */
export interface ApiBaseline {
  analysis_run_id: string;
  commit_sha: string;
  completed_at: string;
}

/** Mirrors `ApiReferenceRead`. `analyzed: false` is the full "not analyzed
 * yet" response. */
export interface ApiReferenceRead {
  analyzed: boolean;
  analysis_run_id: string | null;
  commit_sha: string | null;
  branch: string | null;
  endpoints: ApiEndpointRead[];
  baseline: ApiBaseline | null;
  new_count: number;
  removed: ApiRemovedEndpoint[];
}

/** Real source lines at the analyzed commit (1-indexed, inclusive). */
export interface CodeSnippet {
  file_path: string;
  start_line: number;
  end_line: number;
  code: string;
  truncated: boolean;
}

export interface TouchedFile {
  file_path: string;
  service: string;
}

export interface TouchedService {
  label: string;
  kind: string;
  packages: string[];
}

export interface TouchedDebt {
  issue_type: string;
  severity: string;
  description: string;
}

/** File-grain facts about the file declaring the route — shared by every
 * route in that file, not specific to one endpoint. */
export interface ApiTouches {
  route_file: string;
  routes_in_file: number;
  loc: number;
  complexity_score: number | null;
  in_cycle: boolean;
  cycle_size: number | null;
  imports: TouchedFile[];
  external: TouchedService[];
  used_by: TouchedFile[];
  used_by_total: number;
  debt: TouchedDebt[];
}

export interface ApiEndpointDetailRead {
  id: string;
  declaration: CodeSnippet | null;
  /** Only when the handler resolved to a symbol outside the declaration span. */
  handler_code: CodeSnippet | null;
  /** Why there's no code (GitHub unreachable, run predates line capture). */
  source_note: string | null;
  touches: ApiTouches;
}

/** `GET /repos/{id}/apis`. `null` while the active repository hasn't
 * resolved yet. */
export function useApiEndpoints(repositoryId: string | null) {
  return useQuery({
    queryKey: ["api-reference", repositoryId],
    queryFn: () => apiFetch<ApiReferenceRead>(`/repos/${repositoryId}/apis`),
    enabled: repositoryId !== null,
    retry: false,
  });
}

/** `GET /repos/{id}/apis/{endpoint_id}` — fetched only for the selected
 * endpoint (it reads source from GitHub). */
export function useApiEndpointDetail(repositoryId: string | null, endpointId: string | null) {
  return useQuery({
    queryKey: ["api-reference", repositoryId, "detail", endpointId],
    queryFn: () => apiFetch<ApiEndpointDetailRead>(`/repos/${repositoryId}/apis/${endpointId}`),
    enabled: repositoryId !== null && endpointId !== null,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

export const FRAMEWORK_LABEL: Record<ApiFramework, string> = {
  express: "Express",
  spring_boot: "Spring Boot",
  fastapi: "FastAPI",
  flask: "Flask",
};
