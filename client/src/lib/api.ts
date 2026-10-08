/**
 * Typed fetch client for the FastAPI backend (`server/`). Base URL from
 * `VITE_API_BASE_URL` (see `client/.env.example`). Every `client/src/lib/queries/*.ts`
 * module calls through here rather than using `fetch` directly.
 */
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "@/lib/auth";

const BASE_URL = import.meta.env["VITE_API_BASE_URL"] ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return `Request failed with status ${status}`;
}

// A `/auth/refresh` call is in flight for at most one request at a time —
// concurrent 401s all await the same promise instead of each minting their
// own refresh (and racing to overwrite the stored token pair).
let refreshInFlight: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;

  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${BASE_URL}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!response.ok) return false;
      const body = (await response.json()) as { access_token: string; refresh_token: string };
      setTokens(body.access_token, body.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

/** Only from screens that need a session (`/app/*`, `/onboarding`); the
 * sign-in pages themselves and the landing page are left alone. */
function redirectToSignIn(): void {
  if (typeof window === "undefined") return;
  const path = window.location.pathname;
  if (path === "/app" || path.startsWith("/app/") || path === "/onboarding") window.location.replace("/login");
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Skip attaching the Authorization header (e.g. register/login themselves). */
  auth?: boolean;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, auth = true, headers, ...rest } = options;

  const doFetch = async (): Promise<Response> => {
    const finalHeaders = new Headers(headers);
    if (body !== undefined) finalHeaders.set("Content-Type", "application/json");
    if (auth) {
      const token = getAccessToken();
      if (token) finalHeaders.set("Authorization", `Bearer ${token}`);
    }
    return fetch(`${BASE_URL}${path}`, {
      ...rest,
      headers: finalHeaders,
      body: body !== undefined ? JSON.stringify(body) : null,
    });
  };

  let response = await doFetch();

  if (response.status === 401 && auth && getRefreshToken()) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      response = await doFetch();
    } else {
      clearTokens();
    }
  }

  // Still refused after any refresh: the session is over (expired, or logged
  // out — possibly from another device). Signed-in screens send the user to
  // sign in again rather than showing a page of "failed to load" errors.
  if (response.status === 401 && auth) {
    clearTokens();
    redirectToSignIn();
  }

  if (!response.ok) {
    const responseBody = await parseBody(response);
    throw new ApiError(response.status, responseBody, errorMessage(responseBody, response.status));
  }

  return (await parseBody(response)) as T;
}
