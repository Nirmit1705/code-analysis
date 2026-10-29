import { useEffect, useState } from "react";

import {
  HIGH_FROM,
  MEDIUM_FROM,
  formatPoints,
  plural,
  riskStyle,
  verdictNote,
  verdictSentence,
} from "@/lib/impact-view";
import type { ImpactRead } from "@/lib/queries/impact";
import { cn } from "@/lib/utils";

/** The score on a 0-100 scale with its Low/Medium/High zones. The pin slides
 * to the score once the screen has mounted (and again when another commit is
 * picked); with reduced motion it just sits there. */
function ScoreScale({ score }: { score: number }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setShown(score)));
    return () => cancelAnimationFrame(id);
  }, [score]);

  return (
    <div className="mt-auto pt-7">
      <div className="relative flex h-2.5 gap-[3px]">
        <i className="rounded-[3px] bg-success/30" style={{ width: `${MEDIUM_FROM}%` }} />
        <i
          className="rounded-[3px] bg-warning/30"
          style={{ width: `${HIGH_FROM - MEDIUM_FROM}%` }}
        />
        <i className="flex-1 rounded-[3px] bg-danger/30" />
        <i
          className="absolute -top-[7px] h-6 w-1 -translate-x-1/2 rounded-sm bg-foreground shadow-[0_0_0_3px_var(--surface)] transition-[left] duration-[900ms] ease-out motion-reduce:transition-none"
          style={{ left: `${Math.min(100, shown)}%` }}
        />
      </div>
      <div className="relative mt-2.5 h-5 font-mono text-[10px] text-subtle">
        <span className="absolute left-0">Low</span>
        <span className="absolute" style={{ left: `${MEDIUM_FROM}%` }}>
          Medium from {MEDIUM_FROM}
        </span>
        <span className="absolute" style={{ left: `${HIGH_FROM}%` }}>
          High from {HIGH_FROM}
        </span>
      </div>
    </div>
  );
}

function ReceiptRow({ label, value }: { label: string; value: number }) {
  const zero = value === 0;
  return (
    <div className="grid grid-cols-[132px_minmax(0,1fr)_62px] items-center gap-3 py-2 text-[11.5px]">
      <span className={zero ? "text-subtle" : "text-muted-foreground"}>{label}</span>
      <span className="h-1.5 overflow-hidden rounded-full bg-surface-3">
        <span
          className="block h-full rounded-full bg-muted-foreground"
          style={{ width: `${Math.min(100, value)}%` }}
        />
      </span>
      <span className={cn("text-right font-mono tabular-nums", zero && "text-subtle")}>
        {zero ? "0" : formatPoints(value)}
      </span>
    </div>
  );
}

/** The server's own arithmetic, `(dependents + endpoints + cycle) x multiplier`,
 * one term per row on the same 0-100 scale as the verdict above it. */
function Receipt({ data }: { data: ImpactRead }) {
  const b = data.breakdown;
  const risk = riskStyle(data.risk);
  const subtotal = b.file_points + b.api_points + b.cycle_bonus;
  return (
    <div className="min-w-0 border-t border-border bg-surface-2 p-5 sm:p-7 lg:border-t-0 lg:border-l">
      <h3 className="text-[12px] font-bold text-foreground">How {data.score} was reached</h3>
      <p className="mt-1 mb-3 text-[10.5px] text-subtle">Every term on the same 0 to 100 scale.</p>

      <ReceiptRow label="Dependents" value={b.file_points} />
      <ReceiptRow label="Endpoints" value={b.api_points} />
      <ReceiptRow label="Dependency cycle" value={b.cycle_bonus} />

      <div className="mt-1.5 grid grid-cols-[132px_minmax(0,1fr)_62px] items-center gap-3 border-t border-dashed border-border-strong py-2 pt-3.5 text-[11.5px]">
        <span className="text-muted-foreground">Subtotal</span>
        <span />
        <span className="text-right font-mono tabular-nums">{formatPoints(subtotal)}</span>
      </div>
      <div className="grid grid-cols-[132px_minmax(0,1fr)_62px] items-center gap-3 py-2 text-[11.5px]">
        <span className="font-semibold text-foreground">
          {b.change_kind === "breaking" ? "Breaking" : "Non-breaking"}
        </span>
        <span />
        <span className="text-right font-mono whitespace-nowrap tabular-nums">
          {"×"} {b.multiplier}
        </span>
      </div>
      <div className="mt-1.5 grid grid-cols-[132px_minmax(0,1fr)_62px] items-center gap-3 border-t border-border-strong pt-3.5 text-[11.5px]">
        <span className="font-bold text-foreground">Score</span>
        <span className="h-1.5 overflow-hidden rounded-full bg-surface-3">
          <span
            className={cn("block h-full rounded-full", risk.bar)}
            style={{ width: `${data.score}%` }}
          />
        </span>
        <span className={cn("text-right font-mono text-[15px] font-bold tabular-nums", risk.text)}>
          {data.score}
        </span>
      </div>

      {b.excluded_test_files > 0 ? (
        <p className="mt-3.5 text-[10.5px] text-subtle">
          {plural(b.excluded_test_files, "test file")} also reached, listed below but not scored.
        </p>
      ) : null}
    </div>
  );
}

export function ImpactVerdict({ data }: { data: ImpactRead }) {
  const risk = riskStyle(data.risk);
  return (
    <section
      aria-label="Risk verdict"
      className="panel grid overflow-hidden lg:grid-cols-[minmax(0,1.25fr)_minmax(300px,1fr)]"
    >
      <div className="flex min-w-0 flex-col p-5 sm:p-7">
        <div className={cn("flex flex-wrap items-baseline gap-x-1.5 gap-y-2", risk.text)}>
          <span className="text-[72px] leading-[0.9] font-extrabold tracking-[-0.045em] tabular-nums sm:text-[92px]">
            {data.score}
          </span>
          <span className="mr-3 font-mono text-[12px] text-subtle">/ 100</span>
          <span className="inline-flex items-center gap-2 text-[14px] font-bold">
            <span className="size-2.5 rounded-full bg-current" />
            {risk.word}
          </span>
        </div>
        <p className="mt-5 max-w-[34ch] text-[17px] leading-snug font-semibold tracking-tight text-balance text-foreground">
          {verdictSentence(data)}
        </p>
        <p className="mt-1 max-w-[52ch] text-[12px] text-muted-foreground">{verdictNote(data)}</p>
        <ScoreScale score={data.score} />
      </div>
      <Receipt data={data} />
    </section>
  );
}
