import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { AuthFrame, Field, GithubSignInButton } from "@/components/auth/auth-frame";
import { AuthDivider, buttonPrimary, buttonSize } from "@/components/auth/auth-ui";
import { useRegister } from "@/lib/queries/auth";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

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
      title="Create your account"
      subtitle="Connect a repository and understand it in minutes."
      headline="Understand a new codebase in minutes."
      tagline="Connect a repository. CodeLens AI reads it once, then keeps the map current as your team commits."
      footer={
        <>
          Already have an account?{" "}
          <Link
            to="/login"
            className="focus-ring rounded-sm font-medium text-violet-soft hover:underline"
          >
            Sign in
          </Link>
        </>
      }
    >
      <GithubSignInButton label="Sign up with GitHub" />

      <div className="my-6">
        <AuthDivider>or create an account with email</AuthDivider>
      </div>

      <form onSubmit={handleSubmit} className="space-y-[18px]">
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
          placeholder="Choose a password"
          help="Use at least 8 characters."
          value={password}
          onChange={setPassword}
          name="password"
          autoComplete="new-password"
          required
        />
        <button
          type="submit"
          disabled={register.isPending}
          className={cn(buttonPrimary, buttonSize, "mt-1.5 w-full")}
        >
          {register.isPending ? <Loader2 className="size-4 animate-spin" /> : "Create account"}
        </button>
      </form>
    </AuthFrame>
  );
}
