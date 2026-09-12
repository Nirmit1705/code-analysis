import { Link, useRouterState } from "@tanstack/react-router";
import {
  LayoutDashboard,
  Network,
  FolderTree,
  GitBranch,
  Sparkles,
  Route as RouteIcon,
  Target,
  AlertTriangle,
  BookOpen,
  GitCompareArrows,
  GraduationCap,
  Settings,
  Search,
  ChevronsUpDown,
  Github,
  History,
  Plug,
  Command,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { PRODUCT } from "@/lib/repo-data";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { useRepositories } from "@/lib/queries/repos";
import { Dot, KeyHint } from "@/components/ui-kit";

const nav = [
  { section: "Intelligence", items: [
    { to: "/app", label: "Overview", icon: LayoutDashboard, exact: true },
    { to: "/app/architecture", label: "Architecture", icon: Network },
    { to: "/app/codebase", label: "Codebase", icon: FolderTree },
    { to: "/app/dependencies", label: "Dependencies", icon: GitBranch },
    { to: "/app/assistant", label: "AI Assistant", icon: Sparkles, accent: true },
  ] },
  { section: "Analysis", items: [
    { to: "/app/tracer", label: "Feature Tracer", icon: RouteIcon },
    { to: "/app/impact", label: "Impact Analysis", icon: Target },
    { to: "/app/debt", label: "Technical Debt", icon: AlertTriangle },
    { to: "/app/compare", label: "Compare", icon: GitCompareArrows },
    { to: "/app/history", label: "History", icon: History },
  ] },
  { section: "Knowledge", items: [
    { to: "/app/documentation", label: "Documentation", icon: BookOpen },
    { to: "/app/api", label: "API Reference", icon: Plug },
    { to: "/app/onboarding", label: "Onboarding", icon: GraduationCap },
  ] },
  { section: "Workspace", items: [
    { to: "/app/repository", label: "Repository Settings", icon: Settings },
    { to: "/app/settings", label: "Profile & Settings", icon: Settings },
  ] },
];

function Logo() {
  return (
    <Link to="/" className="focus-ring flex items-center gap-2 rounded-sm">
      <span className="relative grid size-6 place-items-center rounded-[5px] border border-violet/40 bg-violet/15">
        <span className="absolute inset-0 rounded-[5px] bg-violet/25 blur-[6px]" />
        <svg viewBox="0 0 16 16" className="relative size-3.5">
          <path d="M3 11.5 8 3l5 8.5" fill="none" stroke="var(--violet-soft)" strokeWidth="1.6" strokeLinejoin="round" />
          <circle cx="8" cy="12.4" r="1.4" fill="var(--magenta)" />
        </svg>
      </span>
      <span className="text-[13.5px] font-semibold tracking-tight">{PRODUCT.name}</span>
    </Link>
  );
}

export function TopBar() {
  const [open, setOpen] = useState(false);
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const activeRepository = repositories.data?.find((r) => r.id === activeRepositoryId) ?? null;
  const displayName = repositories.isLoading
    ? "Loading…"
    : (activeRepository?.full_name ?? "No repository connected");
  const lastAnalyzedLabel = activeRepository?.last_completed_analysis_at
    ? formatDistanceToNow(new Date(activeRepository.last_completed_analysis_at), { addSuffix: true })
    : "not yet analyzed";
  const githubConnected = (repositories.data?.length ?? 0) > 0;

  return (
    <header className="sticky top-0 z-40 flex h-12 items-center gap-3 border-b border-border bg-background/85 px-3 backdrop-blur-xl">
      <Logo />
      <span className="h-4 w-px bg-border" />

      <button
        onClick={() => setOpen((v) => !v)}
        className="focus-ring flex h-7 items-center gap-2 rounded-md border border-border bg-surface px-2 text-[12px] hover:border-border-strong"
      >
        <Github className="size-3.5 text-muted-foreground" />
        <span className="font-mono text-[12px]">{displayName}</span>
        <ChevronsUpDown className="size-3 text-subtle" />
      </button>

      {/* Repo-switcher stays a non-functional visual placeholder (Phase 6.1
          scope note): it now lists the user's *real* connected repositories
          instead of hardcoded names, but clicking one only closes the menu —
          there's no multi-repo-switching UI yet (see `@/lib/active-repo`). */}
      {open ? (
        <div className="absolute top-11 left-40 z-50 w-72 rounded-lg border border-border bg-popover p-1 shadow-[var(--shadow-pop)]">
          <p className="label-xs px-2 py-1.5">Connected repositories</p>
          {(repositories.data ?? []).map((r) => (
            <button
              key={r.id}
              onClick={() => setOpen(false)}
              className={cn(
                "flex w-full items-center justify-between rounded-sm px-2 py-1.5 font-mono text-[12px] hover:bg-surface-2",
                r.id === activeRepositoryId && "bg-violet/12 text-violet-soft",
              )}
            >
              {r.full_name}
              {r.id === activeRepositoryId ? <Dot tone="violet" /> : null}
            </button>
          ))}
          {repositories.data?.length === 0 ? (
            <p className="px-2 py-1.5 text-[11.5px] text-subtle">No repositories connected yet.</p>
          ) : null}
        </div>
      ) : null}

      <div className="mx-auto hidden w-full max-w-md items-center gap-2 rounded-md border border-border bg-surface px-2 py-1.5 text-[12px] text-subtle transition-colors hover:border-border-strong md:flex">
        <Search className="size-3.5" />
        <span>Search files, modules, APIs, symbols…</span>
        <span className="ml-auto flex items-center gap-1">
          <KeyHint>⌘</KeyHint>
          <KeyHint>K</KeyHint>
        </span>
      </div>

      <div className="ml-auto flex items-center gap-2">
        {githubConnected ? (
          <span className="hidden items-center gap-1.5 rounded-md border border-success/25 bg-success/10 px-2 py-1 text-[11px] text-success lg:flex">
            <Dot tone="success" pulse /> GitHub connected
          </span>
        ) : null}
        {activeRepository ? (
          <span className="hidden font-mono text-[11px] text-subtle xl:inline">
            analyzed {lastAnalyzedLabel}
          </span>
        ) : null}
        <button className="focus-ring flex items-center gap-2 rounded-md border border-border bg-surface py-1 pr-2 pl-1 hover:border-border-strong">
          <span className="grid size-5 place-items-center rounded-[4px] bg-violet/20 font-mono text-[10px] text-violet-soft">
            MO
          </span>
          <span className="text-[12px]">m.okafor</span>
        </button>
      </div>
    </header>
  );
}

export function SideNav() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  return (
    <nav className="hidden w-[212px] shrink-0 flex-col gap-4 overflow-y-auto border-r border-border bg-surface/40 px-2 py-3 lg:flex">
      {nav.map((group) => (
        <div key={group.section}>
          <p className="label-xs px-2 pb-1.5">{group.section}</p>
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const active = item.exact ? pathname === item.to : pathname.startsWith(item.to);
              return (
                <li key={item.to}>
                  <Link
                    to={item.to}
                    className={cn(
                      "focus-ring group relative flex items-center gap-2 rounded-md px-2 py-1.5 text-[12.5px] transition-colors",
                      active
                        ? "bg-violet/12 text-foreground"
                        : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                    )}
                  >
                    {active ? (
                      <span className="absolute top-1.5 bottom-1.5 -left-2 w-[2px] rounded-r bg-violet" />
                    ) : null}
                    <item.icon
                      className={cn(
                        "size-3.5 shrink-0",
                        active ? "text-violet-soft" : "text-subtle group-hover:text-muted-foreground",
                      )}
                    />
                    <span className="truncate">{item.label}</span>
                    {item.accent && !active ? (
                      <span className="ml-auto size-1.5 rounded-full bg-violet/70 pulse-node" />
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <div className="mt-auto rounded-md border border-border bg-surface p-2.5">
        <p className="flex items-center gap-1.5 text-[11px] font-medium text-foreground">
          <Command className="size-3 text-violet-soft" /> Command palette
        </p>
        <p className="mt-1 text-[11px] leading-relaxed text-subtle">
          Jump to any file, module or API with <KeyHint>⌘K</KeyHint>
        </p>
      </div>
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen ambient-app">
      <TopBar />
      <div className="flex min-h-[calc(100vh-3rem)]">
        <SideNav />
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
