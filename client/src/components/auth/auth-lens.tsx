import type { CSSProperties } from "react";

import { cn } from "@/lib/utils";

// Decorative art for the sign-in / sign-up screens: the brand ring enlarged
// into a lens over a dimmed field of files, with the real architecture layers
// (web app, API, services, data, external services) visible inside it and one
// request path (web app -> API -> billing -> Stripe) traced in orange. The
// names are illustrative; nothing here is read from an account.
//
// Drawn in an 840x900 box with the lens centred on (420, 400), radius 250.
// `.lens-sweep` in styles.css hard-codes that centre as its transform origin.

type LensNode = {
  label: string;
  x: number;
  y: number;
  w: number;
  /** On the traced request path. */
  hot?: boolean;
  /** An outside service: dashed outline. */
  external?: boolean;
  /** Fade-in delay (see `.seq-fade`). */
  d: string;
};

const NODES: LensNode[] = [
  { label: "Web app", x: 420, y: 240, w: 112, hot: true, d: "0s" },
  { label: "REST API", x: 330, y: 315, w: 104, hot: true, d: "0.1s" },
  { label: "Webhooks", x: 510, y: 315, w: 104, d: "0.1s" },
  { label: "Auth", x: 285, y: 400, w: 84, d: "0.2s" },
  { label: "Billing", x: 420, y: 400, w: 96, hot: true, d: "0.2s" },
  { label: "Search", x: 555, y: 400, w: 88, d: "0.2s" },
  { label: "PostgreSQL", x: 310, y: 490, w: 112, d: "0.3s" },
  { label: "Redis", x: 530, y: 490, w: 80, d: "0.3s" },
  { label: "Stripe", x: 420, y: 560, w: 88, hot: true, external: true, d: "0.4s" },
  { label: "GitHub", x: 525, y: 545, w: 96, external: true, d: "0.4s" },
];

// Edges between the nodes above; `hot` ones are the traced path, drawn in order.
const EDGES: { d: string; hot?: boolean; delay?: string; dashed?: boolean }[] = [
  { d: "M420 255C420 280 330 285 330 300", hot: true, delay: "0.9s" },
  { d: "M340 330C340 360 410 365 410 385", hot: true, delay: "1.15s" },
  { d: "M420 415L420 545", hot: true, delay: "1.4s" },
  { d: "M320 330C320 360 285 365 285 385" },
  { d: "M500 330C500 360 430 365 430 385" },
  { d: "M520 330C520 360 555 365 555 385" },
  { d: "M285 415C285 450 300 455 300 475" },
  { d: "M405 415C405 450 335 455 335 475" },
  { d: "M555 415C555 450 530 455 530 475" },
  { d: "M573 545C645 540 655 335 562 318", dashed: true },
];

// The dimmed file field outside the lens: [x, y, radius].
const FILES: [number, number, number][] = [
  [70, 90, 3.5],
  [180, 50, 3],
  [300, 110, 4],
  [420, 60, 3],
  [540, 100, 3.5],
  [660, 50, 3],
  [770, 120, 4],
  [790, 250, 3],
  [760, 380, 3.5],
  [800, 520, 3],
  [740, 640, 4],
  [790, 770, 3],
  [660, 700, 3],
  [560, 760, 3.5],
  [430, 730, 3],
  [300, 770, 4],
  [170, 700, 3],
  [100, 590, 3.5],
  [60, 450, 3],
  [110, 320, 4],
  [190, 230, 3],
  [240, 150, 3],
  [620, 170, 3.5],
  [690, 520, 3],
  [150, 520, 3],
];
const FILE_LINKS = [
  "M70 90L180 50L300 110L420 60L540 100L660 50L770 120L790 250L760 380L800 520L740 640L790 770",
  "M740 640L660 700L560 760L430 730L300 770L170 700L100 590L60 450L110 320L190 230L240 150L300 110",
  "M620 170L540 100M620 170L790 250M690 520L760 380M690 520L740 640M150 520L100 590M150 520L60 450M190 230L70 90M110 320L190 230",
];

