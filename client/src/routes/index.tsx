import { createFileRoute, Link } from "@tanstack/react-router";
import {
  Github,
  ArrowRight,
  Network,
  Sparkles,
  GitBranch,
  Route as RouteIcon,
  Target,
  AlertTriangle,
  BookOpen,
  GraduationCap,
  Files,
  FileWarning,
  Waypoints,
  Check,
} from "lucide-react";

import { ArchGraph } from "@/components/arch-graph";
import { Dot, Panel, PanelHeader, Ring, Sparkline, Tag } from "@/components/ui-kit";
import { PRODUCT, healthMetrics, recentCommits, repo } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "CodeLens AI — Understand Any Codebase. Instantly." },
      {
        name: "description",
        content:
          "AI-powered codebase intelligence: architecture graphs, dependency analysis, feature tracing, impact analysis and generated documentation for any GitHub repository.",
      },
      { property: "og:title", content: "CodeLens AI — Understand Any Codebase. Instantly." },
      {
        property: "og:description",
        content:
          "Connect a GitHub repository and get architecture intelligence, an AI codebase assistant, impact analysis and technical debt insight in minutes.",
      },
    ],
  }),
  component: Landing,
});

const capabilities = [
  { icon: Network, title: "Architecture Intelligence", body: "An inferred, navigable map of services, boundaries and data flows — rebuilt on every commit." },
  { icon: Sparkles, title: "AI Codebase Assistant", body: "Repository-aware answers grounded in real files, with every claim linked to source." },
  { icon: GitBranch, title: "Dependency Analysis", body: "Cycles, coupling hotspots, orphaned modules and outdated external packages with CVE context." },
  { icon: RouteIcon, title: "Feature Tracing", body: "Follow any feature from UI event to database write across every service it touches." },
  { icon: Target, title: "Impact Analysis", body: "Select a file, function or endpoint and see exactly what breaks downstream before you ship." },
  { icon: AlertTriangle, title: "Technical Debt", body: "Scored, prioritized and explained — duplication, complexity, cycles and doc gaps over time." },
  { icon: BookOpen, title: "Automated Documentation", body: "An engineering wiki and API reference generated from the code, not from stale markdown." },
  { icon: GraduationCap, title: "Developer Onboarding", body: "A repository-specific learning path that gets a new engineer to first commit in days." },
];

const problems = [
  { icon: Files, title: "Thousands of files", body: "3,184 files across 47 modules. No single engineer holds the whole system in their head, and tribal knowledge leaves with the last owner." },
  { icon: FileWarning, title: "Outdated documentation", body: "The README describes an architecture from two refactors ago. 112 exported symbols have no documented contract at all." },
  { icon: Waypoints, title: "Unknown impact of changes", body: "Touching auth.service.ts silently affects four modules and three endpoints. Nobody finds out until review — or production." },
];

const useCases = [
  { role: "New Developer", body: "Start with a generated learning path instead of a two-week shadowing period. Understand the architecture before touching it.", metric: "Time to first commit ↓ 64%" },
  { role: "Software Engineer", body: "Trace a feature end-to-end, check impact before refactoring and ask the assistant instead of interrupting the module owner.", metric: "Context switches ↓ 41%" },
  { role: "Senior Developer", body: "Find coupling violations, circular dependencies and complexity hotspots with evidence you can bring to a design review.", metric: "2 cycles surfaced on day one" },
  { role: "Tech Lead", body: "Track architecture health and technical debt across branches, and quantify risk before approving a large change.", metric: "Debt trend visible per release" },
];

const steps = [
  { n: "01", title: "Connect Repository", body: "Authorize GitHub and pick a repository and branch. Read-only access, no code stored." },
  { n: "02", title: "Analyze Codebase", body: "Files indexed, structure detected, dependencies extracted and symbols resolved across languages." },
  { n: "03", title: "Build Intelligence", body: "Architecture graph, embeddings, API surface and debt model constructed for the branch." },
  { n: "04", title: "Understand & Improve", body: "Explore, ask, trace, measure impact and generate documentation your team actually trusts." },
];

