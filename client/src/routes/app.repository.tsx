import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";

import { Panel } from "@/components/ui-kit";
import { clearActiveRepositoryId, setActiveRepositoryId, useActiveRepositoryId } from "@/lib/active-repo";
import { ApiError } from "@/lib/api";
import { useCreateAnalysisRun } from "@/lib/queries/analysis";
import {
  useDisconnectRepository,
  usePurgeRepository,
  useRepoSettings,
  useUpdateRepoSettings,
  type AnalysisMode,
  type InstallationStatus,
  type RepositorySettingsRead,
} from "@/lib/queries/repoSettings";
import { useRepoBranches, useRepositories, useSelectBranch } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/repository")({
  head: () => ({
    meta: [
      { title: "Repository Settings — CodeLens AI" },
      { name: "description", content: "When this repository is analyzed, on which branch, which files are left out, and how to start over or disconnect." },
      { property: "og:title", content: "Repository Settings — CodeLens AI" },
      { property: "og:description", content: "Control how CodeLens AI analyzes this repository." },
    ],
  }),
  component: RepositorySettings,
});

const fmt = (n: number) => n.toLocaleString("en-US");
const plural = (n: number, one: string, many = `${one}s`) => `${fmt(n)} ${n === 1 ? one : many}`;
const shortSha = (sha: string) => sha.slice(0, 7);
const day = (iso: string) => new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
const when = (iso: string) =>
  `${new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" })} at ${new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;

/** The message a failed request should show: FastAPI's `detail` string, or the
 * first validation message of a 422. */
function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { detail?: unknown; errors?: { msg?: unknown }[] } | null;
    const first = detail?.errors?.[0]?.msg;
    if (typeof first === "string") return first.replace(/^Value error, /, "");
    if (typeof detail?.detail === "string" && detail.detail !== "Validation error") return detail.detail;
  }
  return fallback;
}

function RepositorySettings() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const settings = useRepoSettings(activeRepositoryId);

  return (
    <div className="mx-auto max-w-[1280px] p-4 pb-16">
      {repositories.isLoading ? (
        <Message>Loading…</Message>
      ) : activeRepositoryId === null ? (
        <Message link="Connect a repository">No repository connected yet.</Message>
      ) : settings.isLoading ? (
        <Message>Loading the repository settings…</Message>
      ) : settings.isError || !settings.data ? (
        <Message danger>Failed to load the repository settings.</Message>
      ) : (
        <SettingsBody settings={settings.data} repositoryId={activeRepositoryId} />
      )}
    </div>
  );
}

function Message({ children, link, danger }: { children: React.ReactNode; link?: string; danger?: boolean }) {
  return (
    <Panel>
      <div className="p-10 text-center">
        <p className={cn("text-[13px]", danger ? "text-danger" : "text-subtle")}>{children}</p>
        {link ? (
          <Link to="/onboarding" className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline">
            {link}
          </Link>
        ) : null}
      </div>
    </Panel>
  );
}

const NO_ACCESS: InstallationStatus[] = ["not_installed", "suspended", "reinstalled"];

function Headline({ s }: { s: RepositorySettingsRead }) {
  const branch = s.selected_branch ? (
    <code className="rounded-[10px] bg-surface-2 px-[0.25em] font-mono text-[0.86em] font-semibold tracking-[-0.03em]">
      {s.selected_branch}
    </code>
  ) : null;
  let text: React.ReactNode;
  if (NO_ACCESS.includes(s.installation)) text = <>{s.name} can't be analyzed until CodeLens AI has access to it again.</>;
  else if (!branch) text = <>Pick a branch for {s.name} to be analyzed on.</>;
  else if (s.analysis_mode === "on_push") text = <>{s.name} is analyzed again every time someone pushes to {branch}.</>;
  else text = <>{s.name} is analyzed only when you start it, on {branch}.</>;
  // Keyed on the setting, so a change replays the short entrance once.
  return (
    <h1
      key={`${s.analysis_mode}:${s.selected_branch}:${s.installation}`}
      className="max-w-[24ch] text-[28px] leading-[1.1] font-semibold tracking-[-0.028em] text-balance animate-in fade-in slide-in-from-bottom-1 duration-300 motion-reduce:animate-none"
    >
      {text}
    </h1>
  );
}

function SettingsBody({ settings: s, repositoryId }: { settings: RepositorySettingsRead; repositoryId: string }) {
  const queryClient = useQueryClient();
  const createRun = useCreateAnalysisRun(repositoryId);
  const [runError, setRunError] = useState<string | null>(null);

  const analyzeNow = async () => {
    setRunError(null);
    try {
      await createRun.mutateAsync();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["repo-settings", repositoryId] }),
        queryClient.invalidateQueries({ queryKey: ["analysis-runs", repositoryId] }),
      ]);
    } catch (error) {
      setRunError(errorText(error, "Couldn't start the analysis. Try again in a moment."));
    }
  };

  return (
    <div className="grid gap-[26px] pt-3">
      <header>
        <Headline s={s} />
        <p className="mt-2.5 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[11px] text-muted-foreground">
          {s.run_in_flight ? (
            <>
              <span className="text-warning">
                An analysis{s.selected_branch ? ` of ${s.selected_branch}` : ""} is running now ({s.run_in_flight.status.replace(/_/g, " ")}).
              </span>
              <Link to="/app/history" className="focus-ring text-violet-soft hover:underline">
                Follow it in History
              </Link>
            </>
          ) : s.last_analysis?.completed_at ? (
            <>
              <span>
                Last analyzed {when(s.last_analysis.completed_at)}, commit{" "}
                <span className="font-mono text-secondary-foreground">{shortSha(s.last_analysis.commit_sha)}</span>,{" "}
                {plural(s.last_analysis.file_count, "file")}.
              </span>
              <Link to="/app/history" className="focus-ring text-violet-soft hover:underline">
                History
              </Link>
            </>
          ) : (
            <>
              <span>Not analyzed yet.</span>
              <button
                type="button"
                onClick={() => void analyzeNow()}
                disabled={createRun.isPending || !s.selected_branch}
                className="focus-ring text-violet-soft hover:underline disabled:opacity-50"
              >
                Analyze now
              </button>
            </>
          )}
          {runError ? <span className="text-danger">{runError}</span> : null}
        </p>
      </header>

      <div className="grid items-start gap-[22px] lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="grid min-w-0 gap-[18px]">
          <WhenSection s={s} repositoryId={repositoryId} />
          <FilesSection s={s} repositoryId={repositoryId} onAnalyze={analyzeNow} analyzing={createRun.isPending} />
          <RiskySection s={s} repositoryId={repositoryId} />
        </div>
        <ConnectionPanel s={s} />
      </div>
    </div>
  );
}

function Section({ title, sub, aside, className, children }: { title: string; sub?: string; aside?: React.ReactNode; className?: string; children: React.ReactNode }) {
  return (
    <section className={cn("min-w-0 rounded-[14px] border border-border bg-surface px-[22px] pt-5 pb-[22px]", className)}>
      <div className="flex items-baseline gap-3">
        <h2 className="text-[14.5px] font-semibold tracking-[-0.01em]">{title}</h2>
        {aside}
      </div>
      {sub ? <p className="mt-0.5 text-[10px] text-subtle">{sub}</p> : null}
      {children}
    </section>
  );
}

/** "Saved", shown for a moment after a change lands. */
function useFlash(): [boolean, () => void] {
  const [on, setOn] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);
  return [
    on,
    () => {
      setOn(true);
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setOn(false), 1600);
    },
  ];
}

function Radio({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-4 shrink-0 place-items-center rounded-full border-[1.5px]",
        on ? "border-violet after:size-2 after:rounded-full after:bg-violet after:content-['']" : "border-border-strong",
      )}
    />
  );
}

// --- when it's analyzed ---------------------------------------------------------------

function WhenSection({ s, repositoryId }: { s: RepositorySettingsRead; repositoryId: string }) {
  const queryClient = useQueryClient();
  const update = useUpdateRepoSettings(repositoryId);
  const branches = useRepoBranches(repositoryId);
  const selectBranch = useSelectBranch(repositoryId);
  const [saved, flash] = useFlash();
  const [error, setError] = useState<string | null>(null);
  const branchName = s.selected_branch ?? "the selected branch";

  const setMode = async (mode: AnalysisMode) => {
    if (mode === s.analysis_mode || update.isPending) return;
    setError(null);
    try {
      await update.mutateAsync({ analysis_mode: mode });
      flash();
    } catch (e) {
      setError(errorText(e, "Couldn't save the change. Try again."));
    }
  };

  const pickBranch = async (name: string) => {
    if (name === s.selected_branch || selectBranch.isPending) return;
    setError(null);
    try {
      await selectBranch.mutateAsync(name);
      await queryClient.invalidateQueries({ queryKey: ["repo-settings", repositoryId] });
      flash();
    } catch (e) {
      setError(errorText(e, "Couldn't switch the branch. Try again."));
    }
  };

  const modes: { value: AnalysisMode; title: string; body: string }[] = [
    {
      value: "on_push",
      title: `Every push to ${branchName}`,
      body: "GitHub tells CodeLens AI about each push and the new commit is analyzed. History marks these runs as push.",
    },
    {
      value: "manual",
      title: "Only when you ask",
      body: "Pushes are ignored. Start an analysis with Re-analyze on the Overview or History screen.",
    },
  ];

  const list = branches.data ?? [];

  return (
    <Section
      title="When it's analyzed"
      aside={
        <span className={cn("text-[10px] text-success transition-opacity", saved ? "opacity-100" : "opacity-0")} aria-live="polite">
          {saved ? "Saved" : ""}
        </span>
      }
    >
      <div role="radiogroup" aria-label="When to analyze" className="mt-4 grid gap-2.5 sm:grid-cols-2">
        {modes.map((m) => {
          const on = s.analysis_mode === m.value;
          return (
            <button
              key={m.value}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => void setMode(m.value)}
              disabled={update.isPending}
              className={cn(
                "focus-ring grid grid-cols-[16px_minmax(0,1fr)] gap-x-3 gap-y-[3px] rounded-xl border px-4 pt-3.5 pb-[15px] text-left transition-colors disabled:cursor-wait",
                on ? "border-violet/55 bg-violet/[0.07]" : "border-border bg-background hover:border-border-strong",
              )}
            >
              <span className="pt-[3px]">
                <Radio on={on} />
              </span>
              <span className="text-[12.5px] font-semibold">{m.title}</span>
              <span className="col-start-2 text-[10px] leading-normal text-muted-foreground">{m.body}</span>
            </button>
          );
        })}
      </div>

      <div className="mt-5 border-t border-border pt-[18px]">
        <h3 className="mb-2.5 text-[11px] font-semibold text-secondary-foreground">Branch</h3>
        {branches.isLoading ? (
          <p className="text-[11px] text-subtle">Loading the branches from GitHub…</p>
        ) : branches.isError || list.length === 0 ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[11px] text-muted-foreground">
            {s.selected_branch ? (
              <span className="rounded-[7px] bg-surface-2 px-[9px] py-1 font-mono text-[11px] text-foreground">{s.selected_branch}</span>
            ) : null}
            <span className={branches.isError ? "text-danger" : undefined}>
              {branches.isError ? "Couldn't load the branch list from GitHub." : "GitHub returned no branches for this repository."}
            </span>
          </div>
        ) : list.length === 1 ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[11px] text-muted-foreground">
            <span className="rounded-[7px] bg-surface-2 px-[9px] py-1 font-mono text-[11px] text-foreground">{list[0]!.name}</span>
            {list[0]!.is_default ? <span className="rounded-full bg-surface-3 px-2 py-px text-[9px] text-secondary-foreground">default</span> : null}
            <span className="font-mono tabular-nums">{shortSha(list[0]!.latest_commit_sha)}</span>
            <span>The only branch on GitHub, so every analysis runs on it.</span>
          </div>
        ) : (
          <>
            <div role="radiogroup" aria-label="Branch to analyze" className="grid gap-1">
              {list.map((b) => {
                const on = b.name === s.selected_branch;
                return (
                  <button
                    key={b.id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => void pickBranch(b.name)}
                    disabled={selectBranch.isPending}
                    className={cn(
                      "focus-ring grid grid-cols-[16px_minmax(0,1fr)_auto] items-center gap-3 rounded-[9px] border px-3 py-[9px] text-left disabled:cursor-wait",
                      on ? "border-border-strong bg-surface-2" : "border-transparent hover:bg-surface-2",
                    )}
                  >
                    <Radio on={on} />
                    <span className="truncate font-mono text-[11px]">{b.name}</span>
                    <span className="flex items-center gap-2.5 text-[10px] text-subtle">
                      {b.is_default ? <span className="rounded-full bg-surface-3 px-2 py-px text-[9px] text-secondary-foreground">default</span> : null}
                      <span className="font-mono tabular-nums">{shortSha(b.latest_commit_sha)}</span>
                    </span>
                  </button>
                );
              })}
            </div>
            <p className="mt-2.5 max-w-[70ch] text-[10px] text-subtle">
              The next analysis runs on the branch you pick. Analyses of other branches stay in History. Pushes to any other branch are ignored.
            </p>
          </>
        )}
      </div>
      {error ? <p className="mt-3 text-[10.5px] text-danger">{error}</p> : null}
    </Section>
  );
}

// --- files it leaves out ----------------------------------------------------------------

type LineError = { line: number; message: string };

/** The server's rules (`ingestion.normalize_ignore_patterns`), checked here
 * line by line so a mistake is marked before Save. The server validates again. */
function checkPatterns(text: string, max: number, maxLength: number): { patterns: string[]; errors: LineError[] } {
  const patterns: string[] = [];
  const errors: LineError[] = [];
  text.split("\n").forEach((raw, i) => {
    let p = raw.trim();
    if (!p || p.startsWith("#")) return;
    const line = i + 1;
    if (p.length > maxLength) return void errors.push({ line, message: `Longer than ${maxLength} characters.` });
    // eslint-disable-next-line no-control-regex
    if (/[\u0000-\u001f\u007f]/.test(p)) return void errors.push({ line, message: "Contains a control character." });
    if (p.startsWith("!")) return void errors.push({ line, message: "Patterns starting with ! (keep this file) aren't supported." });
    p = p.replace(/\\/g, "/");
    while (p.startsWith("./")) p = p.slice(2);
    p = p.replace(/\/+$/, "");
    if (!p.replace(/[*/]/g, "")) return void errors.push({ line, message: "This would leave out every file." });
    if (!patterns.includes(p)) patterns.push(p);
  });
  if (patterns.length > max) errors.push({ line: 0, message: `At most ${max} patterns (this has ${patterns.length}).` });
  return { patterns, errors };
}

function FilesSection({
  s,
  repositoryId,
  onAnalyze,
  analyzing,
}: {
  s: RepositorySettingsRead;
  repositoryId: string;
  onAnalyze: () => Promise<void>;
  analyzing: boolean;
}) {
  const update = useUpdateRepoSettings(repositoryId);
  const [draft, setDraft] = useState(() => s.ignore_patterns.join("\n"));
  const [justSaved, setJustSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gutterRef = useRef<HTMLDivElement>(null);

  const { patterns, errors } = useMemo(
    () => checkPatterns(draft, s.max_ignore_patterns, s.max_ignore_pattern_length),
    [draft, s.max_ignore_patterns, s.max_ignore_pattern_length],
  );
  const dirty = patterns.join("\n") !== s.ignore_patterns.join("\n");
  const errorLines = new Set(errors.map((e) => e.line));
  const lineCount = Math.max(draft.split("\n").length, 4);

  const save = async () => {
    setError(null);
    try {
      const saved = await update.mutateAsync({ ignore_patterns: draft.split("\n") });
      setDraft(saved.ignore_patterns.join("\n"));
      setJustSaved(true);
    } catch (e) {
      setError(errorText(e, "Couldn't save the patterns. Try again."));
    }
  };

  return (
    <Section
      title="Files it leaves out"
      sub="Generated code, fixtures, vendored copies: anything that would skew the numbers. One pattern per line."
    >
      <div className="mt-4 grid items-start gap-x-6 gap-y-5 md:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="min-w-0">
        <div className="grid grid-cols-[34px_minmax(0,1fr)] overflow-hidden rounded-[10px] border border-border bg-background focus-within:border-violet/60">
          <div
            ref={gutterRef}
            aria-hidden
            className="overflow-hidden border-r border-border bg-surface py-3 pr-2 text-right font-mono text-[10px] leading-[21px] text-subtle select-none"
          >
            {Array.from({ length: lineCount }, (_, i) => (
              <div key={i} className={errorLines.has(i + 1) ? "text-danger" : undefined}>
                {i + 1}
              </div>
            ))}
          </div>
          <textarea
            id="ignore-patterns"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setJustSaved(false);
            }}
            onScroll={(e) => {
              if (gutterRef.current) gutterRef.current.scrollTop = e.currentTarget.scrollTop;
            }}
            rows={8}
            wrap="off"
            spellCheck={false}
            aria-label="Ignore patterns, one per line"
            aria-describedby="ignore-help"
            placeholder={"# For example\nserver/__tests__\n*.min.js\nclient/public"}
            className="min-h-[210px] resize-y bg-transparent px-3 py-3 font-mono text-[10.5px] leading-[21px] outline-none placeholder:text-subtle"
          />
        </div>
        {errors.length ? (
          <ul className="mt-2 grid gap-[3px] text-[10px] text-danger">
            {errors.map((e) => (
              <li key={`${e.line}:${e.message}`}>{e.line ? `Line ${e.line}: ${e.message}` : e.message}</li>
            ))}
          </ul>
        ) : null}
        </div>
        <div className="grid min-w-0 gap-4">
          <div>
            <h3 className="mb-1.5 text-[11px] font-semibold text-secondary-foreground">How a pattern matches</h3>
            <ul id="ignore-help" className="grid gap-1.5 text-[10px] leading-relaxed text-muted-foreground">
              <li>
                A name without a slash matches that file or folder anywhere:{" "}
                <span className="font-mono text-secondary-foreground">fixtures</span>,{" "}
                <span className="font-mono text-secondary-foreground">*.min.js</span>.
              </li>
              <li>
                A path with a slash starts at the top of the repository and covers everything inside it:{" "}
                <span className="font-mono text-secondary-foreground">server/__tests__</span>.
              </li>
              <li>
                <span className="font-mono text-secondary-foreground">*</span> matches any characters, including{" "}
                <span className="font-mono text-secondary-foreground">/</span>. Lines starting with{" "}
                <span className="font-mono text-secondary-foreground">#</span> are notes and are ignored.
              </li>
            </ul>
          </div>
          <div>
            <h3 className="mb-1.5 text-[11px] font-semibold text-secondary-foreground">Always left out</h3>
            <p className="mb-2 text-[10px] text-subtle">Dependency, build and editor folders. These can't be changed.</p>
            <div className="flex flex-wrap gap-[5px]">
              {s.default_ignore_patterns.map((d) => (
                <span key={d} className="rounded-[5px] bg-surface-2 px-[7px] py-0.5 font-mono text-[9px] text-secondary-foreground">
                  {d}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-[18px] flex flex-wrap items-center gap-x-3.5 gap-y-2.5 border-t border-border pt-4">
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty || errors.length > 0 || update.isPending}
          className="focus-ring rounded-lg bg-violet px-4 py-2 text-[11px] font-semibold text-primary-foreground transition-colors hover:bg-violet-soft disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-violet"
        >
          {update.isPending ? "Saving…" : "Save patterns"}
        </button>
        {error ? (
          <span className="text-[10px] text-danger">{error}</span>
        ) : justSaved && !dirty ? (
          <span className="text-[10px] text-success">
            Saved. They apply from the next analysis.{" "}
            {s.run_in_flight ? null : (
              <button type="button" onClick={() => void onAnalyze()} disabled={analyzing} className="focus-ring text-violet-soft hover:underline disabled:opacity-50">
                Re-analyze now
              </button>
            )}
          </span>
        ) : (
          <span className="text-[10px] text-muted-foreground">
            {errors.length ? "Fix the marked lines to save." : "They apply from the next analysis. Files already analyzed stay until then."}
          </span>
        )}
      </div>
    </Section>
  );
}

// --- start over or disconnect -------------------------------------------------------------

type Risky = "purge" | "disconnect";

function RiskySection({ s, repositoryId }: { s: RepositorySettingsRead; repositoryId: string }) {
  const repositories = useRepositories();
  const purge = usePurgeRepository(repositoryId);
  const disconnect = useDisconnectRepository(repositoryId);
  const [open, setOpen] = useState<Risky | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [purged, setPurged] = useState(false);
  const locked = s.run_in_flight !== null;
  const busy = purge.isPending || disconnect.isPending;

  const toggle = (which: Risky) => {
    setOpen((cur) => (cur === which ? null : which));
    setTyped("");
    setError(null);
  };

  const run = async (which: Risky) => {
    setError(null);
    try {
      if (which === "purge") {
        await purge.mutateAsync();
        setPurged(true);
        setOpen(null);
        setTyped("");
      } else {
        await disconnect.mutateAsync();
        // A full navigation, so every screen re-resolves the active repository.
        const next = (repositories.data ?? []).find((r) => r.id !== repositoryId);
        clearActiveRepositoryId();
        if (next) setActiveRepositoryId(next.id);
        window.location.assign(next ? "/app" : "/onboarding");
      }
    } catch (e) {
      setError(errorText(e, which === "purge" ? "Couldn't delete the history. Try again." : "Couldn't disconnect the repository. Try again."));
    }
  };

  const rows: { key: Risky; title: string; body: React.ReactNode; open: string; confirm: string }[] = [
    {
      key: "purge",
      title: "Delete history and analyze again",
      body: (
        <>
          {s.analysis_run_count === 0 ? (
            <>{s.name} has no analyses to delete yet. </>
          ) : s.analysis_run_count === 1 ? (
            <>Deletes the only analysis of {s.name}, and with it History, the debt trend and the AI notes. </>
          ) : (
            <>
              Deletes all <b className="font-semibold text-secondary-foreground">{plural(s.analysis_run_count, "analysis", "analyses")}</b> of{" "}
              {s.name}, and with them History, the debt trend and the AI notes.{" "}
            </>
          )}
          Your settings and onboarding progress stay. Then{" "}
          <b className="font-semibold text-secondary-foreground">{s.selected_branch ?? "the selected branch"}</b> is analyzed from scratch.
        </>
      ),
      open: "Delete history…",
      confirm: s.analysis_run_count ? `Delete ${plural(s.analysis_run_count, "analysis", "analyses")}` : "Analyze from scratch",
    },
    {
      key: "disconnect",
      title: `Disconnect ${s.name}`,
      body: (
        <>
          Removes {s.name} and everything analyzed for it from CodeLens AI. The GitHub App stays installed on GitHub; remove
          it there if you also want to revoke access.
        </>
      ),
      open: "Disconnect…",
      confirm: `Disconnect ${s.name}`,
    },
  ];

  return (
    <Section title="Start over or disconnect" sub="Neither can be undone." className="border-danger/30">
      <div className="mt-2 grid">
        {rows.map((r, i) => (
          <div
            key={r.key}
            className={cn("grid grid-cols-1 items-center gap-x-[22px] gap-y-1.5 py-4 sm:grid-cols-[minmax(0,1fr)_auto]", i > 0 && "border-t border-border")}
          >
            <div className="min-w-0">
              <h3 className="text-[12.5px] font-semibold">{r.title}</h3>
              <p className="mt-[3px] max-w-[66ch] text-[10px] leading-[1.55] text-muted-foreground">{r.body}</p>
              {locked ? <p className="mt-1 text-[10px] text-warning">An analysis is running. This unlocks when it finishes.</p> : null}
            </div>
            <button
              type="button"
              onClick={() => toggle(r.key)}
              disabled={locked || busy}
              aria-expanded={open === r.key}
              className="focus-ring justify-self-start rounded-lg border border-danger/45 px-4 py-2 text-[11px] font-semibold text-danger transition-colors hover:bg-danger/10 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent sm:justify-self-end"
            >
              {r.open}
            </button>
            {open === r.key && !locked ? (
              <form
                className="col-span-full mt-1.5 grid gap-2.5 rounded-[10px] border border-danger/30 bg-danger/[0.07] px-4 py-3.5"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (typed === s.name && !busy) void run(r.key);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") toggle(r.key);
                }}
              >
                <label htmlFor={`confirm-${r.key}`} className="text-[11px] text-secondary-foreground">
                  Type <code className="font-mono text-foreground">{s.name}</code> to confirm.
                </label>
                <div className="flex flex-wrap items-center gap-2.5">
                  <input
                    id={`confirm-${r.key}`}
                    autoFocus
                    autoComplete="off"
                    spellCheck={false}
                    value={typed}
                    onChange={(e) => setTyped(e.target.value)}
                    className="min-w-0 flex-[1_1_220px] rounded-lg border border-border-strong bg-background px-[11px] py-2 font-mono text-[11px] outline-none focus:border-danger/60"
                  />
                  <button
                    type="button"
                    onClick={() => toggle(r.key)}
                    className="focus-ring rounded-lg border border-border-strong px-4 py-2 text-[11px] font-semibold text-secondary-foreground hover:bg-surface-2"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={typed !== s.name || busy}
                    className="focus-ring rounded-lg bg-danger px-4 py-2 text-[11px] font-semibold text-destructive-foreground disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    {busy ? "Working…" : r.confirm}
                  </button>
                </div>
              </form>
            ) : null}
            {r.key === "purge" && purged && locked ? (
              <p className="col-span-full text-[11px] text-success">
                History deleted. {s.selected_branch ?? "The selected branch"} is being analyzed from scratch.{" "}
                <Link to="/app/history" className="focus-ring text-violet-soft hover:underline">
                  Follow it in History
                </Link>
              </p>
            ) : null}
          </div>
        ))}
      </div>
      {error ? <p className="text-[10.5px] text-danger">{error}</p> : null}
    </Section>
  );
}

// --- connection ---------------------------------------------------------------------------

const STATUS: Record<InstallationStatus, { color: string; title: string; body: string }> = {
  connected: { color: "var(--success)", title: "Connected", body: "The CodeLens AI GitHub App can read this repository." },
  not_installed: {
    color: "var(--danger)",
    title: "No access",
    body: "The GitHub App was removed from this repository on GitHub, so analyses will fail. Give the app access to the repository again on GitHub to continue.",
  },
  suspended: {
    color: "var(--warning)",
    title: "Suspended on GitHub",
    body: "The GitHub App is suspended for this account. Analyses fail until it's unsuspended on GitHub.",
  },
  reinstalled: {
    color: "var(--warning)",
    title: "Reconnect needed",
    body: "The GitHub App was reinstalled since this repository was connected. Disconnect it and connect it again.",
  },
  unknown: { color: "var(--subtle)", title: "Couldn't check", body: "GitHub didn't answer just now. Analyses may still work." },
};

function ConnectionPanel({ s }: { s: RepositorySettingsRead }) {
  const status = STATUS[s.installation];
  const rows: [string, React.ReactNode][] = [
    [
      "Repository",
      <a key="r" href={`https://github.com/${s.full_name}`} target="_blank" rel="noreferrer" className="focus-ring font-mono text-violet-soft hover:underline">
        {s.full_name}
      </a>,
    ],
    ["Visibility", s.private ? "Private" : "Public"],
    ["Default branch", <span key="b" className="font-mono">{s.default_branch}</span>],
    [
      "Analyses",
      <span key="a">
        <span className="tabular-nums">{fmt(s.analysis_run_count)}</span>
        <Link to="/app/history" className="focus-ring ml-2 text-[10px] text-violet-soft hover:underline">
          History
        </Link>
      </span>,
    ],
    [
      "Last analysis",
      s.last_analysis?.completed_at ? (
        <span key="l">
          <span className="font-mono">{shortSha(s.last_analysis.commit_sha)}</span>
          <small className="block text-[10px] text-subtle">
            {day(s.last_analysis.completed_at)}, {plural(s.last_analysis.file_count, "file")}
          </small>
        </span>
      ) : (
        "None yet"
      ),
    ],
  ];

  return (
    <aside aria-labelledby="connection-title" className="max-lg:-order-1 min-w-0 overflow-hidden rounded-[14px] border border-border bg-surface lg:sticky lg:top-[4.25rem]">
      <div
        className="grid gap-1.5 border-b border-border px-5 pt-[18px] pb-4"
        style={{ background: `linear-gradient(180deg, color-mix(in oklch, ${status.color} 10%, var(--surface)) 0%, var(--surface) 100%)` }}
      >
        <h2 id="connection-title" className="flex items-center gap-[9px] text-[14.5px] font-semibold">
          <span
            className="size-[9px] rounded-full"
            style={{ background: status.color, boxShadow: `0 0 0 4px color-mix(in oklch, ${status.color} 18%, transparent)` }}
          />
          {status.title}
        </h2>
        <p className="text-[10px] leading-[1.55] text-muted-foreground">{status.body}</p>
      </div>
      <dl className="grid px-5 pt-2 pb-1.5">
        {rows.map(([k, v], i) => (
          <div key={k} className={cn("grid grid-cols-[112px_minmax(0,1fr)] gap-3 py-[9px] text-[11px]", i > 0 && "border-t border-border")}>
            <dt className="pt-0.5 text-[10px] text-subtle">{k}</dt>
            <dd className="min-w-0 break-words">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="px-5 pb-4 text-[9px] text-subtle">Access is checked with GitHub each time this page opens.</p>
    </aside>
  );
}
