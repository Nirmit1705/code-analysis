import { Link, useRouterState } from "@tanstack/react-router";
import {
  Gauge,
  DraftingCompass,
  FolderCode,
  Network,
  BotMessageSquare,
  Footprints,
  Radar,
  HandCoins,
  FileDiff,
  BookMarked,
  Webhook,
  Rocket,
  FolderGit2,
  UserCog,
  Search,
  ChevronsUpDown,
  Github,
  History,
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
  {
    section: "Intelligence",
    items: [
      // One distinct icon per page (no repeats), each picked for what the page
      // actually is: a health gauge, an architect's compass, a code folder, a
      // node graph, a chat bot, footprints (trace), a radar sweep (blast
      // radius), coins (debt), a +/- diff, a bookmarked book, a webhook (API),
      // a rocket (getting started), a repo folder, a user cog.
      { to: "/app", label: "Overview", icon: Gauge, exact: true },
      { to: "/app/architecture", label: "Architecture", icon: DraftingCompass },
      { to: "/app/codebase", label: "Codebase", icon: FolderCode },
      { to: "/app/dependencies", label: "Dependencies", icon: Network },
      { to: "/app/assistant", label: "AI Assistant", icon: BotMessageSquare, accent: true },
    ],
  },
  {
    section: "Analysis",
    items: [
      { to: "/app/tracer", label: "Feature Tracer", icon: Footprints },
      { to: "/app/impact", label: "Impact Analysis", icon: Radar },
      { to: "/app/debt", label: "Technical Debt", icon: HandCoins },
      { to: "/app/compare", label: "Compare", icon: FileDiff },
      { to: "/app/history", label: "History", icon: History },
    ],
  },
  {
    section: "Knowledge",
    items: [
      { to: "/app/documentation", label: "Documentation", icon: BookMarked },
      { to: "/app/api", label: "API Reference", icon: Webhook },
      { to: "/app/onboarding", label: "Onboarding", icon: Rocket },
    ],
  },
  {
    section: "Workspace",
    items: [
      { to: "/app/repository", label: "Repository Settings", icon: FolderGit2 },
      { to: "/app/settings", label: "Profile & Settings", icon: UserCog },
    ],
  },
];

