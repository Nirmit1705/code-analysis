import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
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
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { setActiveRepositoryId, useActiveRepositoryId } from "@/lib/active-repo";
import { useMe } from "@/lib/queries/auth";
import { useRepositories } from "@/lib/queries/repos";
import { Dot, KeyHint } from "@/components/ui-kit";

type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  exact?: boolean;
  accent?: boolean;
  /** Not built yet: the page is a "coming soon" placeholder, flagged in the nav. */
  soon?: boolean;
};

const nav: Array<{ section: string; items: NavItem[] }> = [
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
      { to: "/app/tracer", label: "Flow Tracer", icon: Footprints },
      { to: "/app/impact", label: "Impact Analysis", icon: Radar },
      { to: "/app/debt", label: "Technical Debt", icon: HandCoins },
      { to: "/app/compare", label: "Compare", icon: FileDiff },
      { to: "/app/history", label: "History", icon: History },
    ],
  },
  {
    section: "Knowledge",
    items: [
      { to: "/app/documentation", label: "Documentation", icon: BookMarked, soon: true },
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
      <img
        src="/codelens-wordmark-on-dark.svg"
        alt="CodeLens AI"
        width={140}
        height={24}
        className="h-[24px] w-auto"
      />
    </Link>
  );
}

/** Two letters for the avatar from the email's local part: "m.okafor" -> "mo",
 * "john" -> "jo". */