const delayVar = (d: string) => ({ "--d": d }) as CSSProperties;

export function AuthLens({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 840 900"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
      className={className}
    >
      <g fill="none" strokeWidth={1} className="stroke-muted-foreground">
        {FILE_LINKS.map((d) => (
          <path key={d} d={d} />
        ))}
      </g>
      <g className="fill-muted-foreground">
        {FILES.map(([x, y, r]) => (
          <circle key={`${x}-${y}`} cx={x} cy={y} r={r} />
        ))}
      </g>

      <g fill="none" strokeWidth={1.5}>
        {EDGES.map((e) =>
          e.hot ? (
            <path
              key={e.d}
              d={e.d}
              pathLength={1}
              strokeWidth={2}
              className="trace-draw stroke-primary"
              style={delayVar(e.delay ?? "0s")}
            />
          ) : (
            <path
              key={e.d}
              d={e.d}
              className="stroke-subtle"
              strokeDasharray={e.dashed ? "4 5" : undefined}
            />
          ),
        )}
      </g>

      <g fontSize={12} textAnchor="middle" className="font-mono">
        {NODES.map((n) => (
          <g key={n.label} className="seq-fade" style={delayVar(n.d)}>
            <rect
              x={n.x - n.w / 2}
              y={n.y - 15}
              width={n.w}
              height={30}
              rx={8}
              strokeDasharray={n.external ? "4 3" : undefined}
              className={cn(
                "fill-surface-2",
                n.hot ? "stroke-primary" : n.external ? "stroke-subtle" : "stroke-border-strong",
              )}
            />
            <text x={n.x} y={n.y + 4} className="fill-foreground">
              {n.label}
            </text>
          </g>
        ))}
      </g>

      {/* The veil dims everything but the circle, which is what makes it a lens. */}
      <path
        fillRule="evenodd"
        fillOpacity={0.84}
        className="fill-surface"
        d="M0 0H840V900H0Z M170 400a250 250 0 1 0 500 0a250 250 0 1 0 -500 0Z"
      />

      <g className="lens-sweep">
        <circle
          cx={420}
          cy={400}
          r={255}
          fill="none"
          strokeWidth={10}
          strokeLinecap="round"
          strokeDasharray="1266 336"
          transform="rotate(82 420 400)"
          className="stroke-primary"
        />
        <circle cx={598} cy={578} r={14} className="fill-foreground" />
        <circle cx={598} cy={578} r={6.5} className="fill-surface" />
      </g>
    </svg>
  );
}

/** A small lens for phone widths, where the full art has no room. */
export function AuthLensMini({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 112 112"
      width={112}
      height={112}
      aria-hidden="true"
      className={cn("shrink-0", className)}
    >
      <path
        d="M56 36V54M56 54L40 74M56 54L76 74"
        fill="none"
        strokeWidth={1.5}
        className="stroke-subtle"
      />
      <path d="M56 36V54M56 54L40 74" fill="none" strokeWidth={2} className="stroke-primary" />
      <rect x={42} y={24} width={28} height={14} rx={5} className="fill-surface-2 stroke-primary" />
      <rect x={26} y={70} width={28} height={14} rx={5} className="fill-surface-2 stroke-primary" />
      <rect
        x={64}
        y={70}
        width={28}
        height={14}
        rx={5}
        className="fill-surface-2 stroke-border-strong"
      />
      <circle
        cx={56}
        cy={56}
        r={50}
        fill="none"
        strokeWidth={6}
        strokeLinecap="round"
        strokeDasharray="249 65"
        transform="rotate(82 56 56)"
        className="stroke-primary"
      />
      <circle cx={91.5} cy={91.5} r={7} className="fill-foreground" />
      <circle cx={91.5} cy={91.5} r={3} className="fill-background" />
    </svg>
  );
}