const heroMiniNodes = [
  { id: "web", label: "Frontend", x: 44, y: 116, tone: "var(--info)" },
  { id: "gateway", label: "API Gateway", x: 216, y: 116, tone: "var(--violet-soft)" },
  { id: "auth", label: "Auth", x: 390, y: 52, tone: "var(--violet)" },
  { id: "interview", label: "Interview", x: 390, y: 116, tone: "var(--violet-soft)" },
  { id: "billing", label: "Billing", x: 390, y: 180, tone: "var(--warning)" },
  { id: "postgres", label: "Postgres", x: 552, y: 82, tone: "var(--success)" },
  { id: "redis", label: "Redis", x: 552, y: 146, tone: "var(--cyan)" },
] as const;

const heroMiniEdges = [
  ["web", "gateway"],
  ["gateway", "auth"],
  ["gateway", "interview"],
  ["gateway", "billing"],
  ["auth", "postgres"],
  ["auth", "redis"],
  ["interview", "postgres"],
  ["interview", "redis"],
  ["billing", "postgres"],
] as const;

function HeroArchitectureMini() {
  const nodeById = Object.fromEntries(heroMiniNodes.map((n) => [n.id, n]));

  return (
    <div className="relative overflow-hidden rounded-md border border-border/80 bg-background/90 p-2">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(100%_70%_at_30%_0%,rgba(139,92,246,0.15),transparent_65%)]" />
      <svg viewBox="0 0 700 270" className="relative h-full w-full" aria-hidden="true">
        <defs>
          <filter id="hero-node-glow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="2.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {heroMiniEdges.map(([from, to]) => {
          const a = nodeById[from];
          const b = nodeById[to];
          if (!a || !b) return null;
          const x1 = a.x + 124;
          const y1 = a.y + 22;
          const x2 = b.x;
          const y2 = b.y + 22;
          const mid = (x1 + x2) / 2;
          return (
            <path
              key={`${from}-${to}`}
              d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`}
              fill="none"
              stroke="var(--border-strong)"
              strokeWidth="1.6"
              opacity="0.75"
            />
          );
        })}

        {heroMiniNodes.map((n) => (
          <g key={n.id} transform={`translate(${n.x},${n.y})`}>
            <rect x="2" y="2" width="124" height="44" rx="9" fill="rgba(0,0,0,0.22)" opacity="0.7" />
            <rect width="124" height="44" rx="9" fill="color-mix(in oklab, var(--surface) 86%, black 14%)" stroke="var(--border-strong)" strokeWidth="1" />
            <rect x="0" y="0" width="4" height="44" rx="2" fill={n.tone} filter="url(#hero-node-glow)" />
            <text x="12" y="19" style={{ fontSize: 11.8, fontWeight: 620 }} className="fill-foreground">
              {n.label}
            </text>
            <text x="12" y="33" style={{ fontSize: 9.6 }} className="fill-subtle font-mono">
              module
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function ProductPreview() {
  return (
    <div className="relative mx-auto w-full max-w-270 px-1 select-none md:px-4">
      <div className="pointer-events-none absolute inset-x-8 -bottom-5 h-14 rounded-full bg-black/45 blur-2xl" />
      <div className="relative">
        <Panel className="overflow-hidden rounded-2xl border border-white/20 shadow-[0_36px_90px_-36px_rgba(0,0,0,0.85)]" ambient>
            <div className="border-b border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.11),rgba(255,255,255,0.04))] px-4 py-2.5">
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <span className="size-3 rounded-full border border-[#D74336] bg-[#FF5F57] shadow-[inset_0_1px_0_rgba(255,255,255,0.35)]" />
                  <span className="size-3 rounded-full border border-[#C68C1A] bg-[#FEBC2E] shadow-[inset_0_1px_0_rgba(255,255,255,0.35)]" />
                  <span className="size-3 rounded-full border border-[#2C9A41] bg-[#28C840] shadow-[inset_0_1px_0_rgba(255,255,255,0.35)]" />
                </div>

                <div className="mx-auto flex min-w-0 items-center gap-2 rounded-md border border-white/12 bg-black/25 px-2.5 py-1">
                  <Github className="size-3.5 text-muted-foreground" />
                  <span className="truncate font-mono text-[11px] text-muted-foreground">github.com/codelens-ai/{repo.name}</span>
                </div>
              </div>

              <div className="mt-2.5 flex items-center gap-2">
                <Tag tone="violet" className="px-1.5 py-0 text-[10px]">main</Tag>
                <span className="font-mono text-[10.5px] text-subtle">/{repo.name}/architecture-overview</span>
              </div>
            </div>

            <div className="relative grid gap-3 bg-[radial-gradient(130%_100%_at_50%_0%,rgba(139,92,246,0.12),transparent_52%)] p-3.5 lg:grid-cols-[1.55fr_1fr]">
              <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_bottom,rgba(255,255,255,0.035),transparent_14%,transparent_86%,rgba(0,0,0,0.15))]" />

              <div className="relative space-y-3">
                <div className="grid grid-cols-3 gap-2">
                  {healthMetrics.slice(0, 3).map((m) => (
                    <div key={m.key} className="rounded-md border border-border bg-surface-2/75 p-2.5">
                      <p className="label-xs truncate">{m.label}</p>
                      <div className="mt-1.5 flex items-center gap-2">
                        <Ring value={m.value} size={40} tone={m.tone} />
                        <Sparkline points={[54, 58, 57, 63, 61, 68, m.value]} tone={m.tone} width={54} height={22} />
                      </div>
                    </div>
                  ))}
                </div>
                <HeroArchitectureMini />
              </div>

              <div className="relative space-y-3">
                <div className="rounded-md border border-violet/22 bg-violet/[0.07] p-3">
                  <p className="flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-violet-soft uppercase">
                    <span className="size-1.5 rounded-full bg-violet pulse-node" /> AI insight
                  </p>
                  <p className="mt-1.5 text-[12px] leading-relaxed text-secondary-foreground">
                    Authentication module has high coupling with three unrelated services.
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {["auth.service.ts", "billing.service.ts", "analytics.gateway.ts"].map((f) => (
                      <span key={f} className="rounded-sm bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                        {f}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="rounded-md border border-border bg-surface/70">
                  <p className="label-xs border-b border-border px-3 py-2">Recent changes</p>
                  <ul className="divide-y divide-border">
                    {recentCommits.slice(0, 4).map((c) => (
                      <li key={c.sha} className="flex items-center gap-2 px-3 py-2">
                        <span className="font-mono text-[11px] text-violet-soft">{c.sha}</span>
                        <span className="min-w-0 flex-1 truncate text-[11.5px] text-muted-foreground">{c.message}</span>
                        <Tag tone={c.impact === "high" ? "danger" : c.impact === "medium" ? "warning" : "success"}>
                          {c.impact}
                        </Tag>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
        </Panel>
      </div>
    </div>
  );
}

function Landing() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-xl">
        <div className="mx-auto flex h-13 max-w-[1200px] items-center gap-6 px-5 py-2.5">
          <Link to="/" className="flex items-center gap-2">
            <span className="relative grid size-6 place-items-center rounded-[5px] border border-violet/40 bg-violet/15">
              <svg viewBox="0 0 16 16" className="size-3.5">
                <path d="M3 11.5 8 3l5 8.5" fill="none" stroke="var(--violet-soft)" strokeWidth="1.6" strokeLinejoin="round" />
                <circle cx="8" cy="12.4" r="1.4" fill="var(--magenta)" />
              </svg>
            </span>
            <span className="text-[14px] font-semibold tracking-tight">{PRODUCT.name}</span>
          </Link>
          <nav className="hidden items-center gap-5 text-[13px] text-muted-foreground md:flex">
            <a href="#capabilities" className="hover:text-foreground">Capabilities</a>
            <a href="#showcase" className="hover:text-foreground">Product</a>
            <a href="#how" className="hover:text-foreground">How it works</a>
            <a href="#use-cases" className="hover:text-foreground">Use cases</a>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <Link to="/login" className="focus-ring rounded-md px-3 py-1.5 text-[13px] text-muted-foreground hover:text-foreground">
              Sign in
            </Link>
            <Link
              to="/signup"
              className="focus-ring flex items-center gap-1.5 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-3 py-1.5 text-[13px] font-medium  hover:bg-[oklch(0.745_0.19_44)]"
            >
              <Github className="size-3.5" /> Connect GitHub
            </Link>
          </div>
        </div>
      </header>

      {/* HERO */}
      <section className="relative overflow-hidden border-b border-border ambient-hero">
        <div className="absolute inset-0 grid-field opacity-60" />
        <div className="relative mx-auto max-w-300 px-5 pt-22 pb-18">
          <div className="mx-auto max-w-5xl text-center">
            <h1 className="mt-7 text-[56px] leading-[1.02] font-extrabold tracking-[-0.035em] text-foreground md:text-[74px] lg:text-[82px]">
              Understand Any Codebase.
              <br />
              <span className="text-[oklch(0.75_0.19_44)]">Instantly.</span>
            </h1>
            <p className="mx-auto mt-7 max-w-3xl text-[18px] font-medium leading-relaxed text-muted-foreground md:text-[20px]">
              AI-powered codebase intelligence for developers who need to understand, analyze, and
              maintain complex software.
            </p>
            <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
              <Link
                to="/signup"
                className="focus-ring flex items-center gap-2 rounded-md border border-[oklch(0.75_0.19_44)] bg-[oklch(0.75_0.19_44)] px-5 py-3 text-[15px] font-semibold text-[oklch(0.26_0.03_45)] shadow-[0_0_28px_-10px_oklch(0.75_0.19_44)] transition-colors hover:bg-[oklch(0.79_0.19_44)]"
              >
                <Github className="size-4" /> Connect GitHub
              </Link>
              <Link
                to="/app"
                className="focus-ring flex items-center gap-2 rounded-md border border-border bg-surface px-5 py-3 text-[15px] text-foreground transition-colors hover:border-border-strong"
              >
                Explore Demo <ArrowRight className="size-3.5" />
              </Link>
            </div>
            <p className="mt-5 font-mono text-[12px] text-subtle">
              read-only access · no code stored · analysis in ~4 minutes
            </p>
          </div>

          <div className="mt-20">
            <ProductPreview />
          </div>
        </div>
      </section>

      {/* PROBLEM */}
      <section className="border-b border-border">
        <div className="mx-auto max-w-[1200px] px-5 py-16">
          <h2 className="max-w-2xl text-[28px] leading-tight font-semibold tracking-tight">
            Understanding an unfamiliar codebase shouldn&apos;t take weeks.
          </h2>
          <div className="mt-8 grid gap-3 md:grid-cols-3">
            {problems.map((p) => (
              <div key={p.title} className="rounded-lg border border-border bg-surface/60 p-5">
                <p.icon className="size-4 text-danger" />
                <h3 className="mt-3 text-[14.5px] font-semibold">{p.title}</h3>
                <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">{p.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CAPABILITIES */}
      <section id="capabilities" className="border-b border-border">
        <div className="mx-auto max-w-[1200px] px-5 py-16">
          <p className="label-xs">Core capabilities</p>
          <h2 className="mt-2 text-[28px] font-semibold tracking-tight">
            Eight layers of intelligence over one repository.
          </h2>
          <div className="mt-8 grid gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-2 lg:grid-cols-4">
            {capabilities.map((c) => (
              <div key={c.title} className="group bg-surface p-5 transition-colors hover:bg-surface-2">
                <c.icon className="size-4 text-violet-soft" />
                <h3 className="mt-3 text-[13.5px] font-semibold">{c.title}</h3>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">{c.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* SHOWCASE */}
      <section id="showcase" className="border-b border-border">
        <div className="mx-auto max-w-[1200px] space-y-10 px-5 py-16">
          <div className="grid gap-6 lg:grid-cols-[0.85fr_1.15fr] lg:items-center">
            <div>
              <p className="label-xs">Architecture Explorer</p>
              <h3 className="mt-2 text-[22px] font-semibold tracking-tight">
                The system diagram that can&apos;t go stale.
              </h3>
              <p className="mt-3 text-[13.5px] leading-relaxed text-muted-foreground">
                CodeLens AI infers services, boundaries and data flows directly from source. Zoom,
                filter and open any component to see its files, dependents, endpoints and health.
              </p>
              <ul className="mt-4 space-y-2 text-[13px] text-muted-foreground">
                {["Inferred from source on every commit", "Risk-annotated edges for cycles and boundary violations", "Component detail panel with AI-generated reasoning"].map((l) => (
                  <li key={l} className="flex items-start gap-2">
                    <Check className="mt-0.5 size-3.5 text-success" /> {l}
                  </li>
                ))}
              </ul>
            </div>
            <ArchGraph interactive={false} className="h-[320px]" />
          </div>

          <div className="grid gap-6 lg:grid-cols-[1.15fr_0.85fr] lg:items-center">
            <Panel className="overflow-hidden">
              <PanelHeader title="AI Codebase Assistant" subtitle="grounded in 3,184 indexed files" icon={<Sparkles className="size-3.5" />} />
              <div className="space-y-3 p-4">
                <p className="ml-auto w-fit rounded-md rounded-br-sm border border-border bg-surface-2 px-3 py-2 text-[12.5px]">
                  How does authentication work?
                </p>
                <div className="rounded-md border border-violet/20 bg-violet/[0.06] p-3 text-[12.5px] leading-relaxed text-secondary-foreground">
                  Authentication begins in <span className="mono text-violet-soft">auth.controller.ts</span> and
                  passes through <span className="mono text-violet-soft">AuthService</span> before JWT
                  validation in <span className="mono text-violet-soft">jwt.strategy.ts</span>. Guards then
                  resolve scopes per request.
                  <div className="mt-2.5 flex flex-wrap gap-1">
                    {["auth.controller.ts", "auth.service.ts", "jwt.strategy.ts", "auth.guard.ts"].map((f) => (
                      <span key={f} className="rounded-sm border border-border bg-surface px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </Panel>
            <div>
              <p className="label-xs">Repository-aware answers</p>
              <h3 className="mt-2 text-[22px] font-semibold tracking-tight">
                Not a chatbot. A reader of your repository.
              </h3>
              <p className="mt-3 text-[13.5px] leading-relaxed text-muted-foreground">
                Every answer cites the files it was derived from, and every citation opens in the code
                explorer with dependency context attached.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section id="how" className="border-b border-border">
        <div className="mx-auto max-w-[1200px] px-5 py-16">
          <p className="label-xs">How it works</p>
          <div className="mt-6 grid gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-4">
            {steps.map((s) => (
              <div key={s.n} className="bg-surface p-5">
                <span className="font-mono text-[11px] text-violet-soft">{s.n}</span>
                <h3 className="mt-2 text-[14px] font-semibold">{s.title}</h3>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* USE CASES */}
      <section id="use-cases" className="border-b border-border">
        <div className="mx-auto max-w-[1200px] px-5 py-16">
          <p className="label-xs">Built for every seat on the team</p>
          <div className="mt-6 grid gap-3 md:grid-cols-2 lg:grid-cols-4">
            {useCases.map((u) => (
              <div key={u.role} className="rounded-lg border border-border bg-surface/60 p-5">
                <h3 className="text-[14px] font-semibold">{u.role}</h3>
                <p className="mt-2 text-[12.5px] leading-relaxed text-muted-foreground">{u.body}</p>
                <p className="mt-3 font-mono text-[11px] text-violet-soft">{u.metric}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FINAL CTA */}
      <section className="relative overflow-hidden ambient-hero">
        <div className="relative mx-auto max-w-[1200px] px-5 py-20 text-center">
          <h2 className="text-[30px] font-semibold tracking-tight">
            Connect your repository and understand it in minutes.
          </h2>
          <div className="mt-6 flex justify-center gap-2.5">
            <Link
              to="/signup"
              className="focus-ring flex items-center gap-2 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-4 py-2.5 text-[13.5px] font-medium hover:bg-[oklch(0.745_0.19_44)]"
            >
              <Github className="size-4" /> Connect GitHub
            </Link>
            <Link
              to="/app"
              className="focus-ring flex items-center gap-2 rounded-md border border-border bg-surface px-4 py-2.5 text-[13.5px] hover:border-border-strong"
            >
              Explore Demo <ArrowRight className="size-3.5" />
            </Link>
          </div>
        </div>
      </section>

      <footer className="border-t border-border">
        <div className={cn("mx-auto flex max-w-[1200px] flex-wrap items-center gap-4 px-5 py-6 text-[12px] text-subtle")}>
          <span className="font-medium text-muted-foreground">{PRODUCT.name}</span>
          <span>{PRODUCT.tagline}</span>
          <span className="ml-auto font-mono">© 2026 · read-only GitHub access</span>
        </div>
      </footer>
    </div>
  );
}
