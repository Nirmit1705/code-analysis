import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
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

import { PRODUCT } from "@/lib/repo-data";
import { Dot, Ring, StatCell, Tag } from "@/components/ui-kit";
import { cn } from "@/lib/utils";
import { isAuthenticated } from "@/lib/auth";
import { setActiveRepositoryId } from "@/lib/active-repo";
import {
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
});

export const Route = createFileRoute("/onboarding")({
  validateSearch: searchSchema,
  head: () => ({
    meta: [
      { title: "Connect a repository — Substrate" },
      {
        name: "description",
        content:
          "Connect GitHub, pick a repository and branch, and let Substrate build codebase intelligence for it.",
      },
      { property: "og:title", content: "Connect a repository — Substrate" },
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

const stages = [
  { label: "Repository cloned", detail: "184 MB · shallow clone" },
  { label: "Project structure detected", detail: "monorepo · 4 workspaces" },
  { label: "Files indexed", detail: "3,184 files · 412,907 LOC" },
  { label: "Dependencies extracted", detail: "264 packages · 1,912 internal edges" },
  { label: "Building architecture graph", detail: "47 modules · 9 components" },
  { label: "Generating embeddings", detail: "18,402 chunks" },
  { label: "Running technical debt analysis", detail: "duplication · complexity · cycles" },
];

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
  const navigate = useNavigate();
  const search = Route.useSearch();

  const githubInstall = useGithubInstall();
  const githubExchange = useGithubExchange();
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

  // Landing back here after the real GitHub App install round-trip
  // (`GET /auth/github/callback` → this route, Phase 2.5). `exchange_code` is
  // the one-time code the callback hands us in place of raw JWTs (see
  // `server/app/api/v1/github.py::github_exchange`) — trade it for real
  // tokens, then jump straight to repository selection.
  useEffect(() => {
    if (search.exchange_code && !githubExchange.isPending && !githubExchange.isSuccess) {
      githubExchange.mutate(search.exchange_code, {
        onSuccess: () => {
          toast.success("GitHub connected");
          setStep(2);
          void navigate({ to: "/onboarding", search: {}, replace: true });
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

  const handleAuthorizeGithub = () => {
    if (!isAuthenticated()) {
      toast.error("Sign in first, then connect GitHub.");
      void navigate({ to: "/login" });
      return;
    }
    githubInstall.mutate(undefined, {
      onSuccess: (data) => {
        window.location.href = data.install_url;
      },
      onError: () => toast.error("Couldn't start the GitHub App install — please try again."),
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
          <span className="grid size-6 place-items-center rounded-[5px] border border-violet/40 bg-violet/15">
            <svg viewBox="0 0 16 16" className="size-3.5">
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
          <span className="text-[13.5px] font-semibold tracking-tight">{PRODUCT.name}</span>
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
              Substrate reads a repository once, builds an architecture graph, an API surface and a
              debt model, then keeps them current as your team commits.
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
              Substrate requests the minimum scopes needed to read and analyze source.
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
                  disabled={githubInstall.isPending}
                  className="focus-ring flex items-center gap-2 rounded-md border border-border-strong bg-surface-2 px-4 py-2.5 text-[13px] font-medium hover:bg-surface-3 disabled:opacity-60"
                >
                  {githubInstall.isPending ? (
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

        {step === 4 ? (
          <AnalysisStep
            onDone={() => setStep(5)}
            branch={confirmedBranch?.name ?? selectedBranchName ?? ""}
            target={repository?.full_name ?? selectedRepo?.full_name ?? ""}
          />
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
                <StatCell label="Files analyzed" value="3,184" sub="412,907 LOC" />
              </div>
              <div className="bg-surface">
                <StatCell label="Dependencies" value="264" sub="1,912 internal edges" />
              </div>
              <div className="bg-surface">
                <StatCell label="Modules detected" value="47" sub="9 components" />
              </div>
              <div className="bg-surface">
                <StatCell label="APIs detected" value="118" sub="24 public" />
              </div>
            </div>

            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div className="flex items-center gap-3 rounded-lg border border-border bg-surface p-4">
                <Ring value={82} tone="success" />
                <div>
                  <p className="text-[13px] font-medium">Architecture health</p>
                  <p className="text-[11.5px] text-muted-foreground">
                    3 boundary violations detected
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-3 rounded-lg border border-border bg-surface p-4">
                <Ring value={72} tone="warning" label="72" />
                <div>
                  <p className="text-[13px] font-medium">Technical debt</p>
                  <p className="text-[11.5px] text-muted-foreground">
                    Moderate · 6 prioritized issues
                  </p>
                </div>
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

function AnalysisStep({
  onDone,
  branch,
  target,
}: {
  onDone: () => void;
  branch: string;
  target: string;
}) {
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (active >= stages.length) {
      const t = setTimeout(onDone, 700);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setActive((a) => a + 1), active === 0 ? 700 : 1100);
    return () => clearTimeout(t);
  }, [active, onDone]);

  const pct = Math.min(100, Math.round((active / stages.length) * 100));

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
        {stages.map((s, i) => {
          const done = i < active;
          const running = i === active;
          return (
            <li
              key={s.label}
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
                {s.label}
              </span>
              <span className="ml-auto font-mono text-[11px] text-subtle">
                {done || running ? s.detail : "queued"}
              </span>
            </li>
          );
        })}
      </ul>

      <div className="mt-6 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-4">
        <div className="bg-surface">
          <StatCell label="Files indexed" value={active >= 3 ? "3,184" : "1,204"} />
        </div>
        <div className="bg-surface">
          <StatCell label="Symbols resolved" value={active >= 4 ? "41,776" : "18,220"} />
        </div>
        <div className="bg-surface">
          <StatCell label="Graph nodes" value={active >= 5 ? "47" : "12"} />
        </div>
        <div className="bg-surface">
          <StatCell label="Elapsed" value={`${(active * 34 + 12).toString()}s`} />
        </div>
      </div>
    </div>
  );
}
