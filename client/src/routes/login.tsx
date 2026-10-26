import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { z } from "zod";

import { AuthFrame, Field, GithubSignInButton } from "@/components/auth/auth-frame";
import { AuthDivider, buttonPrimary, buttonSize } from "@/components/auth/auth-ui";
import { useLogin } from "@/lib/queries/auth";
import { consumeOAuthState, useGithubExchange } from "@/lib/queries/repos";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

// What `GET /auth/github/login/callback` appends when it sends the browser back
// after "Continue with GitHub". `z.coerce.string()` because the router
// JSON-parses values, so `github_error=1` arrives as a number (see onboarding.tsx).
const searchSchema = z.object({
  exchange_code: z.coerce.string().optional(),
  oauth_state: z.coerce.string().optional(),
  github_error: z.coerce.string().optional(),
});

export const Route = createFileRoute("/login")({
  validateSearch: searchSchema,
  head: () => ({
    meta: [
      { title: "Sign in — CodeLens AI" },
      {
        name: "description",
        content:
          "Sign in to CodeLens AI with GitHub or email to explore your codebase intelligence workspace.",
      },
      { property: "og:title", content: "Sign in — CodeLens AI" },
      {
        property: "og:description",
        content: "Sign in to your CodeLens AI codebase intelligence workspace.",
      },
    ],
  }),
  component: Login,
});

function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const login = useLogin();
  const search = Route.useSearch();
  const githubExchange = useGithubExchange();
  // React Strict Mode runs effects twice in dev; the state check below spends
  // its one use, so a code is handled once.
  const handledCode = useRef<string | null>(null);

  // Returning from github.com (`/auth/github/login/callback` -> here): refuse a
  // return this browser didn't start, otherwise trade the one-time code for a
  // session and carry on to onboarding exactly like an email sign-in.
  useEffect(() => {
    if (search.exchange_code) {
      if (handledCode.current === search.exchange_code) return;
      handledCode.current = search.exchange_code;
      if (!search.oauth_state || !consumeOAuthState(search.oauth_state)) {
        toast.error("That GitHub sign-in didn't start in this browser — please try again.");
        void navigate({ to: "/login", search: {}, replace: true });
        return;
      }
      githubExchange.mutate(search.exchange_code, {
        onSuccess: () => void navigate({ to: "/onboarding", search: {}, replace: true }),
        onError: () => {
          toast.error("Couldn't complete GitHub sign-in — please try again.");
          void navigate({ to: "/login", search: {}, replace: true });
        },
      });
    } else if (search.github_error) {
      toast.error("GitHub sign-in failed or was cancelled.");
      void navigate({ to: "/login", search: {}, replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search.exchange_code, search.github_error]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate(
      { email, password },
      {
        onSuccess: () => void navigate({ to: "/onboarding" }),
        onError: (err) => {
          const message =
            err instanceof ApiError ? String(err.detail ?? err.message) : "Sign in failed";
          toast.error(message);
        },
      },
    );
  };

  return (
    <AuthFrame
      title="Welcome back"
      subtitle="Sign in to see how your repositories fit together."
      headline="Your repositories, mapped."
      tagline="Architecture, dependencies and technical debt, kept current with every commit."
      footer={
        <>
          New to CodeLens AI?{" "}
          <Link
            to="/signup"
            className="focus-ring rounded-sm font-medium text-violet-soft hover:underline"
          >
            Create an account
          </Link>
        </>
      }
    >
      <GithubSignInButton label="Continue with GitHub" />

      <div className="my-6">
        <AuthDivider>or sign in with email</AuthDivider>
      </div>

      <form onSubmit={handleSubmit} className="space-y-[18px]">
        <Field
          label="Email"
          type="email"
          placeholder="you@company.com"
          value={email}
          onChange={setEmail}
          name="email"
          autoComplete="email"
          required
        />
        <Field
          label="Password"
          type="password"
          placeholder="Your password"
          value={password}
          onChange={setPassword}
          name="password"
          autoComplete="current-password"
          required
        />
        <button
          type="submit"
          disabled={login.isPending}
          className={cn(buttonPrimary, buttonSize, "mt-1.5 w-full")}
        >
          {login.isPending ? <Loader2 className="size-4 animate-spin" /> : "Sign in"}
        </button>
      </form>
    </AuthFrame>
  );
}
