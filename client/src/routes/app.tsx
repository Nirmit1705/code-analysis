import { createFileRoute, Outlet, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { isAuthenticated } from "@/lib/auth";

export const Route = createFileRoute("/app")({
  component: AppLayout,
});

/** Sign-in guard for every `/app/*` screen. The token lives in `localStorage`,
 * which only exists in the browser, so the check runs after hydration: the
 * server render and the first client render are both the blank "checking"
 * state (no hydration mismatch, and no screen or request starts before the
 * check). No token -> `/login`. A token the server refuses (expired, or its
 * session logged out) is handled by `apiFetch`, which also sends the user to
 * sign in. */
function AppLayout() {
  const navigate = useNavigate();
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    if (isAuthenticated()) setSignedIn(true);
    else void navigate({ to: "/login", replace: true });
  }, [navigate]);

  if (!signedIn) return <div className="min-h-screen bg-background" aria-busy="true" />;

  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
