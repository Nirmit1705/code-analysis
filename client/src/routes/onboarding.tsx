import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { z } from "zod";
import {
  Github,
  ArrowRight,
  Search,
  Lock,
  Globe,
  GitBranch,
  Check,
  Circle,
  Loader2,
  ShieldCheck,
  Eye,
  FileCode2,
  Sparkles,
  AlertCircle,
} from "lucide-react";

import { Dot, Ring, StatCell, Tag, type Tone } from "@/components/ui-kit";
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

const steps = ["Welcome", "GitHub", "Repository", "Branch", "Analysis", "Complete"];

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

// Simple MVP tiering directly over the raw `debt_score` sum, distinct from
// (and cruder than) the real Technical-Debt health formula Phase 6.3/6.4
// compute from it — this screen only needs a rough tier, not the finished
// health score, per the Phase 6 plan's own note for this step.
function debtTier(score: number): { label: string; tone: Tone } {
  if (score <= 20) return { label: "Low debt", tone: "success" };
  if (score <= 50) return { label: "Moderate debt", tone: "warning" };
  return { label: "High debt", tone: "danger" };
}

function Progress({ step }: { step: number }) {
  return (
    <div className="flex items-center gap-2">
      {steps.map((s, i) => (
        <div key={s} className="flex items-center gap-2">
          <span
            className={cn(
              "flex items-center gap-1.5 text-[11.5px]",
              i === step ? "text-foreground" : i < step ? "text-muted-foreground" : "text-subtle",
            )}
          >
            <span
              className={cn(
                "grid size-4 place-items-center rounded-full border font-mono text-[9px]",
                i < step
                  ? "border-success/40 bg-success/15 text-success"
                  : i === step
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border text-subtle",
              )}
            >
              {i < step ? <Check className="size-2.5" /> : i + 1}
            </span>
            <span className="hidden sm:inline">{s}</span>
          </span>
          {i < steps.length - 1 ? <span className="h-px w-4 bg-border" /> : null}
        </div>
      ))}
    </div>
  );
}

