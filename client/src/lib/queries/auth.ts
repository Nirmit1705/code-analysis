/** TanStack Query hooks for `server/app/api/v1/auth.py` (Phase 1) — the
 * minimal real login/signup wiring Phase 2.5 needs so the onboarding
 * wizard's GitHub steps have an authenticated user to run as. */
import { useMutation, useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { clearActiveRepositoryId } from "@/lib/active-repo";
import { clearTokens, getRefreshToken, setTokens } from "@/lib/auth";

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

async function storeTokens(tokens: TokenResponse): Promise<TokenResponse> {
  setTokens(tokens.access_token, tokens.refresh_token);
  return tokens;
}

export function useRegister() {
  return useMutation({
    mutationFn: (payload: { email: string; password: string }) =>
      apiFetch<TokenResponse>("/auth/register", { method: "POST", body: payload, auth: false }),
    onSuccess: storeTokens,
  });
}

export function useLogin() {
  return useMutation({
    mutationFn: (payload: { email: string; password: string }) =>
      apiFetch<TokenResponse>("/auth/login", { method: "POST", body: payload, auth: false }),
    onSuccess: storeTokens,
  });
}

/** `GET /auth/me` (Phase 1's `UserRead`) — the signed-in user. Used by the
 * Profile screen and the top bar's avatar (Phase 10.12). */
export interface UserRead {
  id: string;
  email: string;
  created_at: string;
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<UserRead>("/auth/me"),
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

/** Ends this browser's session on the server (`POST /auth/logout`): every
 * token of the session — including any copy of it elsewhere — is refused from
 * then on. Returns `false` when the server couldn't be reached, so the caller
 * can say so instead of pretending the session ended. */
export async function endSession(): Promise<boolean> {
  try {
    await apiFetch<null>("/auth/logout", { method: "POST", body: { refresh_token: getRefreshToken() } });
    return true;
  } catch {
    return false;
  }
}

/** Forgets the session in this browser: both tokens and the remembered active
 * repository (it belongs to this account), then a full navigation to `/login`
 * so no in-memory state or cached response survives. */
export function forgetSession(): void {
  clearTokens();
  clearActiveRepositoryId();
  window.location.assign("/login");
}
