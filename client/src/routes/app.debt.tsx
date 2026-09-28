import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { AlertTriangle, ChevronDown } from "lucide-react";

import { AiNote, Panel, PanelHeader, Ring, ScoreBar, StatCell, Tag, severityTone } from "@/components/ui-kit";
import { debtBreakdown, debtIssues, debtTrend } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/debt")({
  head: () => ({
    meta: [
      { title: "Technical Debt — CodeLens AI" },
      { name: "description", content: "A scored technical debt inventory with why it matters, the files involved and a concrete recommended fix." },
      { property: "og:title", content: "Technical Debt — CodeLens AI" },
      { property: "og:description", content: "Prioritised debt with severity, affected files and AI-suggested remediation." },
    ],
  }),
  component: Debt,
});

const severities = ["critical", "high", "medium"] as const;

function Debt() {
  const [filter, setFilter] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(debtIssues[0]!.id);
  const issues = filter ? debtIssues.filter((i) => i.severity === filter) : debtIssues;

  const max = Math.max(...debtTrend.map((t) => t.debt));

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Technical Debt</h1>
          <p className="text-[12px] text-muted-foreground">
            28 open items · scored by blast radius, change frequency and test coverage.
          </p>
        </div>
        <div className="ml-auto flex gap-1.5">
          <button
            onClick={() => setFilter(null)}
            className={cn(
              "focus-ring rounded-md border px-2.5 py-1.5 text-[12px]",
              !filter ? "border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)]" : "border-border text-muted-foreground hover:border-border-strong",
            )}
          >
            All
          </button>
          {severities.map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={cn(
                "focus-ring rounded-md border px-2.5 py-1.5 text-[12px] capitalize",
                filter === s ? "border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)]" : "border-border text-muted-foreground hover:border-border-strong",
              )}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-[260px_1fr] 2xl:grid-cols-[260px_1fr_290px]">
        <Panel ambient className="h-fit">
          <PanelHeader title="Debt score" subtitle="lower is better" icon={<AlertTriangle className="size-3.5" />} />
          <div className="flex flex-col items-center gap-3 p-5">
            <Ring value={28} size={92} tone="warning" label="28" />
            <p className="text-center text-[11.5px] text-muted-foreground">
              Down 14 points since March. Two critical items account for 41% of the remaining score.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-px border-t border-border bg-border">
            <div className="bg-surface"><StatCell label="Critical" value="2" tone="danger" /></div>
            <div className="bg-surface"><StatCell label="High" value="2" tone="warning" /></div>
            <div className="bg-surface"><StatCell label="Medium" value="2" /></div>
            <div className="bg-surface"><StatCell label="Resolved 30d" value="9" tone="success" /></div>
          </div>
        </Panel>

        <div className="space-y-3">
          {issues.map((i) => {
            const isOpen = open === i.id;
            return (
              <Panel key={i.id}>
                <button
                  onClick={() => setOpen(isOpen ? null : i.id)}
                  className="focus-ring flex w-full items-center gap-3 px-4 py-3 text-left"
                >
                  <Tag tone={severityTone(i.severity)}>{i.severity}</Tag>
                  <span className="font-mono text-[11px] text-subtle">{i.id}</span>
                  <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{i.title}</span>
                  <Tag>{i.category}</Tag>
                  <ChevronDown className={cn("size-3.5 text-subtle transition-transform", isOpen && "rotate-180")} />
                </button>
                {isOpen ? (
                  <div className="space-y-3 border-t border-border p-4">
                    <div>
                      <p className="label-xs mb-1.5">Affected files</p>
                      <div className="flex flex-wrap gap-1.5">
                        {i.files.map((f) => <Tag key={f} mono>{f}</Tag>)}
                      </div>
                    </div>
                    <div>
                      <p className="label-xs mb-1">Why this matters</p>
                      <p className="text-[12.5px] leading-relaxed text-secondary-foreground">{i.why}</p>
                    </div>
                    <AiNote title="Recommended fix">{i.fix}</AiNote>
                  </div>
                ) : null}
              </Panel>
            );
          })}
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:col-span-2 2xl:col-span-1 2xl:grid-cols-1">
          <Panel>
            <PanelHeader title="Debt by category" />
            <ul className="space-y-3 p-4">
              {debtBreakdown.map((b) => (
                <li key={b.label}>
                  <div className="mb-1.5 flex items-baseline gap-2 text-[12px]">
                    <span>{b.label}</span>
                    <span className="ml-auto font-mono text-[11.5px] text-muted-foreground">{b.score}</span>
                  </div>
                  <ScoreBar value={b.score} tone={b.score > 70 ? "danger" : b.score > 50 ? "warning" : "success"} />
                  <p className="mt-1 text-[11px] text-subtle">{b.weight}</p>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel>
            <PanelHeader title="Debt trend" subtitle="6 months · lower is better" />
            <div className="flex h-[132px] items-end gap-2 p-4">
              {debtTrend.map((t) => (
                <div key={t.month} className="flex flex-1 flex-col items-center gap-1.5">
                  <span className="font-mono text-[10px] text-subtle">{t.debt}</span>
                  <div
                    className="w-full rounded-t-sm bg-gradient-to-t from-violet/25 to-violet/70"
                    style={{ height: `${(t.debt / max) * 76}px` }}
                  />
                  <span className="text-[10.5px] text-subtle">{t.month}</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
