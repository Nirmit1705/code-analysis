import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export function Panel({
  className,
  children,
  ambient,
}: {
  className?: string;
  children: ReactNode;
  ambient?: boolean;
}) {
  return (
    <section className={cn("panel", ambient && "ambient-panel", className)}>{children}</section>
  );
}

export function PanelHeader({
  title,
  subtitle,
  actions,
  icon,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
      <div className="flex min-w-0 items-center gap-2.5">
        {icon ? <span className="text-violet-soft">{icon}</span> : null}
        <div className="min-w-0">
          <h2 className="truncate text-[13px] font-semibold text-foreground">{title}</h2>
          {subtitle ? (
            <p className="truncate text-[11px] text-muted-foreground">{subtitle}</p>
          ) : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
    </header>
  );
}

const toneMap = {
  success: { text: "text-success", bg: "bg-success/12", ring: "ring-success/25", raw: "var(--success)" },
  warning: { text: "text-warning", bg: "bg-warning/12", ring: "ring-warning/25", raw: "var(--warning)" },
  danger: { text: "text-danger", bg: "bg-danger/12", ring: "ring-danger/25", raw: "var(--danger)" },
  info: { text: "text-info", bg: "bg-info/12", ring: "ring-info/25", raw: "var(--info)" },
  violet: { text: "text-violet-soft", bg: "bg-violet/14", ring: "ring-violet/30", raw: "var(--violet)" },
  neutral: { text: "text-muted-foreground", bg: "bg-surface-2", ring: "ring-border", raw: "var(--subtle)" },
} as const;

export type Tone = keyof typeof toneMap;

/** The raw CSS color behind a tone, for contexts (raw SVG `stroke`/`fill`
 * attributes) that can't take a Tailwind class — the same value `ScoreBar`/
 * `Ring`/`Sparkline` already use internally via `toneMap[tone].raw`. */
export function toneColor(tone: Tone): string {
  return toneMap[tone].raw;
}

export function Tag({
  children,
  tone = "neutral",
  mono,
  caps,
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  mono?: boolean;
  /** For severity / risk / status badges (HIGH, DANGER, COMPLETED…): upper
   * case, a touch smaller and letter-spaced so the capitals don't look
   * oversized next to sentence-case text. Leave off for descriptive tags
   * (file names, dependency names, branch names), which must keep their case. */
  caps?: boolean;
  className?: string;
}) {
  const t = toneMap[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        t.text,
        t.bg,
        t.ring,
        mono && "font-mono",
        caps && "text-[10px] font-semibold tracking-[0.06em] uppercase",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Dot({ tone = "neutral", pulse }: { tone?: Tone; pulse?: boolean }) {
  return (
    <span
      className={cn("inline-block size-1.5 rounded-full", pulse && "pulse-node")}
      style={{ background: toneMap[tone].raw }}
    />
  );
}

export function ScoreBar({ value, tone = "violet" }: { value: number; tone?: Tone }) {
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-surface-3">
      <div
        className="h-full rounded-full transition-[width] duration-700"
        style={{ width: `${value}%`, background: toneMap[tone].raw }}
      />
    </div>
  );
}

export function Ring({
  value,
  size = 54,
  tone = "violet",
  label,
}: {
  value: number;
  size?: number;
  tone?: Tone;
  label?: string;
}) {
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={4} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={toneMap[tone].raw}
          strokeWidth={4}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c - (c * value) / 100}
          className="transition-[stroke-dashoffset] duration-1000"
        />
      </svg>
      <span className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-mono text-[13px] font-medium text-foreground">{label ?? value}</span>
      </span>
    </div>
  );
}

export function Sparkline({
  points,
  tone = "violet",
  width = 96,
  height = 26,
}: {
  points: number[];
  tone?: Tone;
  width?: number;
  height?: number;
}) {
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const d = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * width;
      const y = height - ((p - min) / span) * (height - 4) - 2;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={width} height={height} className="overflow-visible">
      <path d={d} fill="none" stroke={toneMap[tone].raw} strokeWidth={1.5} strokeLinecap="round" />
    </svg>
  );
}

export function KeyHint({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
      {children}
    </kbd>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[19px] font-semibold tracking-tight text-foreground">{title}</h1>
        {description ? (
          <p className="mt-1 max-w-2xl text-[13px] text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function StatCell({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  tone?: Tone;
}) {
  return (
    <div className="px-4 py-3">
      <p className="label-xs">{label}</p>
      <p className={cn("mt-1 font-mono text-[17px] font-medium", toneMap[tone].text === "text-muted-foreground" ? "text-foreground" : toneMap[tone].text)}>
        {value}
      </p>
      {sub ? <p className="mt-0.5 text-[11px] text-subtle">{sub}</p> : null}
    </div>
  );
}

export function AiNote({ children, title = "AI insight" }: { children: ReactNode; title?: string }) {
  return (
    <div className="rounded-md border border-violet/22 bg-violet/[0.07] p-3">
      <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-violet-soft uppercase">
        <span className="inline-block size-1.5 rounded-full bg-violet pulse-node" />
        {title}
      </p>
      <p className="text-[12.5px] leading-relaxed text-secondary-foreground">{children}</p>
    </div>
  );
}

export function severityTone(s: string): Tone {
  if (s === "critical" || s === "high" || s === "danger") return "danger";
  if (s === "medium" || s === "warning" || s === "moderate") return "warning";
  if (s === "low" || s === "ok") return "success";
  return "neutral";
}
