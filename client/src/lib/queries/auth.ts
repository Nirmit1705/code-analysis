/** TanStack Query hooks for `server/app/api/v1/auth.py` (Phase 1) — the
 * minimal real login/signup wiring Phase 2.5 needs so the onboarding
 * wizard's GitHub steps have an authenticated user to run as. */
import { useMutation } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { setTokens } from "@/lib/auth";

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
