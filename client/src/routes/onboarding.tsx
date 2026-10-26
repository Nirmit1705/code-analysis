import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { z } from "zod";
import {
  AlertCircle,
  Check,
  Github,
  Globe,
  Loader2,
  Lock,
  Search,
  ShieldCheck,
} from "lucide-react";

import {
  buttonGhost,
  buttonPrimary,
  buttonSecondary,
  buttonSize,
  buttonSizeLg,
  inputClass,
} from "@/components/auth/auth-ui";
import { LensProgress } from "@/components/onboarding/lens-progress";
import { OnboardingShell } from "@/components/onboarding/onboarding-shell";
import type { Tone } from "@/components/ui-kit";
import { cn } from "@/lib/utils";
import { getAccessToken, isAuthenticated } from "@/lib/auth";
import { setActiveRepositoryId } from "@/lib/active-repo";
import {
  ANALYSIS_RUN_STAGES,
  useAnalysisRun,
  useAnalysisProgressSocket,
  useCreateAnalysisRun,
  type AnalysisRunRead,
  type AnalysisRunStatus,
} from "@/lib/queries/analysis";
import {
  consumeOAuthState,
  useGithubAuthorize,
  useGithubExchange,
  useGithubInstall,
  useGithubRepos,
  useRepoBranches,
  useSelectBranch,
  useSelectRepo,
  type BranchRead,
  type GitHubRepoSummary,
  type RepositoryRead,
} from "@/lib/queries/repos";

// TanStack Router's default search parser JSON-parses each raw query value,
// so a numeric-looking param (`installation_id=158015098`, `github_error=1`)
// arrives as a `number`, not a `string` — `z.coerce.string()` accepts either
// instead of throwing (an uncaught throw here took down the whole route,
// which is the "Something went wrong" error boundary this was hit during
// manual verification).
const searchSchema = z.object({
  exchange_code: z.coerce.string().optional(),
  installation_id: z.coerce.string().optional(),
  github_error: z.coerce.string().optional(),
  // Set by `GET /auth/github/login/callback` (the "link GitHub" OAuth return):
  // `oauth_state` must match the one this browser started with; `needs_install`
  // means the person is verified but the App isn't installed on their account yet.
  oauth_state: z.coerce.string().optional(),
  needs_install: z.coerce.string().optional(),
});

export const Route = createFileRoute("/onboarding")({
  validateSearch: searchSchema,
  head: () => ({
    meta: [
      { title: "Connect a repository — CodeLens AI" },
      {
        name: "description",
        content:
          "Connect GitHub, pick a repository and branch, and let CodeLens AI build codebase intelligence for it.",
      },
      { property: "og:title", content: "Connect a repository — CodeLens AI" },
      {
        property: "og:description",
        content:
          "Guided setup: GitHub access, repository selection, branch selection and full codebase analysis.",
      },
    ],
  }),
  component: Onboarding,
});

function formatSizeKb(sizeKb: number): string {
  if (sizeKb >= 1024 * 1024) return `${(sizeKb / (1024 * 1024)).toFixed(1)} TB`;
  if (sizeKb >= 1024) return `${(sizeKb / 1024).toFixed(0)} MB`;
  return `${sizeKb} KB`;
}

function formatPushedAt(pushedAt: string | null): string {
  if (!pushedAt) return "—";
  try {
    return formatDistanceToNow(new Date(pushedAt), { addSuffix: true });
  } catch {
    return pushedAt;
  }
}

// Step indexes, in the order the rail in `OnboardingShell` lists them.
const STEP_CONNECT = 0;
const STEP_REPOSITORY = 1;
const STEP_BRANCH = 2;
const STEP_ANALYSIS = 3;
const STEP_READY = 4;

// Human-readable phrasing of each real `AnalysisRunStatus` value the
// checklist below renders — the label text is invented, but the set and
// order of stages it labels is not (see `ANALYSIS_RUN_STAGES`'s own doc).
const STAGE_LABELS: Record<(typeof ANALYSIS_RUN_STAGES)[number], string> = {
  queued: "Queued",
  cloning: "Cloning repository",
  parsing: "Parsing files",
  analyzing: "Analyzing code",
  building_graph: "Building architecture graph",
  indexing: "Generating embeddings",
  generating_insights: "Running technical debt analysis",
};

