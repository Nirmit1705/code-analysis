import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowRight, Loader2, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { AuthFrame, Field, GithubSignInButton } from "./login";
import { useRegister } from "@/lib/queries/auth";
import { ApiError } from "@/lib/api";

export const Route = createFileRoute("/signup")({
  head: () => ({
    meta: [
      { title: "Create account — CodeLens AI" },
      {
        name: "description",
        content:
          "Create a CodeLens AI account with GitHub and analyze your first repository in minutes.",
      },
      { property: "og:title", content: "Create account — CodeLens AI" },
      {
        property: "og:description",
        content:
          "Connect GitHub and get architecture, dependency and technical debt intelligence for your repository.",
      },
    ],
  }),
  component: SignUp,
});

function SignUp() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const register = useRegister();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    register.mutate(
      { email, password },
      {
        onSuccess: () => void navigate({ to: "/onboarding" }),
        onError: (err) => {
          const message =
            err instanceof ApiError ? String(err.detail ?? err.message) : "Sign up failed";
          toast.error(message);
        },
      },
    );
  };

  return (
    <AuthFrame
      title="Create your workspace"
      subtitle="Connect a repository and understand it in minutes"
      footer={
        <>
          Already have an account?{" "}
          <Link to="/login" className="text-violet-soft hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <GithubSignInButton label="Sign up with GitHub" />
      <p className="mt-2 flex items-center justify-center gap-1.5 font-mono text-[10.5px] text-subtle">
        <ShieldCheck className="size-3 text-success" /> Read-only access · no code stored
      </p>

      <div className="my-5 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="label-xs">or create account with email</span>
        <span className="h-px flex-1 bg-border" />
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        <Field
          label="Work email"
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
          placeholder="min. 8 characters"
          value={password}
          onChange={setPassword}
          name="password"
          autoComplete="new-password"
          required
        />
        <button
          type="submit"
          disabled={register.isPending}
          className="focus-ring flex w-full items-center justify-center gap-1.5 rounded-md border border-border-strong bg-surface-2 px-3 py-2.5 text-[13px] font-medium transition-colors hover:bg-surface-3 disabled:opacity-60"
        >
          {register.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <>
              Create account <ArrowRight className="size-3.5" />
            </>
          )}
        </button>
      </form>
    </AuthFrame>
  );
}
