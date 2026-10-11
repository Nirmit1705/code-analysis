import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Github, ArrowRight, Loader2 } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { z } from "zod";

import { PRODUCT } from "@/lib/repo-data";
import { useLogin } from "@/lib/queries/auth";
import {
  consumeOAuthState,
  useGithubAuthorize,
  useGithubExchange,
} from "@/lib/queries/repos";
import { ApiError } from "@/lib/api";

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

export function AuthFrame({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <div className="relative grid min-h-screen place-items-center overflow-hidden ambient-hero px-4">
      <div className="absolute inset-0 grid-field opacity-50" />
      <div className="relative w-full max-w-[392px]">
        <div className="mb-6 flex flex-col items-center text-center">
          <Link to="/" className="flex items-center gap-2">
            <span className="grid size-7 place-items-center rounded-[6px] border border-violet/40 bg-violet/15">
              <svg viewBox="0 0 16 16" className="size-4">
                <path
                  d="M3 11.5 8 3l5 8.5"
                  fill="none"
                  stroke="var(--violet-soft)"
                  strokeWidth="1.6"
                  strokeLinejoin="round"
                />
                <circle cx="8" cy="12.4" r="1.4" fill="var(--magenta)" />
              </svg>
            </span>
            <span className="text-[15px] font-semibold tracking-tight">{PRODUCT.name}</span>
          </Link>
          <h1 className="mt-5 text-[20px] font-semibold tracking-tight">{title}</h1>
          <p className="mt-1.5 text-[13px] text-muted-foreground">{subtitle}</p>
        </div>
        <div className="panel ambient-panel p-5">{children}</div>
        <p className="mt-4 text-center text-[12.5px] text-muted-foreground">{footer}</p>
      </div>
    </div>
  );
}

export function Field({
  label,
  type,
  placeholder,
  hint,
  value,
  onChange,
  name,
  autoComplete,
  required,
}: {
  label: string;
  type: string;
  placeholder: string;
  hint?: React.ReactNode;
  value?: string;
  onChange?: (value: string) => void;
  name?: string;
  autoComplete?: string;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center justify-between text-[12px] text-muted-foreground">
        {label}
        {hint}
      </span>
      <input
        type={type}
        name={name}
        placeholder={placeholder}
        value={value}
        onChange={onChange ? (e) => onChange(e.target.value) : undefined}
        autoComplete={autoComplete}
        required={required}
        className="focus-ring w-full rounded-md border border-input bg-background px-2.5 py-2 font-mono text-[12.5px] text-foreground placeholder:text-subtle"
      />
    </label>
  );
}

/** "Continue with GitHub": asks the API where to send the browser, then goes
 * there. Shared by the sign-in and sign-up screens. */
export function GithubSignInButton({ label }: { label: string }) {
  const authorize = useGithubAuthorize("signin");

  const handleClick = () => {
    authorize.mutate(undefined, {
      onSuccess: (data) => {
        window.location.href = data.authorize_url;
      },
      onError: (err) => {
        const message =
          err instanceof ApiError && err.status === 503
            ? "GitHub sign-in isn't configured on this server."
            : "Couldn't start GitHub sign-in — please try again.";
        toast.error(message);
      },
    });
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={authorize.isPending}
      className="focus-ring flex w-full items-center justify-center gap-2 rounded-md border border-border-strong bg-surface-2 px-3 py-2.5 text-[13px] font-medium transition-colors hover:bg-surface-3 disabled:opacity-60"
    >
      {authorize.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Github className="size-4" />}
      {label}
    </button>
  );
}

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
      title="Sign in to CodeLens AI"
      subtitle="Codebase intelligence for your repositories"
      footer={
        <>
          Don&apos;t have an account?{" "}
          <Link to="/signup" className="text-violet-soft hover:underline">
            Create account
          </Link>
        </>
      }
    >
      <GithubSignInButton label="Continue with GitHub" />

      <div className="my-5 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="label-xs">or sign in with email</span>
        <span className="h-px flex-1 bg-border" />
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
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
          placeholder="••••••••••"
          value={password}
          onChange={setPassword}
          name="password"
          autoComplete="current-password"
          required
          hint={
            <span className="cursor-pointer text-[11.5px] text-violet-soft hover:underline">
              Forgot?
            </span>
          }
        />
        <button
          type="submit"
          disabled={login.isPending}
          className="focus-ring flex w-full items-center justify-center gap-1.5 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-3 py-2.5 text-[13px] font-medium transition-colors hover:bg-[oklch(0.745_0.19_44)] disabled:opacity-60"
        >
          {login.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <>
              Sign In <ArrowRight className="size-3.5" />
            </>
          )}
        </button>
      </form>
    </AuthFrame>
  );
}