// GitHub's own language colours — data, not theme tokens.
const LANGUAGE_COLORS: Record<string, string> = {
  TypeScript: "#3178c6",
  JavaScript: "#f1e05a",
  Python: "#3572a5",
  Go: "#00add8",
  Java: "#b07219",
  Kotlin: "#a97bff",
  Rust: "#dea584",
  Ruby: "#701516",
  PHP: "#4f5d95",
  "C#": "#178600",
  "C++": "#f34b7d",
  C: "#555555",
  Swift: "#f05138",
  HCL: "#844fba",
  Shell: "#89e051",
  HTML: "#e34c26",
  CSS: "#563d7c",
};

// Simple MVP tiering directly over the raw `debt_score` sum, distinct from
// (and cruder than) the real Technical-Debt health formula Phase 6.3/6.4
// compute from it — this screen only needs a rough tier, not the finished
// health score, per the Phase 6 plan's own note for this step.
function debtTier(score: number): { label: string; tone: Tone } {
  if (score <= 20) return { label: "Low debt", tone: "success" };
  if (score <= 50) return { label: "Moderate debt", tone: "warning" };
  return { label: "High debt", tone: "danger" };
}

const DEBT_PILL: Partial<Record<Tone, string>> = {
  success: "bg-success/12 text-success",
  warning: "bg-warning/12 text-warning",
  danger: "bg-danger/12 text-danger",
};

function StepHeading({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <>
      <h1 className="text-[34px] font-semibold leading-[1.05] tracking-[-0.03em] sm:text-[40px]">
        {title}
      </h1>
      {children ? (
        <p className="mt-3.5 max-w-[580px] text-[14.5px] leading-normal text-muted-foreground">
          {children}
        </p>
      ) : null}
    </>
  );
}

/** A selectable list row's radio mark. The dot scales in when chosen. */
function RadioDot({ checked }: { checked: boolean }) {
  return (
    <span
      className={cn(
        "grid size-[18px] shrink-0 place-items-center rounded-full border-[1.5px] transition-colors duration-150 motion-reduce:transition-none",
        checked ? "border-primary" : "border-border-strong",
      )}
    >
      <span
        className={cn(
          "size-2 rounded-full bg-primary transition-transform duration-150 motion-reduce:transition-none",
          checked ? "scale-100" : "scale-0",
        )}
      />
    </span>
  );
}

const rowBase =
  "w-full border-t border-border text-left transition-colors duration-150 first:border-t-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/60 motion-reduce:transition-none";

function rowTone(selected: boolean) {
  return selected ? "bg-primary/[0.09]" : "hover:bg-surface-2";
}

function ListSkeleton({ rows }: { rows: number }) {
  return (
    <div className="divide-y divide-border" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex animate-pulse items-center gap-4 px-6 py-[18px]">
          <span className="size-[18px] rounded-full bg-surface-3" />
          <div className="flex-1 space-y-2.5">
            <span className="block h-3.5 w-48 rounded bg-surface-3" />
            <span className="block h-3 w-28 rounded bg-surface-2" />
          </div>
        </div>
      ))}
    </div>
  );
}