function Logo() {
  return (
    <Link to="/" className="focus-ring flex items-center gap-2 rounded-sm">
      <span className="relative grid size-6 place-items-center rounded-[5px] border border-violet/40 bg-violet/15">
        <span className="absolute inset-0 rounded-[5px] bg-violet/25 blur-[6px]" />
        <svg viewBox="0 0 16 16" className="relative size-3.5">
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
    ? formatDistanceToNow(new Date(activeRepository.last_completed_analysis_at), {
        addSuffix: true,
      })
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

// Collapsed width / expanded width of the floating capsule. `AppShell` reserves
// the collapsed width (+ its left gutter) on `main`; expanding *overlays* the
// content rather than pushing it, so pages never reflow on hover.
const NAV_COLLAPSED = "w-[52px]";
const NAV_EXPANDED = "hover:w-[248px] focus-within:w-[248px]";

/** Desktop-only (`lg+`) icon rail in a tall capsule: icons only until the
 * pointer (or keyboard focus) enters it, then it widens to reveal labels.
 * Below `lg` it stays hidden, as the previous sidebar did. */
export function SideNav() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  // Labels fade in with the width; `whitespace-nowrap` + the nav's own
  // `overflow-hidden` keep them from wrapping mid-transition.
  const reveal =
    "whitespace-nowrap opacity-0 transition-opacity duration-150 group-hover/nav:opacity-100 group-focus-within/nav:opacity-100 group-hover/nav:delay-75";

  return (
    <nav
      aria-label="Primary"
      className={cn(
        // Outer shell: width, the soft ring and the inner shadow. It never
        // scrolls — the inner div below does — so the ring stays put.
        "group/nav fixed top-[3.75rem] bottom-4 left-3 z-30 hidden rounded-[26px] bg-surface/80 backdrop-blur-xl transition-[width] duration-200 ease-out lg:block",
        // Inner shadow (rim highlight + soft vignette) plus a diffuse drop shadow.
        "shadow-[inset_0_1px_1px_rgba(255,255,255,0.06),inset_0_0_18px_rgba(0,0,0,0.55),0_18px_40px_-18px_rgba(0,0,0,0.8)]",
        // A 1px grey gradient ring drawn with a masked pseudo-element instead
        // of `border`: brighter at the top-left, fading down, so it reads as
        // a lit edge rather than an outline.
        "before:pointer-events-none before:absolute before:inset-0 before:rounded-[inherit] before:p-px",
        "before:[background:linear-gradient(165deg,rgba(255,255,255,0.22),rgba(255,255,255,0.05)_45%,rgba(255,255,255,0.11))]",
        "before:[mask:linear-gradient(#000_0_0)_content-box,linear-gradient(#000_0_0)] before:[mask-composite:exclude] before:[-webkit-mask-composite:xor]",
        NAV_COLLAPSED,
        NAV_EXPANDED,
      )}
    >
      <div className="flex h-full flex-col overflow-x-hidden overflow-y-auto rounded-[inherit] p-1.5 [scrollbar-width:none]">
        {nav.map((group) => (
          <div key={group.section}>
            {/* A hairline when collapsed, the section name when expanded. */}
            <div className="relative my-1 h-4">
              <span className="absolute inset-x-2.5 top-1/2 h-px bg-border transition-opacity duration-150 group-hover/nav:opacity-0 group-focus-within/nav:opacity-0" />
              <p className={cn("label-xs absolute inset-y-0 left-3 flex items-center", reveal)}>
                {group.section}
              </p>
            </div>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = item.exact ? pathname === item.to : pathname.startsWith(item.to);
                return (
                  <li key={item.to}>
                    <Link
                      to={item.to}
                      aria-label={item.label}
                      title={item.label}
                      className={cn(
                        "focus-ring group relative flex h-10 items-center gap-3 rounded-full pl-[9px] text-[15.5px] transition-colors",
                        active
                          ? "bg-violet/15 text-foreground ring-1 ring-inset ring-violet/25"
                          : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                      )}
                    >
                      <item.icon
                        className={cn(
                          "size-[22px] shrink-0",
                          active
                            ? "text-violet-soft"
                            : "text-subtle group-hover:text-muted-foreground",
                        )}
                      />
                      <span className={cn("truncate", reveal)}>{item.label}</span>
                      {item.accent && !active ? (
                        <span className="absolute top-[7px] left-[28px] size-1.5 rounded-full bg-violet/80 pulse-node" />
                      ) : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
        <div className="mt-auto pt-2">
          <div className="flex h-10 items-center gap-3 rounded-full pl-[9px] text-[15.5px] text-muted-foreground">
            <Command className="size-[22px] shrink-0 text-violet-soft" />
            <span className={cn("flex items-center gap-2", reveal)}>
              Command palette
              <span className="flex items-center gap-1">
                <KeyHint>⌘</KeyHint>
                <KeyHint>K</KeyHint>
              </span>
            </span>
          </div>
        </div>
      </div>
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen ambient-app">
      <TopBar />
      {/* `lg:pl-[76px]` = capsule gutter (12) + collapsed width (52) + gap (12).
          While the capsule is hovered/focused the content's left offset grows to
          272px = gutter (12) + expanded width (248) + gap (12), i.e. by exactly
          the amount the capsule widens, on the same 200ms ease — so the content
          never sits under it. Pure CSS via `:has()`, no shared state. Padding,
          not a transform, so nothing is clipped off the right edge; the page
          reflows to the narrower width instead. Keep 256 in sync with
          `NAV_EXPANDED`. */}
      <div className="flex min-h-[calc(100vh-3rem)] lg:pl-[76px] lg:transition-[padding-left] lg:duration-200 lg:ease-out lg:has-[nav:hover]:pl-[272px] lg:has-[nav:focus-within]:pl-[272px]">
        <SideNav />
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
