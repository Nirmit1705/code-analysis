import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Github, ArrowRight, Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { PRODUCT } from "@/lib/repo-data";
import { useLogin } from "@/lib/queries/auth";
import { ApiError } from "@/lib/api";

export const Route = createFileRoute("/login")({
  head: () => ({
    meta: [
      { title: "Sign in — Substrate" },
      {
        name: "description",
        content:
          "Sign in to Substrate with GitHub or email to explore your codebase intelligence workspace.",
      },
      { property: "og:title", content: "Sign in — Substrate" },
      {
        property: "og:description",
        content: "Sign in to your Substrate codebase intelligence workspace.",
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

function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const login = useLogin();

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
      title="Sign in to Substrate"
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
      <p className="rounded-md border border-border bg-surface-2/50 px-3 py-2.5 text-center text-[12px] text-muted-foreground">
        <Github className="mr-1.5 inline size-3.5 align-[-2px]" />
        GitHub connects from onboarding, once you have an account — sign in with email first.
      </p>

      <div className="my-5 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="label-xs">sign in with email</span>
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