function initials(email: string | undefined): string {
  if (!email) return "";
  const local = email.split("@")[0] ?? "";
  const words = local.split(/[._-]+/).filter(Boolean);
  return words.length >= 2 ? `${words[0]![0]}${words[1]![0]}` : local.slice(0, 2);
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
  const me = useMe();
  const navigate = useNavigate();
  const switcherRef = useRef<HTMLDivElement>(null);

  // Close the repository menu on an outside click or Escape.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (!switcherRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // Switching lands on the Overview: other screens' search params (run ids,
  // file paths, endpoints) belong to the repository being left.
  const switchRepository = (repositoryId: string) => {
    setOpen(false);
    if (repositoryId === activeRepositoryId) return;
    setActiveRepositoryId(repositoryId);
    void navigate({ to: "/app" });
  };

  return (
    <header className="sticky top-0 z-40 flex h-12 items-center gap-3 border-b border-border bg-background/85 px-3 backdrop-blur-xl">
      <Logo />
      <span className="h-4 w-px bg-border" />

      <div ref={switcherRef} className="relative">
        <button
          onClick={() => setOpen((v) => !v)}
          className="focus-ring flex h-7 items-center gap-2 rounded-md border border-border bg-surface px-2 text-[12px] hover:border-border-strong"
        >
          <Github className="size-3.5 text-muted-foreground" />
          <span className="font-mono text-[12px]">{displayName}</span>
          <ChevronsUpDown className="size-3 text-subtle" />
        </button>

        {open ? (
          <div className="absolute top-9 left-0 z-50 w-72 rounded-lg border border-border bg-popover p-1 shadow-[var(--shadow-pop)]">
            <p className="label-xs px-2 py-1.5">Connected repositories</p>
            {(repositories.data ?? []).map((r) => (
              <button
                key={r.id}
                onClick={() => switchRepository(r.id)}
                className={cn(
                  "flex w-full items-center justify-between gap-3 rounded-sm px-2 py-1.5 text-left hover:bg-surface-2",
                  r.id === activeRepositoryId && "bg-violet/12 text-violet-soft",
                )}
              >
                <span className="min-w-0">
                  <span className="block truncate font-mono text-[12px]">{r.full_name}</span>
                  <span className="block text-[11px] text-subtle">
                    {r.last_completed_analysis_at
                      ? `Analyzed ${formatDistanceToNow(new Date(r.last_completed_analysis_at), { addSuffix: true })}`
                      : "Not analyzed yet"}
                  </span>
                </span>
                {r.id === activeRepositoryId ? <Dot tone="violet" /> : null}
              </button>
            ))}
            {repositories.data?.length === 0 ? (
              <p className="px-2 py-1.5 text-[11.5px] text-subtle">
                No repositories connected yet.
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

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
            Analyzed {lastAnalyzedLabel}
          </span>
        ) : null}
        <Link
          to="/app/settings"
          title={me.data?.email ?? "Your account"}
          className="focus-ring flex items-center gap-2 rounded-md border border-border bg-surface py-1 pr-2 pl-1 hover:border-border-strong"
        >
          <span className="grid size-5 place-items-center rounded-[4px] bg-violet/20 font-mono text-[10px] text-violet-soft uppercase">
            {initials(me.data?.email)}
          </span>
          <span className="hidden max-w-[16ch] truncate text-[12px] sm:inline">
            {me.data ? me.data.email.split("@")[0] : "Account"}
          </span>
        </Link>
      </div>
    </header>
  );
}

// Collapsed width / expanded width of the floating capsule (each = 16px of bezel+track padding + the item). `AppShell` reserves
// the collapsed width (+ its left gutter) on `main`; expanding *overlays* the
// content rather than pushing it, so pages never reflow on hover.
const NAV_COLLAPSED = "w-[56px]";
const NAV_EXPANDED = "hover:w-[252px] has-[:focus-visible]:w-[252px]";

/** Desktop-only (`lg+`) icon rail in a tall capsule: icons only until the
 * pointer (or keyboard focus) enters it, then it widens to reveal labels.
 * Below `lg` it stays hidden, as the previous sidebar did. */
export function SideNav() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  // Labels fade in with the width; `whitespace-nowrap` + the nav's own
  // `overflow-hidden` keep them from wrapping mid-transition.
  const reveal =
    "whitespace-nowrap opacity-0 transition-opacity duration-150 group-hover/nav:opacity-100 group-has-[:focus-visible]/nav:opacity-100 group-hover/nav:delay-75";

  return (
    <nav
      aria-label="Primary"
      data-side-nav
      className={cn(
        // Outer shell: a raised capsule. Its 5px padding is the visible bezel; the
        // recessed track below sits inside it. The shell's frame is lighter at both
        // the top and the bottom and darker through the middle (a vertical
        // gradient), and floats on a layered drop shadow: a
        // tight contact shadow plus a wide, soft one underneath. Width, rim
        // highlight and shadows live here. It never scrolls — the inner track
        // does — so the bezel stays put.
        "group/nav fixed top-[3.75rem] bottom-4 left-3 z-30 hidden rounded-[30px] bg-gradient-to-b from-surface-3/90 via-surface-2/60 to-surface-3/90 p-[5px] ring-1 ring-white/[0.07] backdrop-blur-xl transition-[width] duration-200 ease-out lg:block",
        "shadow-[inset_0_1px_0_rgba(255,255,255,0.12),inset_0_-1px_0_rgba(0,0,0,0.5),0_3px_8px_rgba(0,0,0,0.5),0_28px_56px_-14px_rgba(0,0,0,0.95)]",
        NAV_COLLAPSED,
        NAV_EXPANDED,
      )}
    >
      {/* The recessed track: a groove the items sit in. A vertical gradient (darkest
          at the top, where the inner shadow falls, lightening toward the bottom)
          plus four inset shadows: a deep soft one from the top edge, a faint
          side vignette, a 1px dark hairline, and a thin light line along the
          bottom lip. `p-[3px]` + the shell's 5px = 8px each side, which is what
          `NAV_COLLAPSED` / the `AppShell` offsets are sized for. */}
      <div className="flex h-full flex-col overflow-x-hidden overflow-y-auto rounded-[25px] bg-gradient-to-b from-background via-background/90 to-surface/70 p-[3px] shadow-[inset_0_8px_16px_-6px_rgba(0,0,0,0.8),inset_0_0_14px_rgba(0,0,0,0.45),inset_0_0_0_1px_rgba(0,0,0,0.4),inset_0_-1px_0_rgba(255,255,255,0.09)] [scrollbar-width:none]">
        {nav.map((group) => (
          <div key={group.section}>
            {/* A hairline when collapsed, the section name when expanded. */}
            <div className="relative my-1 h-4">
              <span className="absolute inset-x-2.5 top-1/2 h-px bg-border transition-opacity duration-150 group-hover/nav:opacity-0 group-has-[:focus-visible]/nav:opacity-0" />
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
                          ? // A raised pebble: a glossy top-lit gradient, a top highlight and a dark
                            // lower edge, lifted off the track by its own shadow.
                            "bg-gradient-to-b from-violet/40 to-violet/14 text-foreground shadow-[inset_0_1px_0_rgba(255,255,255,0.16),inset_0_-1px_0_rgba(0,0,0,0.35),0_8px_16px_-6px_rgba(0,0,0,0.95),0_2px_4px_rgba(0,0,0,0.5)]"
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
                      <span className={cn("flex items-center gap-2 truncate", reveal)}>
                        {item.label}
                        {item.soon ? (
                          <span className="rounded-full border border-violet/30 bg-violet/10 px-1.5 py-px text-[10px] font-medium tracking-wide text-violet-soft uppercase">
                            Soon
                          </span>
                        ) : null}
                      </span>
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
      {/* `lg:pl-[80px]` = capsule gutter (12) + collapsed width (56) + gap (12).
          While the capsule is hovered (or keyboard-focused) the content's left
          offset grows to 276px = gutter (12) + expanded width (252) + gap (12),
          i.e. by exactly the amount the capsule widens, on the same 200ms ease
          — so the content never sits under it. Pure CSS via `:has()`, no shared
          state. Padding, not a transform, so nothing is clipped off the right
          edge; the page reflows to the narrower width instead. Keep 276 in sync
          with `NAV_EXPANDED`.

          Two scoping rules, both learned from real bugs: the selector targets
          `[data-side-nav]`, never a bare `nav` (a page's own `<nav>`, e.g. the
          Dependencies scope tabs, used to trigger the shift on hover), and it
          uses `:focus-visible`, never `:focus-within` (a link clicked with the
          mouse keeps focus, which left the capsule stuck open). */}
      <div className="flex min-h-[calc(100vh-3rem)] lg:pl-[80px] lg:transition-[padding-left] lg:duration-200 lg:ease-out lg:has-[[data-side-nav]:hover]:pl-[276px] lg:has-[[data-side-nav]_:focus-visible]:pl-[276px]">
        <SideNav />
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
