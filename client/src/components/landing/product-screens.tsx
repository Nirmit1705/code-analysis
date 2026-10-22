/**
 * The product screens shown on the landing page. Each one reproduces a real screen of the app
 * (`app-shell.tsx`, `arch-graph.tsx`, `app.assistant.tsx`, `app.impact.tsx`, `app.tracer.tsx`,
 * `onboarding.tsx`) at the width the app gives it, using the app's own colours, layout and
 * displayed type sizes, filled with demo data. Styling lives in `landing.css` (scoped under `.lp`).
 * They are decorative copies, so they take no data from the backend and are hidden from assistive tech.
 */
import {
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  BookMarked,
  BotMessageSquare,
  Boxes,
  Check,
  ChevronsUpDown,
  Circle,
  Cloud,
  Command,
  Copy,
  CornerDownRight,
  Database,
  DraftingCompass,
  FileCheck2,
  FileCode2,
  FileDiff,
  FolderCode,
  FolderGit2,
  Footprints,
  Gauge,
  GitBranch,
  Github,
  HandCoins,
  History,
  Layers,
  Loader2,
  Maximize2,
  Minus,
  Monitor,
  Network,
  Plug,
  Plus,
  Radar,
  Rocket,
  RotateCcw,
  Route as RouteIcon,
  Search,
  ShieldCheck,
  TriangleAlert,
  UserCog,
  Webhook,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** Lays a screen out at the width the real app has, then scales it down to the space available. */
export function ScaledScreen({
  width,
  crop,
  nativeHeight,
  className,
  innerClassName,
  label,
  children,
}: {
  width: number;
  /** Show only the top `crop` px of the screen (the bottom fades out in CSS). */
  crop?: number;
  /** Rough height before measuring, so the page doesn't jump while it loads. */
  nativeHeight: number;
  className?: string;
  innerClassName?: string;
  label: string;
  children: ReactNode;
}) {
  const outer = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState<{ scale: number; height: number } | null>(null);

  useIsoLayoutEffect(() => {
    const o = outer.current;
    const i = inner.current;
    if (!o || !i) return;
    const measure = () => {
      const scale = Math.min(1, o.clientWidth / width);
      setFit({ scale, height: (crop ?? i.offsetHeight) * scale });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(o);
    void document.fonts?.ready.then(measure);
    return () => ro.disconnect();
  }, [width, crop]);

  return (
    <div
      ref={outer}
      role="img"
      aria-label={label}
      className={cn("fit", className)}
      style={
        fit
          ? { height: fit.height }
          : { aspectRatio: `${width} / ${crop ?? nativeHeight}`, visibility: "hidden" }
      }
    >
      <div
        ref={inner}
        aria-hidden="true"
        className={cn("fit-in", innerClassName)}
        style={{ width, transform: `scale(${fit?.scale ?? 1})` }}
      >
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Architecture Explorer inside the app shell
// ---------------------------------------------------------------------------

const NODE_W = 216;
const NODE_H = 64;
const COLUMN_GAP = 136;
const ROW_PITCH = 124;
const TOP = 98;
const MARGIN_X = 96;

type Kind = "frontend" | "gateway" | "service" | "data" | "cache" | "external";

const KIND: Record<Kind, { color: string; icon: LucideIcon }> = {
  frontend: { color: "oklch(0.76 0.12 250)", icon: Monitor },
  gateway: { color: "oklch(0.77 0.17 52)", icon: Webhook },
  service: { color: "oklch(0.74 0.15 305)", icon: Boxes },
  data: { color: "oklch(0.76 0.15 155)", icon: Database },
  cache: { color: "oklch(0.8 0.11 205)", icon: Zap },
  external: { color: "oklch(0.75 0.16 350)", icon: Cloud },
};

const COLUMNS = ["Frontend", "Gateway / API", "Services", "Data"];

const GRAPH_NODES: Array<{
  id: string;
  label: string;
  kind: Kind;
  col: number;
  row: number;
  stats: string;
  circular?: boolean;
}> = [
  {
    id: "web",
    label: "React Frontend",
    kind: "frontend",
    col: 0,
    row: 1.5,
    stats: "1,042 files · 118k loc",
    circular: true,
  },
  {
    id: "routes",
    label: "API Routes",
    kind: "gateway",
    col: 1,
    row: 1.5,
    stats: "148 files · 14k loc",
  },
  {
    id: "auth",
    label: "Auth Service",
    kind: "service",
    col: 2,
    row: 0.5,
    stats: "96 files · 9.8k loc",
  },
  {
    id: "interview",
    label: "Interview Service",
    kind: "service",
    col: 2,
    row: 1.5,
    stats: "214 files · 21k loc",
  },
  {
    id: "billing",
    label: "Billing Service",
    kind: "service",
    col: 2,
    row: 2.5,
    stats: "87 files · 7.4k loc",
  },
  { id: "pg", label: "PostgreSQL", kind: "data", col: 3, row: 0, stats: "41 files · 3.2k loc" },
  { id: "redis", label: "Redis", kind: "cache", col: 3, row: 1, stats: "12 files · 920 loc" },
  { id: "stripe", label: "Stripe", kind: "external", col: 3, row: 2, stats: "6 files · 480 loc" },
  {
    id: "deepgram",
    label: "Deepgram",
    kind: "external",
    col: 3,
    row: 3,
    stats: "3 files · 210 loc",
  },
];

// [from, to, number of file-level imports]. An edge reads "from depends on to".
const GRAPH_EDGES: Array<[string, string, number]> = [
  ["web", "routes", 31],
  ["routes", "auth", 12],
  ["routes", "interview", 9],
  ["routes", "billing", 5],
  ["auth", "pg", 14],
  ["auth", "redis", 6],
  ["interview", "pg", 11],
  ["interview", "redis", 4],
  ["interview", "deepgram", 3],
  ["billing", "pg", 5],
  ["billing", "stripe", 7],
];

const FOCUS = "auth";
const EDGE_OUT = "oklch(0.762 0.163 46)";
const EDGE_IN = "oklch(0.78 0.12 235)";
const EDGE_BASE = "oklch(0.74 0.012 60)";

interface PlacedNode {
  id: string;
  label: string;
  kind: Kind;
  col: number;
  stats: string;
  circular?: boolean;
  x: number;
  y: number;
}

interface PlacedEdge {
  d: string;
  state: "out" | "in" | "dim";
  width: number;
  label: string;
  lane: number;
  mid: number;
}

/** The same layered layout the app uses: one column per layer, a shared row grid, and every edge
 * given its own lane in the gutter beside its column so no two vertical runs overlap. */
function layoutGraph() {
  const nodes: PlacedNode[] = GRAPH_NODES.map((n) => ({
    ...n,
    x: MARGIN_X + n.col * (NODE_W + COLUMN_GAP),
    y: TOP + n.row * ROW_PITCH,
  }));
  const byId = new Map(nodes.map((n) => [n.id, n]));

  type Work = {
    from: PlacedNode;
    to: PlacedNode;
    weight: number;
    y0: number;
    y1: number;
    lane: number;
  };
  const work: Work[] = GRAPH_EDGES.map(([f, t, weight]) => ({
    from: byId.get(f)!,
    to: byId.get(t)!,
    weight,
    y0: 0,
    y1: 0,
    lane: 0,
  }));

  // Spread edge ends along a node's side.
  const spread = (end: "from" | "to", other: "to" | "from", key: "y0" | "y1") => {
    const groups = new Map<string, Work[]>();
    for (const e of work) groups.set(e[end].id, [...(groups.get(e[end].id) ?? []), e]);
    for (const list of groups.values()) {
      list.sort((a, b) => a[other].y - b[other].y);
      const pad = 12;
      const span = NODE_H - pad * 2;
      list.forEach((e, i) => {
        e[key] = e[end].y + pad + (list.length === 1 ? span / 2 : (span * i) / (list.length - 1));
      });
    }
  };
  spread("from", "to", "y0");
  spread("to", "from", "y1");

  // One gutter lane per edge between two neighbouring columns.
  const gutters = new Map<number, Work[]>();
  for (const e of work) gutters.set(e.from.col, [...(gutters.get(e.from.col) ?? []), e]);
  const lanes = new Map<Work, number>();
  for (const [col, list] of gutters) {
    list.sort((a, b) => a.y0 - b.y0 || a.y1 - b.y1);
    list.forEach((e, i) => {
      lanes.set(
        e,
        MARGIN_X +
          col * (NODE_W + COLUMN_GAP) +
          NODE_W +
          (COLUMN_GAP / (list.length + 1)) * (i + 1),
      );
    });
  }

  const r = 9;
  const edges: PlacedEdge[] = work.map((e) => {
    const lx = lanes.get(e)!;
    const x1 = e.from.x + NODE_W;
    const x2 = e.to.x;
    let d: string;
    if (Math.abs(e.y1 - e.y0) < 2) {
      d = `M${x1} ${e.y0} H${x2}`;
    } else {
      const s = e.y1 > e.y0 ? 1 : -1;
      d = `M${x1} ${e.y0} H${lx - r} Q${lx} ${e.y0} ${lx} ${e.y0 + s * r} V${e.y1 - s * r} Q${lx} ${e.y1} ${lx + r} ${e.y1} H${x2}`;
    }
    const state = e.from.id === FOCUS ? "out" : e.to.id === FOCUS ? "in" : "dim";
    return {
      d,
      state,
      width: 1.25 + Math.min(2, Math.log2(Math.max(1, e.weight)) * 0.55),
      label: `×${e.weight}`,
      lane: lx,
      mid: (e.y0 + e.y1) / 2,
    };
  });
  // Highlighted edges are painted last so they sit on top of the dim ones.
  edges.sort((a, b) => Number(a.state !== "dim") - Number(b.state !== "dim"));

  const connected = new Set<string>();
  for (const [f, t] of GRAPH_EDGES) {
    if (f === FOCUS) connected.add(t);
    if (t === FOCUS) connected.add(f);
  }

  const height = TOP + 3 * ROW_PITCH + NODE_H + 58;
  const width = MARGIN_X * 2 + COLUMNS.length * NODE_W + (COLUMNS.length - 1) * COLUMN_GAP;
  return { nodes, edges, connected, width, height };
}

const GRAPH = layoutGraph();

function ArchitectureGraph() {
  const { nodes, edges, connected, width, height } = GRAPH;
  return (
    <div className="gv">
      <div className="world" style={{ width, height, transform: "translate(-430px, 22px)" }}>
        {COLUMNS.map((label, i) => (
          <div
            key={label}
            className="lane"
            style={{
              left: MARGIN_X + i * (NODE_W + COLUMN_GAP) - 16,
              width: NODE_W + 32,
              top: 10,
              height: height - 20,
            }}
          >
            <div>
              <b>{label}</b>
              <span>{nodes.filter((n) => n.col === i).length}</span>
            </div>
          </div>
        ))}

        <svg className="ed" width={width} height={height}>
          <defs>
            {(
              [
                ["base", EDGE_BASE],
                ["out", EDGE_OUT],
                ["in", EDGE_IN],
              ] as const
            ).map(([name, color]) => (
              <marker
                key={name}
                id={`lp-mk-${name}`}
                viewBox="0 0 10 10"
                refX={9}
                refY={5}
                markerWidth={10}
                markerHeight={10}
                orient="auto"
                markerUnits="userSpaceOnUse"
              >
                <path d="M1,1.2 L9.2,5 L1,8.8 Z" fill={color} />
              </marker>
            ))}
          </defs>
          {edges.map((e, i) => {
            const hot = e.state !== "dim";
            const color = e.state === "out" ? EDGE_OUT : e.state === "in" ? EDGE_IN : EDGE_BASE;
            const labelWidth = e.label.length * 7.2 + 18;
            return (
              <g key={i} opacity={hot ? 1 : 0.07}>
                <path
                  d={e.d}
                  fill="none"
                  stroke={color}
                  strokeWidth={hot ? e.width + 0.8 : e.width}
                  strokeLinecap="round"
                  markerEnd={`url(#lp-mk-${hot ? e.state : "base"})`}
                  className={e.state === "out" ? "hot-edge" : undefined}
                />
                {hot ? (
                  <g transform={`translate(${e.lane},${e.mid})`}>
                    <rect
                      x={-labelWidth / 2}
                      y={-11}
                      width={labelWidth}
                      height={22}
                      rx={11}
                      fill="oklch(0.218 0.006 50)"
                      stroke={color}
                    />
                    <text textAnchor="middle" dy={4.2}>
                      {e.label}
                    </text>
                  </g>
                ) : null}
              </g>
            );
          })}
        </svg>

        {nodes.map((n) => {
          const { color, icon: Icon } = KIND[n.kind];
          const selected = n.id === FOCUS;
          const dim = !selected && !connected.has(n.id);
          return (
            <div
              key={n.id}
              className="nd"
              style={{
                left: n.x,
                top: n.y,
                opacity: dim ? 0.35 : 1,
                background: `linear-gradient(135deg, color-mix(in oklab, ${color} 17%, var(--surface)) 0%, var(--surface) 72%)`,
                border: `1px solid color-mix(in oklab, ${color} ${selected ? 80 : 28}%, var(--line))`,
                boxShadow: selected
                  ? `0 0 0 3px color-mix(in oklab, ${color} 30%, transparent), 0 14px 30px -12px color-mix(in oklab, ${color} 55%, black)`
                  : "0 8px 18px -10px rgba(0,0,0,.85), inset 0 1px 0 oklch(1 0 0 / .05)",
              }}
            >
              <span
                className="it"
                style={{
                  color,
                  background: `color-mix(in oklab, ${color} 20%, transparent)`,
                  border: `1px solid color-mix(in oklab, ${color} 38%, transparent)`,
                }}
              >
                <Icon className="ic" strokeWidth={1.9} />
              </span>
              <span className="tx">
                <b>{n.label}</b>
                <span>{n.stats}</span>
              </span>
              {n.circular ? (
                <span className="bd">
                  <TriangleAlert className="ic" strokeWidth={2.4} />
                </span>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="legend">
        <div>
          <span>
            <svg width="24" height="8">
              <path d="M0 4H16" stroke={EDGE_BASE} strokeWidth="1.6" opacity=".85" />
              <path d="M15 .6 23 4 15 7.4Z" fill={EDGE_BASE} opacity=".85" />
            </svg>
            Depends on
          </span>
          <span>
            <svg width="24" height="8">
              <path d="M0 4H24" stroke={EDGE_BASE} strokeWidth="3.4" opacity=".85" />
            </svg>
            Thicker = more imports
          </span>
          <span>
            <svg width="24" height="8">
              <path d="M0 4H24" stroke="var(--danger)" strokeWidth="1.8" strokeDasharray="4 3" />
            </svg>
            Circular
          </span>
        </div>
        <p>
          Hover a component to trace it: <span style={{ color: "var(--accent-hi)" }}>orange</span> =
          what it depends on, <span style={{ color: "var(--edge-in)" }}>blue</span> = what depends
          on it.
        </p>
      </div>
      <div className="zoom">
        <i>
          <Minus className="ic" />
        </i>
        <span>100%</span>
        <i>
          <Plus className="ic" />
        </i>
        <i>
          <Maximize2 className="ic" />
        </i>
      </div>
    </div>
  );
}

const RAIL: Array<Array<{ icon: LucideIcon; active?: boolean; dot?: boolean }>> = [
  [
    { icon: Gauge },
    { icon: DraftingCompass, active: true },
    { icon: FolderCode },
    { icon: Network },
    { icon: BotMessageSquare, dot: true },
  ],
  [
    { icon: Footprints },
    { icon: Radar },
    { icon: HandCoins },
    { icon: FileDiff },
    { icon: History },
  ],
  [{ icon: BookMarked }, { icon: Webhook }, { icon: Rocket }],
  [{ icon: FolderGit2 }, { icon: UserCog }],
];

export function ArchitectureScreen() {
  return (
    <>
      <div className="tb">
        <span className="lg">
          <img
            src="/codelens-wordmark-on-dark.svg"
            alt="CodeLens AI"
            width={140}
            height={24}
            className="wordmark"
          />
        </span>
        <span className="vr" />
        <span className="rp">
          <Github className="ic" />
          hiresight/hiresight-platform
          <ChevronsUpDown className="ic sm" />
        </span>
        <span className="sr">
          <Search className="ic" style={{ fontSize: 14 }} />
          Search files, modules, APIs, symbols…
          <span className="kk">
            <span className="kb">⌘</span>
            <span className="kb">K</span>
          </span>
        </span>
        <span className="rt">
          <span className="gh">GitHub connected</span>
          <span className="an">Analyzed 14 minutes ago</span>
          <span className="ac">
            <i>mo</i>m.okafor
          </span>
        </span>
      </div>

      <nav className="cap">
        <div>
          {RAIL.map((group, gi) => (
            <div key={gi}>
              <div className="sp" style={gi === 0 ? { marginTop: 0 } : undefined} />
              {group.map(({ icon: Icon, active, dot }, i) => (
                <div key={i} className={cn("it", active && "on")}>
                  <Icon className="ic" />
                  {dot ? <span className="dt" /> : null}
                </div>
              ))}
            </div>
          ))}
          <div className="sp" />
          <div className="it" style={{ color: "var(--accent-hi)" }}>
            <Command className="ic" />
          </div>
        </div>
      </nav>

      <div className="pg">
        <div className="hd">
          <div>
            <h1>Architecture Explorer</h1>
            <p className="sub">
              9 components · 11 inferred edges · 1 circular dependency (14 files)
            </p>
          </div>
          <div className="r">
            <span className="in">
              <Search className="ic" style={{ fontSize: 14 }} />
              Search components
            </span>
            <span className="sg">
              <span className="on">
                <Layers className="ic" style={{ fontSize: 12 }} />
                by service
              </span>
              <span>
                <Layers className="ic" style={{ fontSize: 12 }} />
                by module
              </span>
            </span>
          </div>
        </div>

        <div className="chips">
          {(
            [
              ["Frontend", "frontend"],
              ["Gateway", "gateway"],
              ["Services", "service"],
              ["Data", "data"],
              ["Cache", "cache"],
              ["External", "external"],
            ] as const
          ).map(([label, kind]) => (
            <span key={kind} className="chip">
              <i style={{ background: KIND[kind].color }} />
              {label}
            </span>
          ))}
          <span className="hint">Scroll to zoom · drag to pan · click a node for detail</span>
        </div>

        <div className="body">
          <ArchitectureGraph />
          <div className="pn ins">
            <div className="ph">
              <div className="l">
                <div>
                  <h2>Auth Service</h2>
                  <p>service · 96 files</p>
                </div>
              </div>
              <X className="ic" style={{ color: "var(--subtle)", fontSize: 14 }} />
            </div>
            <div className="bd">
              <div className="two">
                <div className="bx">
                  <span className="lx">Files</span>
                  <b>96</b>
                </div>
                <div className="bx">
                  <span className="lx">Lines of code</span>
                  <b>9,812</b>
                </div>
              </div>
              <div>
                <span className="lx">Key files</span>
                <ul className="kf">
                  <li>src/auth/auth.controller.ts</li>
                  <li>src/auth/auth.service.ts</li>
                  <li>src/auth/jwt.strategy.ts</li>
                </ul>
              </div>
              <div className="two">
                <div>
                  <span className="lx">Dependencies</span>
                  <div className="tags">
                    <span className="tg">PostgreSQL</span>
                    <span className="tg">Redis</span>
                  </div>
                </div>
                <div>
                  <span className="lx">Dependents</span>
                  <div className="tags">
                    <span className="tg">API Routes</span>
                  </div>
                </div>
              </div>
              <div>
                <span className="lx">APIs</span>
                <div className="api">
                  <span>POST /api/login</span>
                  <span>POST /api/register</span>
                  <span>POST /api/token/refresh</span>
                </div>
              </div>
              <div className="ai">
                <b>AI insight</b>
                <p>
                  Handles sign-in and token refresh. Most of its dependents reach it through API
                  Routes, so changes here show up at the edge first.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// AI Assistant
// ---------------------------------------------------------------------------

const ASSISTANT_SOURCES = [
  { dir: "src/auth/", file: "auth.controller.ts", symbol: "login", lines: "L24–61" },
  { dir: "src/auth/", file: "auth.service.ts", symbol: "validateUser", lines: "L12–48" },
  { dir: "src/auth/", file: "auth.service.ts", symbol: "issueTokens", lines: "L50–72" },
  { dir: "src/auth/", file: "jwt.strategy.ts", symbol: "validate", lines: "L9–31" },
];

export function AssistantScreen() {
  return (
    <>
      <section className="main">
        <div className="hdr">
          <h1>AI Assistant</h1>
          <span className="br">
            <GitBranch className="ic" />
            main · a3f91c2
          </span>
        </div>
        <div className="col">
          <div className="qr">
            <span className="av">MO</span>
            <h2>How does authentication work?</h2>
          </div>
          <div className="card">
            <div className="ch">
              <span className="nm">
                <i>
                  <BotMessageSquare className="ic" />
                </i>
                CodeLens AI
              </span>
              <span className="pl">
                <FileCheck2 className="ic" style={{ fontSize: 14 }} />
                Sources shown
              </span>
              <span className="act">
                <span>
                  <Copy className="ic" style={{ fontSize: 14 }} />
                  Copy
                </span>
                <span>
                  <RotateCcw className="ic" style={{ fontSize: 14 }} />
                  Ask again
                </span>
              </span>
            </div>
            <div className="bd">
              <p>
                Sign-in starts at <strong>POST /api/login</strong> in{" "}
                <code>auth.controller.ts</code>, which passes the credentials to{" "}
                <code>AuthService.validateUser</code>.
              </p>
              <ul>
                <li>
                  <code>validateUser</code> loads the user through the users repository and checks
                  the password hash.
                </li>
                <li>
                  <code>issueTokens</code> then signs an access token and a refresh token for the
                  session.
                </li>
                <li>
                  On later requests, <code>jwt.strategy.ts</code> validates the access token and{" "}
                  <code>auth.guard.ts</code> checks the caller&apos;s scopes.
                </li>
              </ul>
            </div>
            <div className="kg">
              <p className="lx">Keep going</p>
              <div className="q">
                <CornerDownRight className="ic" />
                How does the refresh token rotation work?
              </div>
              <div className="q">
                <CornerDownRight className="ic" />
                <span>
                  Which endpoints are protected by{" "}
                  <code style={{ font: "400 .92em var(--mono)", color: "var(--accent-hi)" }}>
                    auth.guard.ts
                  </code>
                  ?
                </span>
              </div>
            </div>
          </div>
          <div className="cmp">
            <p>Ask a follow-up, or a new question…</p>
            <div>
              <span className="sc">
                <ShieldCheck className="ic" />
                Each question is answered on its own
              </span>
              <span className="en">
                <span className="kb">Enter</span> to send
              </span>
              <span className="snd">
                <ArrowUp className="ic" />
              </span>
            </div>
          </div>
        </div>
      </section>

      <aside>
        <section className="from">
          <p className="lx">Answers come from</p>
          <dl>
            <dt>Repository</dt>
            <dd>hiresight-platform</dd>
            <dt>Branch</dt>
            <dd>main</dd>
            <dt>Commit</dt>
            <dd>a3f91c2</dd>
            <dt>Indexed</dt>
            <dd>3,184 files · 118 APIs</dd>
            <dt>Analyzed</dt>
            <dd>14 minutes ago</dd>
          </dl>
        </section>
        <section className="rs">
          <h3 className="lx">
            Sources<span className="n">4</span>
          </h3>
          <p className="fq">For “How does authentication work?”</p>
          {ASSISTANT_SOURCES.map((s) => (
            <div key={`${s.file}-${s.symbol}`} className="src">
              <span className="ex">ts</span>
              <span className="fp">
                {s.dir}
                <b>{s.file}</b>
              </span>
              <span className="sy">
                <i>{s.symbol}</i>
                <u>{s.lines}</u>
              </span>
            </div>
          ))}
        </section>
        <section className="rs">
          <h3 className="lx">
            This session<span className="n">1</span>
          </h3>
          <div className="ses">
            <div className="on">
              <i />
              <span>How does authentication work?</span>
              <span className="c">4</span>
            </div>
          </div>
        </section>
      </aside>
    </>
  );
}

// ---------------------------------------------------------------------------
// Impact Analysis
// ---------------------------------------------------------------------------

const RUNS = [
  { sha: "a3f91c2", when: "2 hours ago", files: "3,184", debt: "41.2", active: true },
  { sha: "5cc21ab", when: "9 hours ago", files: "3,179", debt: "39.8" },
  { sha: "b7740de", when: "1 day ago", files: "3,171", debt: "39.8" },
  { sha: "1fa8c04", when: "2 days ago", files: "3,166", debt: "40.3" },
];

const DIRECT = [
  "src/auth/auth.controller.ts",
  "src/auth/jwt.strategy.ts",
  "src/billing/billing.service.ts",
  "src/users/users.service.ts",
  "src/auth/auth.service.spec.ts",
];

const INDIRECT = [
  ["src/auth/auth.guard.ts", "2 hops"],
  ["src/billing/billing.controller.ts", "2 hops"],
  ["src/users/users.controller.ts", "2 hops"],
  ["src/interview/interview.controller.ts", "3 hops"],
];

export function ImpactScreen() {
  const ringLength = 2 * Math.PI * 24;
  return (
    <>
      <div className="pn amb top">
        <p className="lx">Change Intelligence</p>
        <h1>Impact Analysis</h1>
        <p className="d">
          What a change actually breaks downstream, computed by walking the real dependency graph
          from a commit&apos;s changed files. Pick any analyzed commit on the left.
        </p>
      </div>
      <div className="cols">
        <section className="pn">
          <div className="ph">
            <div className="l">
              <Radar className="ic" />
              <div>
                <h2>Recent runs</h2>
                <p>Select one to compute impact</p>
              </div>
            </div>
          </div>
          <p className="lx rb">main</p>
          {RUNS.map((r) => (
            <div key={r.sha} className={cn("rr", r.active && "on")}>
              <div className="a">
                <b>{r.sha}</b>
                <span>{r.when}</span>
              </div>
              <div className="b">
                <GitBranch className="ic" style={{ fontSize: 12 }} />
                {r.files} files · debt {r.debt}
              </div>
            </div>
          ))}
        </section>

        <div className="res">
          <section className="pn">
            <div className="cm">
              <div className="cmt">
                <span className="l">Predecessor</span>
                <span className="r">
                  <GitBranch className="ic" />
                  <b>main</b>
                  <i>5cc21ab</i>
                </span>
                <span className="w">9 hours ago</span>
              </div>
              <ArrowRight className="ic ar" />
              <div className="cmt">
                <span className="l">Analyzing</span>
                <span className="r">
                  <GitBranch className="ic" />
                  <b>main</b>
                  <i>a3f91c2</i>
                </span>
              </div>
              <div className="rk">
                <div className="rg">
                  <svg width="54" height="54">
                    <circle
                      cx="27"
                      cy="27"
                      r="24"
                      fill="none"
                      stroke="var(--surface-3)"
                      strokeWidth="4"
                    />
                    <circle
                      cx="27"
                      cy="27"
                      r="24"
                      fill="none"
                      stroke="var(--danger)"
                      strokeWidth="4"
                      strokeLinecap="round"
                      strokeDasharray={ringLength}
                      strokeDashoffset={ringLength - (ringLength * 62) / 100}
                    />
                  </svg>
                  <span>62</span>
                </div>
                <div>
                  <span className="lx">Risk</span>
                  <p className="v">
                    <TriangleAlert className="ic" style={{ fontSize: 14 }} />
                    HIGH
                  </p>
                </div>
              </div>
            </div>
            <p className="cap2">
              Score 62 = (34.5 from dependents + 12.9 from endpoints + 15 for a changed file in a
              dependency cycle) × 1 — this change removed a file, symbol or endpoint. 1 test file
              also reached by this change, listed below but not scored.
            </p>
          </section>

          <div className="two">
            <section className="pn">
              <div className="ph">
                <div className="l">
                  <div>
                    <h2>Directly affected</h2>
                    <p>1 hop · imports a changed file</p>
                  </div>
                </div>
                <span className="ct">5 files</span>
              </div>
              <ul className="fl">
                {DIRECT.map((path) => (
                  <li key={path}>
                    <p className="p">{path}</p>
                    <p className="y">
                      imports <code>src/auth/auth.service.ts</code>
                    </p>
                  </li>
                ))}
              </ul>
            </section>
            <section className="pn">
              <div className="ph">
                <div className="l">
                  <div>
                    <h2>Indirectly affected</h2>
                    <p>2+ hops through the dependency graph</p>
                  </div>
                </div>
                <span className="ct">4 files</span>
              </div>
              <ul className="fl">
                {INDIRECT.map(([path, hops]) => (
                  <li key={path} className="row">
                    <p className="p">{path}</p>
                    <i>{hops}</i>
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <section className="pn">
            <div className="ph">
              <div className="l">
                <Plug className="ic" />
                <div>
                  <h2>Affected APIs</h2>
                  <p>endpoints reachable from the changed files</p>
                </div>
              </div>
            </div>
            <div className="apis">
              <span>POST /api/login</span>
              <span>POST /api/register</span>
              <span>POST /api/token/refresh</span>
              <span>GET /api/interviews</span>
              <span>POST /api/billing/checkout</span>
            </div>
          </section>

          <div className="ai">
            <b>AI insight</b>
            <p>
              Token rotation sits at the centre of sign-in, refresh and billing. Four files import
              it directly, and the interview routes pick it up through the auth guard, so check
              those endpoints first.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Flow Tracer
// ---------------------------------------------------------------------------

const LAYER_COLOR = {
  route: "oklch(0.77 0.17 52)",
  controller: "oklch(0.76 0.12 250)",
  service: "oklch(0.74 0.15 305)",
  repository: "oklch(0.76 0.15 155)",
  model: "oklch(0.8 0.11 205)",
} as const;

const LAYER_COUNTS: Array<[string, keyof typeof LAYER_COLOR, number]> = [
  ["Route", "route", 1],
  ["Controller", "controller", 1],
  ["Service", "service", 2],
  ["Repository", "repository", 1],
  ["Model", "model", 1],
];

/** Guide cells drawn left of each row, as in the real tree: "n" = nothing, "" = a line running
 * through, "h" = a branch into the row, "h e" = a branch that is the last child. */
const TRACE_ROWS: Array<{
  layer: string;
  color: keyof typeof LAYER_COLOR;
  file: string;
  dir: string;
  guides: string[];
  fn?: string;
  service?: string;
  active?: boolean;
}> = [
  {
    layer: "Route",
    color: "route",
    file: "auth.routes.ts",
    dir: "src/routes/",
    guides: [],
    fn: "login",
  },
  {
    layer: "Controller",
    color: "controller",
    file: "auth.controller.ts",
    dir: "src/controllers/",
    guides: ["h e"],
  },
  {
    layer: "Service",
    color: "service",
    file: "auth.service.ts",
    dir: "src/services/",
    guides: ["n", "h e"],
    active: true,
  },
  {
    layer: "Repository",
    color: "repository",
    file: "user.repository.ts",
    dir: "src/repositories/",
    guides: ["n", "n", "h"],
  },
  {
    layer: "Model",
    color: "model",
    file: "user.model.ts",
    dir: "src/models/",
    guides: ["n", "n", "", "h e"],
  },
  {
    layer: "Service",
    color: "service",
    file: "token.service.ts",
    dir: "src/services/",
    guides: ["n", "n", "h e"],
    service: "Redis",
  },
];

export function TracerScreen() {
  return (
    <>
      <div className="hd">
        <div>
          <h1>Flow Tracer</h1>
          <p>6 files on the path from POST /api/login, computed from the import graph.</p>
        </div>
        <span className="pk">
          <RouteIcon className="ic" />
          POST /api/login
          <ChevronsUpDown className="ic" />
        </span>
      </div>

      <section className="pn amb">
        <div className="sm">
          <div className="lane">
            {LAYER_COUNTS.map(([label, key, count]) => (
              <span
                key={key}
                className="ln"
                style={{ background: `color-mix(in oklch, ${LAYER_COLOR[key]} 14%, transparent)` }}
              >
                <i style={{ background: LAYER_COLOR[key] }} />
                <b>{label}</b>
                <span>{count}</span>
              </span>
            ))}
            <span className="tgs">
              <span className="tg s">Reaches data</span>
            </span>
          </div>
          <div className="rch">
            Reaches <span className="tg v">Redis</span>
          </div>
          <p className="note">
            Built from file imports, so it shows which files this request can reach, not which
            functions run. Layers are guessed from folder and file names.
          </p>
        </div>
      </section>

      <div className="cols">
        <section className="pn">
          <div className="ph">
            <div className="l">
              <RouteIcon className="ic" />
              <div>
                <h2>POST /api/login</h2>
                <p>Declared in src/routes/auth.routes.ts</p>
              </div>
            </div>
          </div>
          <ol>
            {TRACE_ROWS.map((row) => (
              <li key={row.file}>
                <div className="rw">
                  {row.guides.map((g, i) => (
                    <div key={i} className={cn("gd", g)} />
                  ))}
                  <div
                    className={cn("bt", row.active && "on")}
                    style={{ borderLeftColor: LAYER_COLOR[row.color] }}
                  >
                    <div className="t">
                      <em style={{ color: LAYER_COLOR[row.color] }}>{row.layer}</em>
                      <b>{row.file}</b>
                      <span>{row.dir}</span>
                    </div>
                    {row.fn || row.service ? (
                      <div className="m">
                        {row.fn ? <span className="tg v m">{row.fn}</span> : null}
                        {row.service ? <span className="tg">{row.service}</span> : null}
                      </div>
                    ) : null}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="pn">
          <div className="ph">
            <div className="l">
              <FileCode2 className="ic" />
              <div>
                <h2>auth.service.ts</h2>
                <p>src/services/</p>
              </div>
            </div>
            <a>
              Open in Codebase
              <ArrowUpRight className="ic" style={{ fontSize: 12 }} />
            </a>
          </div>
          <div className="dt">
            <div className="dh">
              <span
                className="lay"
                style={{
                  color: LAYER_COLOR.service,
                  background: `color-mix(in oklch, ${LAYER_COLOR.service} 14%, transparent)`,
                }}
              >
                <i style={{ background: LAYER_COLOR.service }} />
                Service
              </span>
              <span className="ln2">212 lines</span>
            </div>
            <div>
              <h3>Reached from</h3>
              <span className="lk">auth.controller.ts</span>
            </div>
            <div>
              <h3>Declares</h3>
              <div className="tags">
                <span className="tg m">validateUser</span>
                <span className="tg m">issueTokens</span>
                <span className="tg m">hashPassword</span>
              </div>
            </div>
            <div>
              <h3>Imports</h3>
              <div className="tags">
                <span className="tg v m">user.repository.ts</span>
                <span className="tg v m">token.service.ts</span>
                <span className="tg m">logger.ts</span>
              </div>
              <p className="s">
                Highlighted imports are stops on this trace. The rest are shared utilities or
                config.
              </p>
            </div>
            <div className="ai">
              <b>AI note</b>
              <p>
                Checks the submitted credentials against the user repository, then asks the token
                service to start a session.
              </p>
              <p className="f">
                Written from file names and imports only. It can&apos;t see what the code does, so
                check it against the file.
              </p>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Analysis progress (the screen a visitor lands on after connecting a repository)
// ---------------------------------------------------------------------------

const ANALYSIS_STAGES: Array<{ label: string; state: "done" | "run" | "todo" }> = [
  { label: "Queued", state: "done" },
  { label: "Cloning repository", state: "done" },
  { label: "Parsing files", state: "done" },
  { label: "Analyzing code", state: "done" },
  { label: "Building architecture graph", state: "run" },
  { label: "Generating embeddings", state: "todo" },
  { label: "Running technical debt analysis", state: "todo" },
];

export function AnalysisScreen() {
  const pct = 58;
  const length = 2 * Math.PI * 29;
  return (
    <div className="pn amb az">
      <div className="top">
        <div>
          <h1>Analyzing repository</h1>
          <p className="tg2">hiresight/hiresight-platform · main</p>
        </div>
        <div className="rg">
          <svg width="64" height="64">
            <circle cx="32" cy="32" r="29" fill="none" stroke="var(--surface-3)" strokeWidth="4" />
            <circle
              cx="32"
              cy="32"
              r="29"
              fill="none"
              stroke="var(--accent)"
              strokeWidth="4"
              strokeLinecap="round"
              strokeDasharray={length}
              strokeDashoffset={length - (length * pct) / 100}
            />
          </svg>
          <span>{pct}%</span>
        </div>
      </div>
      <div className="bar">
        <i style={{ width: `${pct}%` }} />
      </div>
      <ul>
        {ANALYSIS_STAGES.map((s) => (
          <li key={s.label} className={s.state === "todo" ? undefined : s.state}>
            {s.state === "done" ? (
              <Check className="ic" />
            ) : s.state === "run" ? (
              <Loader2 className="ic" />
            ) : (
              <Circle className="ic" />
            )}
            {s.label}
          </li>
        ))}
      </ul>
      <div className="meta">
        <span>Run 7e4a627c</span>
        <span>Elapsed 41s</span>
      </div>
    </div>
  );
}