function Onboarding() {
  const [step, setStep] = useState(0);
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
  // Also probed while on step 1 (not just step 2): GitHub only round-trips
  // through our callback on the *first* authorization of an installation —
  // revisiting "Authorize GitHub" once already installed just lands the user
  // on GitHub's own "manage access" page with no redirect back to us. This
  // succeeding is how step 1 detects an already-connected account and skips
  // straight to repo selection instead of demanding a callback that will
  // never come. A 400 here just means "not connected yet" — the ordinary
  // starting state — not a real error, so it's never surfaced as one.
  const githubRepos = useGithubRepos(step === 1 || step === 2);
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
          setStep(2);
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
        setStep(3);
      },
      onError: () => toast.error("Couldn't save the selected repository — please try again."),
    });
  };

  const handleSelectBranchContinue = () => {
    if (!selectedBranchName) return;
    selectBranch.mutate(selectedBranchName, {
      onSuccess: (branch) => {
        setConfirmedBranch(branch);
        setStep(4);
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

  return (
    <div className="relative min-h-screen overflow-hidden ambient-app">
      <div className="absolute inset-0 grid-field opacity-40" />
      <header className="relative flex h-12 items-center gap-3 border-b border-border px-4">
        <Link to="/" className="flex items-center gap-2">
          <img
            src="/codelens-wordmark-on-dark.svg"
            alt="CodeLens AI"
            width={140}
            height={24}
            className="h-[24px] w-auto"
          />
        </Link>
        <div className="mx-auto hidden md:block">
          <Progress step={step} />
        </div>
        <Link to="/app" className="ml-auto text-[12px] text-subtle hover:text-foreground">
          Skip setup
        </Link>
      </header>

      <div className="relative mx-auto max-w-[860px] px-5 py-12">
        {step === 0 ? (
          <div className="panel ambient-panel p-8 text-center">
            <span className="mx-auto grid size-11 place-items-center rounded-lg border border-violet/35 bg-violet/12">
              <Sparkles className="size-5 text-violet-soft" />
            </span>
            <h1 className="mt-5 text-[26px] font-semibold tracking-tight">
              Let&apos;s understand your first codebase.
            </h1>
            <p className="mx-auto mt-3 max-w-lg text-[13.5px] leading-relaxed text-muted-foreground">
              CodeLens AI reads a repository once, builds an architecture graph, an API surface and
              a debt model, then keeps them current as your team commits.
            </p>
            <div className="mt-7 grid gap-2 text-left sm:grid-cols-3">
              {[
                { icon: Eye, t: "Read-only", d: "We never write to your repository." },
                { icon: FileCode2, t: "No code stored", d: "Only derived indexes and embeddings." },
                { icon: ShieldCheck, t: "Revoke anytime", d: "Access is scoped per repository." },
              ].map((c) => (
                <div key={c.t} className="rounded-md border border-border bg-surface-2/50 p-3">
                  <c.icon className="size-3.5 text-violet-soft" />
                  <p className="mt-2 text-[12.5px] font-medium">{c.t}</p>
                  <p className="mt-0.5 text-[11.5px] text-muted-foreground">{c.d}</p>
                </div>
              ))}
            </div>
            <button
              onClick={() => setStep(1)}
              className="focus-ring mx-auto mt-7 flex items-center gap-2 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-4 py-2.5 text-[13px] font-medium hover:bg-[oklch(0.745_0.19_44)]"
            >
              Get started <ArrowRight className="size-3.5" />
            </button>
          </div>
        ) : null}

        {step === 1 ? (
          <div className="panel ambient-panel p-7">
            <h1 className="text-[20px] font-semibold tracking-tight">Connect GitHub</h1>
            <p className="mt-2 text-[13px] text-muted-foreground">
              CodeLens AI requests the minimum scopes needed to read and analyze source.
            </p>
            <ul className="mt-5 divide-y divide-border rounded-md border border-border">
              {[
                { scope: "repo:read", why: "Clone source and read file contents for indexing." },
                { scope: "metadata:read", why: "List repositories, branches and commit history." },
                {
                  scope: "webhook:write",
                  why: "Re-analyze automatically when a branch receives commits.",
                },
              ].map((s) => (
                <li key={s.scope} className="flex items-start gap-3 px-4 py-3">
                  <Tag tone="violet" mono>
                    {s.scope}
                  </Tag>
                  <p className="text-[12.5px] text-muted-foreground">{s.why}</p>
                </li>
              ))}
            </ul>
            {githubRepos.isSuccess ? (
              <div className="mt-5 flex items-center gap-2">
                <span className="flex items-center gap-1.5 text-[12.5px] text-success">
                  <Check className="size-3.5" /> GitHub already connected
                </span>
                <button
                  onClick={() => setStep(2)}
                  className="focus-ring ml-auto flex items-center gap-2 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-4 py-2.5 text-[13px] font-medium hover:bg-[oklch(0.745_0.19_44)]"
                >
                  Continue <ArrowRight className="size-3.5" />
                </button>
              </div>
            ) : (
              <div className="mt-5 flex items-center gap-2">
                <button
                  onClick={handleAuthorizeGithub}
                  disabled={githubAuthorize.isPending || githubInstall.isPending}
                  className="focus-ring flex items-center gap-2 rounded-md border border-border-strong bg-surface-2 px-4 py-2.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-60"
                >
                  {githubAuthorize.isPending || githubInstall.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Github className="size-4" />
                  )}
                  Authorize GitHub
                </button>
                <span className="font-mono text-[11px] text-subtle">
                  opens github.com in this tab · you'll come back here automatically
                </span>
              </div>
            )}
          </div>
        ) : null}

        {step === 2 ? (
          <div className="panel p-5">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-[17px] font-semibold tracking-tight">Select a repository</h1>
              <div className="ml-auto flex items-center gap-2">
                <div className="flex items-center gap-2 rounded-md border border-input bg-background px-2 py-1.5">
                  <Search className="size-3.5 text-subtle" />
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Filter repositories"
                    className="w-44 bg-transparent font-mono text-[12px] outline-none placeholder:text-subtle"
                  />
                </div>
                <button
                  onClick={() => setOnlyPrivate((v) => !v)}
                  className={cn(
                    "focus-ring rounded-md border px-2.5 py-1.5 text-[12px]",
                    onlyPrivate
                      ? "border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] "
                      : "border-border text-muted-foreground hover:border-border-strong",
                  )}
                >
                  Private only
                </button>
              </div>
            </div>

            {githubRepos.isLoading ? (
              <div className="mt-6 flex items-center justify-center gap-2 py-10 text-[13px] text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Loading repositories from GitHub…
              </div>
            ) : githubRepos.isError ? (
              <div className="mt-6 flex items-center gap-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-3 text-[12.5px] text-danger">
                <AlertCircle className="size-4 shrink-0" />
                Couldn't load repositories. Make sure the GitHub App is installed, then retry.
              </div>
            ) : (
              <ul className="mt-4 space-y-1.5">
                {filtered.map((r) => (
                  <li key={r.github_repo_id}>
                    <button
                      onClick={() => setSelectedRepo(r)}
                      className={cn(
                        "focus-ring flex w-full items-center gap-3 rounded-md border px-3 py-2.5 text-left transition-colors",
                        selectedRepo?.github_repo_id === r.github_repo_id
                          ? "border-violet/45 bg-violet/10"
                          : "border-border hover:border-border-strong hover:bg-surface-2",
                      )}
                    >
                      {r.private ? (
                        <Lock className="size-3.5 text-warning" />
                      ) : (
                        <Globe className="size-3.5 text-info" />
                      )}
                      <span className="font-mono text-[12.5px]">{r.full_name}</span>
                      {r.language ? <Tag tone="neutral">{r.language}</Tag> : null}
                      <span className="flex items-center gap-1 font-mono text-[11px] text-subtle">
                        <GitBranch className="size-3" /> {r.default_branch}
                      </span>
                      <span className="ml-auto font-mono text-[11px] text-subtle">
                        {formatSizeKb(r.size_kb)}
                      </span>
                      <span className="w-24 text-right font-mono text-[11px] text-subtle">
                        {formatPushedAt(r.pushed_at)}
                      </span>
                      {selectedRepo?.github_repo_id === r.github_repo_id ? (
                        <Dot tone="violet" />
                      ) : (
                        <span className="size-1.5" />
                      )}
                    </button>
                  </li>
                ))}
                {filtered.length === 0 ? (
                  <li className="rounded-md border border-border px-3 py-6 text-center text-[12.5px] text-muted-foreground">
                    No repositories match.
                  </li>
                ) : null}
              </ul>
            )}

            <div className="mt-5 flex justify-end">
              <button
                onClick={handleSelectRepoContinue}
                disabled={!selectedRepo || selectRepo.isPending}
                className="focus-ring flex items-center gap-2 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-4 py-2 text-[13px] font-medium hover:bg-[oklch(0.745_0.19_44)] disabled:opacity-60"
              >
                {selectRepo.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
                Continue <ArrowRight className="size-3.5" />
              </button>
            </div>
          </div>
        ) : null}

        {step === 3 ? (
          <div className="panel p-5">
            <h1 className="text-[17px] font-semibold tracking-tight">Select a branch</h1>
            <p className="mt-1.5 font-mono text-[12px] text-subtle">{repository?.full_name}</p>

            {repoBranches.isLoading ? (
              <div className="mt-6 flex items-center justify-center gap-2 py-10 text-[13px] text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Loading branches from GitHub…
              </div>
            ) : repoBranches.isError ? (
              <div className="mt-6 flex items-center gap-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-3 text-[12.5px] text-danger">
                <AlertCircle className="size-4 shrink-0" /> Couldn't load branches. Please retry.
              </div>
            ) : (
              <ul className="mt-4 divide-y divide-border rounded-md border border-border">
                {(repoBranches.data ?? []).map((b) => {
                  const isChosen = selectedBranchName === b.name;
                  return (
                    <li key={b.id}>
                      <button
                        onClick={() => setSelectedBranchName(b.name)}
                        className={cn(
                          "flex w-full items-center gap-3 px-3 py-3 text-left transition-colors",
                          isChosen ? "bg-violet/10" : "hover:bg-surface-2",
                        )}
                      >
                        <GitBranch
                          className={cn("size-3.5", isChosen ? "text-violet-soft" : "text-subtle")}
                        />
                        <span className="w-48 font-mono text-[12.5px]">{b.name}</span>
                        <span className="font-mono text-[11.5px] text-violet-soft">
                          {b.latest_commit_sha.slice(0, 7)}
                        </span>
                        <span className="flex flex-1 items-center gap-1.5">
                          {/* is_default = GitHub's actual default branch; is_selected = the branch
                              currently chosen to analyze (Phase 2.4 fix) — two independent facts. */}
                          {b.is_default ? <Tag tone="info">default on GitHub</Tag> : null}
                          {b.is_selected ? <Tag tone="violet">currently analyzed</Tag> : null}
                        </span>
                        {isChosen ? <Dot tone="violet" /> : <span className="size-1.5" />}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}

            <div className="mt-5 flex justify-end">
              <button
                onClick={handleSelectBranchContinue}
                disabled={!selectedBranchName || selectBranch.isPending}
                className="focus-ring flex items-center gap-2 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-4 py-2 text-[13px] font-medium hover:bg-[oklch(0.745_0.19_44)] disabled:opacity-60"
              >
                {selectBranch.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
                Start analysis <ArrowRight className="size-3.5" />
              </button>
            </div>
          </div>
        ) : null}

        {step === 4 && repository ? (
          <AnalysisStep
            repositoryId={repository.id}
            onDone={(run) => {
              setCompletedRun(run);
              setStep(5);
            }}
            branch={confirmedBranch?.name ?? selectedBranchName ?? ""}
            target={repository.full_name}
          />
        ) : step === 4 ? (
          <div className="panel ambient-panel p-7 text-center text-[13px] text-muted-foreground">
            No repository selected — go back and pick one first.
          </div>
        ) : null}

        {step === 5 ? (
          <div className="panel ambient-panel p-7">
            <span className="flex items-center gap-2 text-[12px] text-success">
              <Dot tone="success" pulse /> Analysis complete
            </span>
            <h1 className="mt-3 text-[24px] font-semibold tracking-tight">
              {repository?.name ?? "Your repository"} is now understandable.
            </h1>
            <p className="mt-2 font-mono text-[12px] text-subtle">
              {repository?.full_name} · {confirmedBranch?.name}
              {confirmedBranch ? ` · ${confirmedBranch.latest_commit_sha.slice(0, 7)}` : ""}
            </p>

            <div className="mt-6 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
              <div className="bg-surface">
                <StatCell
                  label="Files analyzed"
                  value={(completedRun?.file_count ?? 0).toLocaleString()}
                />
              </div>
              <div className="bg-surface">
                <StatCell
                  label="Dependencies"
                  value={(completedRun?.dependency_count ?? 0).toLocaleString()}
                />
              </div>
              <div className="bg-surface">
                <StatCell
                  label="Modules detected"
                  value={(completedRun?.module_count ?? 0).toLocaleString()}
                />
              </div>
              <div className="bg-surface">
                <StatCell
                  label="APIs detected"
                  value={(completedRun?.api_count ?? 0).toLocaleString()}
                />
              </div>
            </div>

            {/* Only a rough debt tier here, deliberately — the real
                Architecture/Technical-Debt health-ring formulas don't exist
                until Phase 6.3/6.4, which is when this panel's ring(s) get
                finalized (see the Phase 6 plan's 6.2 section). */}
            <div className="mt-3 flex items-center gap-3 rounded-lg border border-border bg-surface p-4">
              <Tag tone={debtTier(completedRun?.debt_score ?? 0).tone}>
                {debtTier(completedRun?.debt_score ?? 0).label}
              </Tag>
              <div>
                <p className="text-[13px] font-medium">Technical debt</p>
                <p className="text-[11.5px] text-muted-foreground">
                  Debt score {(completedRun?.debt_score ?? 0).toFixed(1)} · full health scoring
                  lands with the Dashboard
                </p>
              </div>
            </div>

            <button
              onClick={() => navigate({ to: "/app" })}
              className="focus-ring mt-6 flex items-center gap-2 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-4 py-2.5 text-[13px] font-medium hover:bg-[oklch(0.745_0.19_44)]"
            >
              Explore Project <ArrowRight className="size-3.5" />
            </button>
          </div>
        ) : null}
      </div>
    </div>
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

  if (outcome === "failed") {
    return (
      <div className="panel ambient-panel p-7">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-[20px] font-semibold tracking-tight">Analysis failed</h1>
            <p className="mt-1.5 font-mono text-[12px] text-subtle">
              {target} · {branch}
            </p>
          </div>
          <Ring value={pct} size={64} tone="danger" label="!" />
        </div>

        <div className="mt-6 flex items-start gap-2 rounded-md border border-danger/30 bg-danger/10 px-3 py-3 text-[12.5px] text-danger">
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          <span>{errorMessage ?? "The analysis run failed for an unknown reason."}</span>
        </div>

        <button
          onClick={startAnalysis}
          className="focus-ring mt-5 flex items-center gap-2 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-4 py-2.5 text-[13px] font-medium hover:bg-[oklch(0.745_0.19_44)]"
        >
          Retry analysis <ArrowRight className="size-3.5" />
        </button>
      </div>
    );
  }

  return (
    <div className="panel ambient-panel p-7">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight">Analyzing repository</h1>
          <p className="mt-1.5 font-mono text-[12px] text-subtle">
            {target} · {branch}
          </p>
        </div>
        <Ring value={pct} size={64} label={`${pct}%`} />
      </div>

      <div className="mt-6 h-1 overflow-hidden rounded-full bg-surface-3">
        <div
          className="h-full rounded-full bg-violet transition-[width] duration-700"
          style={{ width: `${pct}%` }}
        />
      </div>

      <ul className="mt-6 space-y-1">
        {ANALYSIS_RUN_STAGES.map((stageKey, i) => {
          const done = i < stageIndex || outcome === "completed";
          const running = !done && i === stageIndex;
          return (
            <li
              key={stageKey}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 transition-colors",
                running && "bg-violet/[0.08]",
              )}
            >
              {done ? (
                <Check className="size-3.5 text-success" />
              ) : running ? (
                <Loader2 className="size-3.5 animate-spin text-violet-soft" />
              ) : (
                <Circle className="size-3.5 text-subtle" />
              )}
              <span
                className={cn(
                  "text-[13px]",
                  done ? "text-muted-foreground" : running ? "text-foreground" : "text-subtle",
                )}
              >
                {STAGE_LABELS[stageKey]}
              </span>
            </li>
          );
        })}
      </ul>

      <div className="mt-6 flex items-center justify-between rounded-lg border border-border bg-surface px-4 py-3">
        <span className="font-mono text-[11px] text-subtle">
          {runId ? `Run ${runId.slice(0, 8)}` : "Starting…"}
        </span>
        <span className="font-mono text-[11px] text-subtle">Elapsed {elapsedSeconds}s</span>
      </div>
    </div>
  );
}