function ErrorNote({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="flex items-start gap-3 px-6 py-5 text-[12px] text-danger">
      <AlertCircle className="mt-0.5 size-4 shrink-0" />
      <span className="flex-1">{children}</span>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="focus-ring rounded-sm font-medium underline-offset-2 hover:underline"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}

function Onboarding() {
  const [step, setStep] = useState(STEP_CONNECT);
  const [query, setQuery] = useState("");
  const [onlyPrivate, setOnlyPrivate] = useState(false);
  const [selectedRepo, setSelectedRepo] = useState<GitHubRepoSummary | null>(null);
  const [repository, setRepository] = useState<RepositoryRead | null>(null);
  const [selectedBranchName, setSelectedBranchName] = useState<string | null>(null);
  const [confirmedBranch, setConfirmedBranch] = useState<BranchRead | null>(null);
  const [completedRun, setCompletedRun] = useState<AnalysisRunRead | null>(null);
  const navigate = useNavigate();
  const search = Route.useSearch();

  const githubInstall = useGithubInstall();
  const githubAuthorize = useGithubAuthorize("link");
  const githubExchange = useGithubExchange();
  // Strict Mode runs effects twice in dev; a returning OAuth state is single-use.
  const handledExchange = useRef<string | null>(null);
  // Also probed while on the connect step (not just the repository step): GitHub
  // only round-trips through our callback on the *first* authorization of an
  // installation — revisiting "Connect GitHub" once already installed just lands
  // the user on GitHub's own "manage access" page with no redirect back to us.
  // This succeeding is how the connect step detects an already-connected account
  // and offers to continue straight to repo selection instead of demanding a
  // callback that will never come. A 400 here just means "not connected yet" —
  // the ordinary starting state — not a real error, so it's never surfaced as one.
  const githubRepos = useGithubRepos(step === STEP_CONNECT || step === STEP_REPOSITORY);
  const repoBranches = useRepoBranches(repository?.id ?? null);
  const selectRepo = useSelectRepo();
  const selectBranch = useSelectBranch(repository?.id ?? null);

  // Landing back here from GitHub, by either route:
  //  - the App install round-trip (`GET /auth/github/callback`, Phase 2.5), or
  //  - the "link GitHub" OAuth return (`GET /auth/github/login/callback`),
  //    which also carries `oauth_state` (must be this browser's) and, when the
  //    App isn't installed on the person's own account yet, `needs_install`.
  // `exchange_code` is the one-time code handed over in place of raw JWTs (see
  // `server/app/api/v1/github.py::github_exchange`) — trade it for real
  // tokens, then jump to repository selection (or, for `needs_install`, on to
  // GitHub's install page, since the person just asked to connect).
  useEffect(() => {
    if (search.exchange_code) {
      if (handledExchange.current === search.exchange_code) return;
      handledExchange.current = search.exchange_code;
      if (search.oauth_state !== undefined && !consumeOAuthState(search.oauth_state)) {
        toast.error("That GitHub connection didn't start in this browser — please try again.");
        void navigate({ to: "/onboarding", search: {}, replace: true });
        return;
      }
      const needsInstall = Boolean(search.needs_install);
      githubExchange.mutate(search.exchange_code, {
        onSuccess: () => {
          void navigate({ to: "/onboarding", search: {}, replace: true });
          if (needsInstall) {
            startInstall();
            return;
          }
          toast.success("GitHub connected");
          setStep(STEP_REPOSITORY);
        },
        onError: () => {
          toast.error("Couldn't complete the GitHub connection — please try again.");
          void navigate({ to: "/onboarding", search: {}, replace: true });
        },
      });
    } else if (search.github_error) {
      toast.error("GitHub connection failed or was cancelled.");
      void navigate({ to: "/onboarding", search: {}, replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search.exchange_code, search.github_error]);

  // Second half of connecting: install the App (choose repositories) on GitHub.
  const startInstall = () => {
    githubInstall.mutate(undefined, {
      onSuccess: (data) => {
        window.location.href = data.install_url;
      },
      onError: () => toast.error("Couldn't start the GitHub App install — please try again."),
    });
  };

  // First half: prove who the person is on GitHub and attach it to this
  // account. If their own account already has the App installed, that is
  // connected right here and the install page is skipped (Known Issue #17).
  const handleAuthorizeGithub = () => {
    if (!isAuthenticated()) {
      toast.error("Sign in first, then connect GitHub.");
      void navigate({ to: "/login" });
      return;
    }
    githubAuthorize.mutate(undefined, {
      onSuccess: (data) => {
        window.location.href = data.authorize_url;
      },
      onError: () => toast.error("Couldn't start the GitHub connection — please try again."),
    });
  };

  const handleSelectRepoContinue = () => {
    if (!selectedRepo) return;
    selectRepo.mutate(selectedRepo, {
      onSuccess: (repo) => {
        setRepository(repo);
        // The repo becomes "active" for `/app/*` as soon as it's connected,
        // not only once analysis finishes — this is the earliest point a
        // real `Repository.id` exists to anchor it to (Phase 6.1).
        setActiveRepositoryId(repo.id);
        setSelectedBranchName(null);
        setStep(STEP_BRANCH);
      },
      onError: () => toast.error("Couldn't save the selected repository — please try again."),
    });
  };

  const handleSelectBranchContinue = () => {
    if (!selectedBranchName) return;
    selectBranch.mutate(selectedBranchName, {
      onSuccess: (branch) => {
        setConfirmedBranch(branch);
        setStep(STEP_ANALYSIS);
      },
      onError: () => toast.error("Couldn't select that branch — please try again."),
    });
  };

  // Preselect the branch already marked `is_selected` (or, failing that,
  // GitHub's `is_default`) once the sync comes back, so the picker opens on
  // a sensible choice instead of nothing.
  useEffect(() => {
    if (selectedBranchName || !repoBranches.data?.length) return;
    const preselect =
      repoBranches.data.find((b) => b.is_selected) ?? repoBranches.data.find((b) => b.is_default);
    if (preselect) setSelectedBranchName(preselect.name);
  }, [repoBranches.data, selectedBranchName]);

  const repos = githubRepos.data ?? [];
  const filtered = repos.filter(
    (r) => r.full_name.toLowerCase().includes(query.toLowerCase()) && (!onlyPrivate || r.private),
  );
  const connectPending = githubAuthorize.isPending || githubInstall.isPending;

  return (
    <OnboardingShell step={step}>
      <div className="max-w-[820px]">
        {step === STEP_CONNECT ? (
          <>
            <StepHeading title="Connect GitHub">
              CodeLens AI reads your code through a GitHub App. You choose which repositories it can
              see.
            </StepHeading>

            <div className="mt-10 rounded-2xl border border-border bg-surface">
              <div className="flex flex-wrap items-center gap-4 px-6 py-6 sm:px-7">
                <span className="grid size-12 shrink-0 place-items-center rounded-xl border border-border-strong bg-surface-2">
                  <Github className="size-[26px]" />
                </span>
                <div className="min-w-0 flex-1 basis-56">
                  <div className="text-[15px] font-semibold">GitHub</div>
                  <div className="mt-0.5 text-[11.5px] text-muted-foreground">
                    {githubRepos.isSuccess
                      ? "Connected to this account."
                      : "Opens github.com in this tab. You'll come straight back here."}
                  </div>
                </div>
                {githubRepos.isLoading ? (
                  <span className="flex items-center gap-2 text-[11.5px] text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" /> Checking connection
                  </span>
                ) : githubRepos.isSuccess ? (
                  <div className="flex items-center gap-4">
                    <span className="flex items-center gap-1.5 text-[12px] text-success">
                      <Check className="size-4" strokeWidth={3} /> Connected
                    </span>
                    <button
                      type="button"
                      onClick={() => setStep(STEP_REPOSITORY)}
                      className={cn(buttonPrimary, buttonSize)}
                    >
                      Continue
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={handleAuthorizeGithub}
                    disabled={connectPending}
                    className={cn(buttonPrimary, buttonSize)}
                  >
                    {connectPending ? <Loader2 className="size-4 animate-spin" /> : null}
                    Connect GitHub
                  </button>
                )}
              </div>

              <div className="border-t border-border px-6 pb-3 pt-2 sm:px-7">
                <div className="pb-1.5 pt-3.5 text-[11.5px] font-medium text-muted-foreground">
                  What you&apos;re allowing
                </div>
                {[
                  {
                    name: "Contents",
                    access: "read-only",
                    why: "Clone your code so we can build the architecture graph and the search index.",
                  },
                  {
                    name: "Metadata",
                    access: "read-only",
                    why: "List your repositories, branches and commits.",
                  },
                  {
                    name: "Push events",
                    access: null,
                    why: "Re-analyze automatically when your tracked branch gets new commits.",
                  },
                ].map((p) => (
                  <div
                    key={p.name}
                    className="grid gap-1 border-t border-border py-3.5 sm:grid-cols-[170px_minmax(0,1fr)] sm:gap-5"
                  >
                    <div className="flex items-center gap-2.5">
                      <span className="font-mono text-[11px]">{p.name}</span>
                      {p.access ? (
                        <span className="text-[10px] text-success">{p.access}</span>
                      ) : null}
                    </div>
                    <p className="text-[12.5px] leading-snug text-muted-foreground">{p.why}</p>
                  </div>
                ))}
              </div>
            </div>

            <p className="mt-5 flex max-w-[600px] items-start gap-3 text-[12px] leading-normal text-muted-foreground">
              <ShieldCheck className="mt-0.5 size-[18px] shrink-0 text-success" />
              <span>
                CodeLens AI never writes to your repositories and doesn&apos;t keep your source,
                only the indexes it builds. You can revoke access any time in your GitHub settings.
              </span>
            </p>
          </>
        ) : null}

        {step === STEP_REPOSITORY ? (
          <>
            <StepHeading title="Choose a repository">
              Pick the repository you want to understand. CodeLens AI only sees the ones you gave it
              access to.
            </StepHeading>

            <div className="mt-8 flex flex-wrap items-center gap-3.5">
              <div className="relative w-full sm:w-[340px]">
                <Search className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-subtle" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Filter repositories"
                  aria-label="Filter repositories"
                  className={cn(inputClass, "h-[42px] pl-10 pr-3.5 text-[12.5px]")}
                />
              </div>
              <div className="flex overflow-hidden rounded-xl border border-border-strong">
                {[
                  { label: "All", value: false },
                  { label: "Private", value: true },
                ].map((o) => (
                  <button
                    key={o.label}
                    type="button"
                    aria-pressed={onlyPrivate === o.value}
                    onClick={() => setOnlyPrivate(o.value)}
                    className={cn(
                      "focus-ring h-[42px] px-4 text-[12px] transition-colors duration-150 motion-reduce:transition-none",
                      o.value && "border-l border-border-strong",
                      onlyPrivate === o.value
                        ? "bg-surface-3 text-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
              {githubRepos.isSuccess ? (
                <span className="text-[11.5px] text-muted-foreground sm:ml-auto">
                  {filtered.length} {filtered.length === 1 ? "repository" : "repositories"}
                </span>
              ) : null}
            </div>

            <div className="mt-4 max-h-[440px] overflow-y-auto rounded-2xl border border-border bg-surface">
              {githubRepos.isLoading ? (
                <ListSkeleton rows={5} />
              ) : githubRepos.isError ? (
                <ErrorNote onRetry={() => void githubRepos.refetch()}>
                  Couldn&apos;t load repositories. Make sure the GitHub App is installed, then
                  retry.
                </ErrorNote>
              ) : filtered.length === 0 ? (
                <p className="px-6 py-10 text-center text-[12px] text-muted-foreground">
                  No repositories match.
                </p>
              ) : (
                filtered.map((r) => {
                  const selected = selectedRepo?.github_repo_id === r.github_repo_id;
                  const [owner, name] = r.full_name.split("/");
                  return (
                    <button
                      key={r.github_repo_id}
                      type="button"
                      aria-pressed={selected}
                      onClick={() => setSelectedRepo(r)}
                      className={cn(
                        rowBase,
                        rowTone(selected),
                        "grid min-h-[68px] grid-cols-[28px_minmax(0,1fr)] items-center px-6 py-3 sm:grid-cols-[28px_minmax(0,1fr)_84px_130px]",
                      )}
                    >
                      <RadioDot checked={selected} />
                      <span className="min-w-0">
                        <span className="block truncate font-mono text-[12.5px]">
                          <span className="text-muted-foreground">{owner}/</span>
                          {name}
                        </span>
                        <span className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                          {r.language ? (
                            <span className="flex items-center gap-1.5">
                              <span
                                className="size-[9px] rounded-full"
                                style={{
                                  background: LANGUAGE_COLORS[r.language] ?? "var(--subtle)",
                                }}
                              />
                              {r.language}
                            </span>
                          ) : null}
                          <span className="font-mono text-[10px]">{r.default_branch}</span>
                          <span className="flex items-center gap-1">
                            {r.private ? <Lock className="size-3" /> : <Globe className="size-3" />}
                            {r.private ? "Private" : "Public"}
                          </span>
                        </span>
                      </span>
                      <span className="hidden font-mono text-[10.5px] text-muted-foreground sm:block">
                        {formatSizeKb(r.size_kb)}
                      </span>
                      <span className="hidden text-right text-[11.5px] text-subtle sm:block">
                        {formatPushedAt(r.pushed_at)}
                      </span>
                    </button>
                  );
                })
              )}
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-4">
              <span className="min-w-0 truncate text-[12px] text-muted-foreground">
                {selectedRepo ? (
                  <>
                    Selected{" "}
                    <span className="font-mono text-[11.5px] text-foreground">
                      {selectedRepo.full_name}
                    </span>
                  </>
                ) : (
                  "Select a repository to continue"
                )}
              </span>
              <button
                type="button"
                onClick={handleSelectRepoContinue}
                disabled={!selectedRepo || selectRepo.isPending}
                className={cn(buttonPrimary, buttonSize, "ml-auto")}
              >
                {selectRepo.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
                Continue
              </button>
            </div>
          </>
        ) : null}

        {step === STEP_BRANCH ? (
          <>
            <StepHeading title="Which branch should we track?">
              We analyze this branch now, then again whenever it gets new commits.
            </StepHeading>
            <p className="mt-4 font-mono text-[11.5px] text-muted-foreground">
              {repository?.full_name}
            </p>

            <div className="mt-5 max-h-[440px] overflow-y-auto rounded-2xl border border-border bg-surface">
              {repoBranches.isLoading ? (
                <ListSkeleton rows={4} />
              ) : repoBranches.isError ? (
                <ErrorNote onRetry={() => void repoBranches.refetch()}>
                  Couldn&apos;t load branches. Please retry.
                </ErrorNote>
              ) : (
                (repoBranches.data ?? []).map((b) => {
                  const chosen = selectedBranchName === b.name;
                  return (
                    <button
                      key={b.id}
                      type="button"
                      aria-pressed={chosen}
                      onClick={() => setSelectedBranchName(b.name)}
                      className={cn(
                        rowBase,
                        rowTone(chosen),
                        "grid min-h-[60px] grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-y-1 px-6 py-3 sm:grid-cols-[28px_220px_90px_minmax(0,1fr)]",
                      )}
                    >
                      <RadioDot checked={chosen} />
                      <span className="truncate font-mono text-[12.5px]">{b.name}</span>
                      <span
                        className={cn(
                          "font-mono text-[11px] transition-colors duration-150 motion-reduce:transition-none",
                          chosen ? "text-violet-soft" : "text-muted-foreground",
                        )}
                      >
                        {b.latest_commit_sha.slice(0, 7)}
                      </span>
                      {/* is_default = GitHub's actual default branch; is_selected = the branch
                          currently chosen to analyze (Phase 2.4 fix) — two independent facts. */}
                      <span className="col-span-full flex flex-wrap items-center gap-x-3 gap-y-1 pl-7 sm:col-span-1 sm:pl-0">
                        {b.is_default ? (
                          <span className="text-[11px] text-muted-foreground">
                            Default branch on GitHub
                          </span>
                        ) : null}
                        {b.is_selected ? (
                          <span className="rounded-md bg-primary/10 px-2 py-0.5 text-[10px] text-violet-soft">
                            Currently analyzed
                          </span>
                        ) : null}
                      </span>
                    </button>
                  );
                })
              )}
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => setStep(STEP_REPOSITORY)}
                className={cn(buttonGhost, buttonSize, "-ml-5")}
              >
                Back
              </button>
              <span className="ml-auto text-[12px] text-muted-foreground">
                {selectedBranchName ? (
                  <>
                    Tracking{" "}
                    <span className="font-mono text-[11.5px] text-foreground">
                      {selectedBranchName}
                    </span>
                  </>
                ) : null}
              </span>
              <button
                type="button"
                onClick={handleSelectBranchContinue}
                disabled={!selectedBranchName || selectBranch.isPending}
                className={cn(buttonPrimary, buttonSize)}
              >
                {selectBranch.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
                Start analysis
              </button>
            </div>
          </>
        ) : null}

        {step === STEP_ANALYSIS && repository ? (
          <AnalysisStep
            repositoryId={repository.id}
            onDone={(run) => {
              setCompletedRun(run);
              setStep(STEP_READY);
            }}
            branch={confirmedBranch?.name ?? selectedBranchName ?? ""}
            target={repository.full_name}
          />
        ) : step === STEP_ANALYSIS ? (
          <div className="rounded-2xl border border-border bg-surface p-7 text-center text-[12.5px] text-muted-foreground">
            No repository selected — go back and pick one first.
          </div>
        ) : null}

        {step === STEP_READY ? (
          <>
            <h1 className="text-[34px] font-semibold leading-[1.05] tracking-[-0.03em] sm:text-[42px]">
              {repository?.name ?? "Your repository"} is ready to explore.
            </h1>
            <p className="mt-[18px] break-all font-mono text-[11.5px] text-muted-foreground">
              {repository?.full_name}
              {confirmedBranch ? (
                <>
                  {" on "}
                  <span className="text-foreground">{confirmedBranch.name}</span>
                  {" at "}
                  <span className="text-violet-soft">
                    {confirmedBranch.latest_commit_sha.slice(0, 7)}
                  </span>
                </>
              ) : null}
            </p>

            <dl className="mt-12 grid grid-cols-2 gap-px border-y border-border bg-border lg:grid-cols-4">
              {[
                { label: "Files analyzed", value: completedRun?.file_count },
                { label: "Dependencies", value: completedRun?.dependency_count },
                { label: "Modules detected", value: completedRun?.module_count },
                { label: "APIs detected", value: completedRun?.api_count },
              ].map((s) => (
                <div
                  key={s.label}
                  className="flex flex-col-reverse bg-background py-7 pl-7 first:pl-0 max-lg:odd:pl-0"
                >
                  <dt className="mt-2.5 text-[12.5px] text-muted-foreground">{s.label}</dt>
                  <dd className="text-[34px] font-semibold leading-none tracking-[-0.03em] sm:text-[48px]">
                    {(s.value ?? 0).toLocaleString()}
                  </dd>
                </div>
              ))}
            </dl>

            <div className="mt-7 flex flex-wrap items-center gap-4">
              <span
                className={cn(
                  "flex h-[30px] items-center gap-2 rounded-lg px-3 text-[11.5px] font-medium",
                  DEBT_PILL[debtTier(completedRun?.debt_score ?? 0).tone],
                )}
              >
                <span className="size-2 rounded-full bg-current" />
                {debtTier(completedRun?.debt_score ?? 0).label}
              </span>
              <span className="text-[12.5px] text-muted-foreground">
                Debt score{" "}
                <span className="font-mono text-[11.5px] text-foreground">
                  {(completedRun?.debt_score ?? 0).toFixed(1)}
                </span>
                . The full breakdown is under Technical Debt.
              </span>
            </div>

            <button
              type="button"
              onClick={() => navigate({ to: "/app" })}
              className={cn(buttonPrimary, buttonSizeLg, "mt-12")}
            >
              Explore project
            </button>
          </>
        ) : null}
      </div>
    </OnboardingShell>
  );
}

type AnalysisStage = (typeof ANALYSIS_RUN_STAGES)[number];
type AnalysisOutcome = "in_progress" | "completed" | "failed";

function AnalysisStep({
  repositoryId,
  onDone,
  branch,
  target,
}: {
  repositoryId: string;
  onDone: (run: AnalysisRunRead) => void;
  branch: string;
  target: string;
}) {
  const [runId, setRunId] = useState<string | null>(null);
  const [progressStage, setProgressStage] = useState<AnalysisStage>("queued");
  const [outcome, setOutcome] = useState<AnalysisOutcome>("in_progress");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Forces a re-render each second so the elapsed-time readout below ticks;
  // the actual value is recomputed from the run's real `started_at` on every
  // render, not accumulated locally.
  const [, tick] = useState(0);
  // Guards the mount effect below against firing twice — confirmed during
  // manual verification that without this, React's dev-mode Strict Mode
  // double-invoke (mount → cleanup → mount again, same component instance)
  // queues two real `POST /analysis-runs` calls and two real Celery runs
  // for one "Start analysis" click. A ref survives that simulated
  // remount (unlike component-scoped state), so this is the standard fix
  // for a mount effect with a real, non-idempotent side effect.
  const hasStartedRef = useRef(false);

  const createRun = useCreateAnalysisRun(repositoryId);
  const runQuery = useAnalysisRun(runId);
  const { lastEvent } = useAnalysisProgressSocket(runId, getAccessToken());

  // `mutateAsync` + a plain try/catch, not `.mutate(vars, {onSuccess,
  // onError})` — confirmed during manual verification that the per-call
  // callback form silently never fired here (the mutation's own promise
  // resolved with the real created run, but `onSuccess` was never invoked),
  // while `mutateAsync`'s returned promise is a plain, reliably-awaitable
  // JS promise unaffected by whatever internal observer/subscription
  // timing dropped the callback.
  const startAnalysis = async () => {
    setOutcome("in_progress");
    setErrorMessage(null);
    setProgressStage("queued");
    setRunId(null);
    try {
      const run = await createRun.mutateAsync();
      setRunId(run.id);
    } catch {
      toast.error("Couldn't start the analysis — please try again.");
    }
  };

  // Kick off the run exactly once, on mount.
  useEffect(() => {
    if (hasStartedRef.current) return;
    hasStartedRef.current = true;
    startAnalysis();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyStatus(status: AnalysisRunStatus, message: string | null) {
    if (status === "completed") {
      setOutcome("completed");
      return;
    }
    if (status === "failed") {
      setOutcome("failed");
      if (message) setErrorMessage(message);
      return;
    }
    // Whichever of the REST fallback or the WS relay is furthest along
    // wins — both report the same real progression, so this only ever
    // advances, never regresses.
    setProgressStage((prev) =>
      ANALYSIS_RUN_STAGES.indexOf(status) >= ANALYSIS_RUN_STAGES.indexOf(prev) ? status : prev,
    );
  }

  useEffect(() => {
    if (runQuery.data) applyStatus(runQuery.data.status, runQuery.data.error_message);
  }, [runQuery.data]);

  useEffect(() => {
    if (lastEvent) applyStatus(lastEvent.status, lastEvent.error_message);
  }, [lastEvent]);

  // The WS event only ever carries `{status}`, never the real counts (see
  // `AnalysisProgressEvent`) — fetch the full row the instant a terminal
  // status is observed rather than waiting for the REST fallback's own
  // poll interval to come back around.
  useEffect(() => {
    if (outcome !== "in_progress" && runId) void runQuery.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outcome, runId]);

  useEffect(() => {
    if (outcome === "completed" && runQuery.data?.status === "completed") {
      const t = setTimeout(() => onDone(runQuery.data!), 700);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [outcome, runQuery.data, onDone]);

  useEffect(() => {
    if (outcome !== "in_progress") return undefined;
    const interval = setInterval(() => tick((t) => t + 1), 1000);
    return () => clearInterval(interval);
  }, [outcome]);

  const startedAt = createRun.data?.started_at ?? runQuery.data?.started_at;
  const elapsedSeconds = startedAt
    ? Math.max(0, Math.round((Date.now() - new Date(startedAt).getTime()) / 1000))
    : 0;

  const stageIndex = ANALYSIS_RUN_STAGES.indexOf(progressStage);
  const pct =
    outcome === "completed" ? 100 : Math.round((stageIndex / ANALYSIS_RUN_STAGES.length) * 100);

  const failed = outcome === "failed";

  return (
    <>
      <h1 className="text-[34px] font-semibold leading-[1.05] tracking-[-0.03em] sm:text-[40px]">
        {failed ? "Analysis failed" : "Analyzing repository"}
      </h1>
      <p className="mt-4 break-all font-mono text-[11.5px] text-muted-foreground">
        {target} on <span className="text-foreground">{branch}</span>
      </p>

      <div className="mt-9 flex flex-col overflow-hidden rounded-2xl border border-border bg-surface md:flex-row">
        <div className="min-w-0 flex-1 px-5 pb-3 pt-5">
          {failed ? (
            <div className="flex items-start gap-3 rounded-xl bg-danger/10 px-4 py-4 text-[12.5px] text-danger">
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
              <span>{errorMessage ?? "The analysis run failed for an unknown reason."}</span>
            </div>
          ) : (
            <ul>
              {ANALYSIS_RUN_STAGES.map((stageKey, i) => {
                const done = i < stageIndex || outcome === "completed";
                const running = !done && i === stageIndex;
                return (
                  <li
                    key={stageKey}
                    className={cn(
                      "flex h-12 items-center gap-3.5 rounded-[10px] px-3.5 transition-colors duration-300 motion-reduce:transition-none",
                      running && "bg-primary/[0.09]",
                    )}
                  >
                    <span className="grid size-[18px] place-items-center">
                      {done ? (
                        <Check
                          strokeWidth={3}
                          className="size-[18px] animate-in text-primary duration-200 zoom-in-50 motion-reduce:animate-none"
                        />
                      ) : running ? (
                        <Loader2 className="size-[18px] animate-spin text-violet-soft motion-reduce:animate-none" />
                      ) : (
                        <span className="size-2.5 rounded-full border-[1.5px] border-border-strong" />
                      )}
                    </span>
                    <span
                      className={cn(
                        "text-[13.5px] transition-colors duration-300 motion-reduce:transition-none",
                        done
                          ? "text-muted-foreground"
                          : running
                            ? "font-semibold text-foreground"
                            : "text-subtle",
                      )}
                    >
                      {STAGE_LABELS[stageKey]}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-center justify-center gap-5 border-t border-border p-7 md:w-[300px] md:border-l md:border-t-0">
          <LensProgress
            value={pct}
            label={failed ? "!" : `${pct}%`}
            caption={failed ? "Analysis failed" : STAGE_LABELS[progressStage]}
            tone={failed ? "danger" : "primary"}
          />
          {failed ? (
            <button
              type="button"
              onClick={startAnalysis}
              className={cn(buttonSecondary, buttonSize)}
            >
              Retry analysis
            </button>
          ) : null}
        </div>
      </div>

      <div className="mt-4 flex justify-between font-mono text-[10.5px] text-muted-foreground">
        <span>{runId ? `Run ${runId.slice(0, 8)}` : "Starting…"}</span>
        <span>Elapsed {elapsedSeconds}s</span>
      </div>
    </>
  );
}
