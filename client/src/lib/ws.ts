/**
 * Derives the backend's WebSocket base URL from the same `VITE_API_BASE_URL`
 * `api.ts` uses for REST calls (e.g. `http://localhost:8000/api/v1` becomes
 * `ws://localhost:8000`). The WS routes in `server/app/main.py` are
 * registered directly on `app`, not through `api_router` — they have no
 * `/api/v1` prefix (see `server/app/api/ws/analysis_progress.py`'s own
 * module docstring) — so it has to be stripped here, not just reused.
 */
export function wsBaseUrl(): string {
  const apiBase: string = import.meta.env["VITE_API_BASE_URL"] ?? "http://localhost:8000/api/v1";
  const withoutApiPrefix = apiBase.replace(/\/api\/v1\/?$/, "");
  return withoutApiPrefix.replace(/^http/, "ws");
}
