import { createFileRoute, Link } from "@tanstack/react-router";
import { Github, ArrowRight, ShieldCheck } from "lucide-react";
import { AuthFrame, Field } from "./login";

export const Route = createFileRoute("/signup")({
  head: () => ({
    meta: [
      { title: "Create account — Substrate" },
      { name: "description", content: "Create a Substrate account with GitHub and analyze your first repository in minutes." },
      { property: "og:title", content: "Create account — Substrate" },
      { property: "og:description", content: "Connect GitHub and get architecture, dependency and technical debt intelligence for your repository." },
    ],
  }),
  component: SignUp,
});

function SignUp() {
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
      <Link
        to="/onboarding"
        className="focus-ring flex w-full items-center justify-center gap-2 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-3 py-2.5 text-[13px] font-medium transition-colors hover:bg-[oklch(0.745_0.19_44)]"
      >
        <Github className="size-4" /> Continue with GitHub
      </Link>
      <p className="mt-2 flex items-center justify-center gap-1.5 font-mono text-[10.5px] text-subtle">
        <ShieldCheck className="size-3 text-success" /> read-only access · no code stored
      </p>

      <div className="my-5 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="label-xs">or email</span>
        <span className="h-px flex-1 bg-border" />
      </div>

      <div className="space-y-3">
        <Field label="Work email" type="email" placeholder="you@company.com" />
        <Field label="Password" type="password" placeholder="min. 12 characters" />
        <Link
          to="/onboarding"
          className="focus-ring flex w-full items-center justify-center gap-1.5 rounded-md border border-border-strong bg-surface-2 px-3 py-2.5 text-[13px] font-medium transition-colors hover:bg-surface-3"
        >
          Create account <ArrowRight className="size-3.5" />
        </Link>
      </div>
    </AuthFrame>
  );
}
