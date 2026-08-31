/**
 * Token storage for the FastAPI backend's bearer-JWT auth (`server/app/api/v1/auth.py`).
 *
 * The backend plan's original "access token in memory + httpOnly refresh
 * cookie" transport isn't what Phase 1 actually shipped — `/auth/register`,
 * `/auth/login`, `/auth/refresh`, and the GitHub callback's exchange
 * endpoint (Phase 2.5) all return both tokens as plain JSON, not a cookie.
 * Phase 1 is already signed off and out of scope to redo here, so this
 * stores both tokens in `localStorage` instead — functional, but every
 * script on the page can read them (XSS risk) and they survive a browser
 * restart. Fine for local dev/MVP; flagged here for reconsideration
 * alongside the Phase 2.3 JWT-in-URL fix once a real session-cookie
 * transport is worth building.
 */

const ACCESS_TOKEN_KEY = "substrate.access_token";
const REFRESH_TOKEN_KEY = "substrate.refresh_token";

export function getAccessToken(): string | null {
  try {
    return localStorage.getItem(ACCESS_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setTokens(accessToken: string, refreshToken: string): void {
  try {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  } catch {
    // localStorage unavailable (private browsing, disabled storage, SSR) —
    // the session just won't persist; callers still work for this request.
  }
}

export function clearTokens(): void {
  try {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  } catch {
    // ignore
  }
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}
