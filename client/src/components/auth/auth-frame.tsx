import { Link } from "@tanstack/react-router";
import { Github, Loader2, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import { useGithubAuthorize } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";
import { buttonSecondary, buttonSize, inputClass } from "./auth-ui";
import { AuthLens, AuthLensMini } from "./auth-lens";

/** The sign-in / sign-up layout: the form on the left, the lens art with a
 * one-line pitch on the right (large screens only; phones get a small lens above
 * the heading instead). */
export function AuthFrame({
  title,
  subtitle,
  children,
  footer,
  headline,
  tagline,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer: ReactNode;
  /** The pitch shown over the art. */
  headline: string;
  tagline: string;
}) {
  return (
    <div className="min-h-screen bg-background font-auth text-foreground lg:grid lg:grid-cols-[minmax(0,600px)_minmax(0,1fr)]">
      <div className="flex min-h-screen flex-col px-6 pb-8 pt-7 sm:px-12 lg:px-[88px] lg:pb-10 lg:pt-11">
        <Link to="/" className="focus-ring w-fit rounded-md">
          <img
            src="/codelens-wordmark-on-dark.svg"
            alt="CodeLens AI"
            width={175}
            height={30}
            className="h-[30px] w-auto"
          />
        </Link>

        <div className="flex flex-1 flex-col justify-center py-10">
          <div className="w-full max-w-[424px]">
            <AuthLensMini className="mb-6 lg:hidden" />
            <h1 className="text-[34px] font-semibold leading-[1.05] tracking-[-0.03em] sm:text-[40px]">
              {title}
            </h1>
            <p className="mt-3.5 text-[13.5px] leading-normal text-muted-foreground">{subtitle}</p>
            <div className="mt-9">{children}</div>
            <p className="mt-7 text-[12px] text-muted-foreground">{footer}</p>
          </div>
        </div>

        <p className="flex items-center gap-2 text-[10px] text-muted-foreground">
          <ShieldCheck className="size-3.5 shrink-0 text-success" />
          Read-only access. Your source is never stored.
        </p>
      </div>

      <aside className="relative hidden overflow-hidden border-l border-border bg-surface lg:block">
        <AuthLens className="absolute inset-0 h-full w-full" />
        <div className="absolute bottom-16 left-16 max-w-[460px] [@media(max-height:720px)]:hidden">
          <h2 className="text-[26px] font-semibold leading-[1.12] tracking-[-0.025em]">
            {headline}
          </h2>
          <p className="mt-3 text-[13.5px] leading-normal text-muted-foreground">{tagline}</p>
        </div>
      </aside>
    </div>
  );
}

export function Field({
  label,
  type,
  placeholder,
  hint,
  help,
  value,
  onChange,
  name,
  autoComplete,
  required,
}: {
  label: string;
  type: string;
  placeholder: string;
  /** Sits opposite the label (e.g. a link). */
  hint?: ReactNode;
  /** A line of guidance under the input. */
  help?: ReactNode;
  value?: string;
  onChange?: (value: string) => void;
  name?: string;
  autoComplete?: string;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-2 flex items-baseline justify-between text-[11px] font-medium text-muted-foreground">
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
        className={cn(inputClass, "h-[46px] px-3.5 text-[12.5px]")}
      />
      {help ? <span className="mt-2 block text-[10px] text-muted-foreground">{help}</span> : null}
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
      className={cn(buttonSecondary, buttonSize, "w-full")}
    >
      {authorize.isPending ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <Github className="size-[18px]" />
      )}
      {label}
    </button>
  );
}
