import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { GitBranch, RefreshCcw, PackageX, AlertOctagon } from "lucide-react";

import { ArchGraph } from "@/components/arch-graph";
import { AiNote, Panel, PanelHeader, ScoreBar, StatCell, Tag, severityTone } from "@/components/ui-kit";
import { dependencyRisks } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/dependencies")({
  head: () => ({
    meta: [
      { title: "Dependency Analysis — Substrate" },
      { name: "description", content: "Dependency graph, circular dependencies, coupling hotspots, orphaned modules and outdated external packages." },
      { property: "og:title", content: "Dependency Analysis — Substrate" },
      { property: "og:description", content: "Understand internal coupling and external package risk in one view." },
    ],
  }),
  component: Dependencies,
});

const filters = ["Frontend", "Backend", "Services", "Libraries", "External APIs"];

function Dependencies() {
  const [active, setActive] = useState<string[]>([]);

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Dependency Analysis</h1>
          <p className="text-[12px] text-muted-foreground">
            1,912 internal edges · 264 external packages · resolved on commit a3f91c2
          </p>
        </div>
        <div className="ml-auto flex flex-wrap gap-1.5">
          {filters.map((f) => {
            const on = active.includes(f);
            return (
              <button
                key={f}
                onClick={() => setActive((a) => (on ? a.filter((x) => x !== f) : [...a, f]))}
                className={cn(
                  "focus-ring rounded-md border px-2.5 py-1.5 text-[12px]",
                  on ? "border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] " : "border-border text-muted-foreground hover:border-border-strong",
                )}
              >
                {f}
              </button>
            );
          })}
        </div>
      </div>

      {/* RISK SUMMARY */}
      <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-5">
        <div className="bg-surface"><StatCell label="Risk level" value="Elevated" tone="warning" sub="2 CVEs · 2 cycles" /></div>
        <div className="bg-surface"><StatCell label="Circular dependencies" value="2" tone="danger" sub="auth ↔ billing" /></div>
        <div className="bg-surface"><StatCell label="Coupling hotspots" value="4" tone="warning" sub="fan-in > 25" /></div>
        <div className="bg-surface"><StatCell label="Orphan components" value="3" sub="no inbound references" /></div>
        <div className="bg-surface"><StatCell label="Outdated packages" value="9" tone="warning" sub="of 264 external" /></div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
        <Panel className="overflow-hidden">
          <PanelHeader title="Dependency graph" subtitle="risk edges are dashed" icon={<GitBranch className="size-3.5" />} />
          <div className="p-3">
            <ArchGraph className="h-[330px]" compact />
          </div>
        </Panel>

        <div className="space-y-3">
          <Panel>
            <PanelHeader title="Circular dependencies" icon={<RefreshCcw className="size-3.5" />} />
            <ul className="divide-y divide-border">
              {dependencyRisks.circular.map((c) => (
                <li key={c.a} className="flex items-center gap-2 px-4 py-3">
                  <span className="font-mono text-[11.5px]">{c.a}</span>
                  <span className="text-subtle">↔</span>
                  <span className="font-mono text-[11.5px]">{c.b}</span>
                  <Tag tone={severityTone(c.severity)} className="ml-auto">{c.severity}</Tag>
                  <Tag mono>{c.hops} hops</Tag>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel>
            <PanelHeader title="Highly coupled modules" subtitle="normalized coupling score" />
            <ul className="space-y-3 p-4">
              {dependencyRisks.coupled.map((m) => (
                <li key={m.module}>
                  <div className="mb-1.5 flex items-center gap-2 text-[12px]">
                    <span className="font-mono">{m.module}</span>
                    <span className="font-mono text-[11px] text-subtle">
                      fan-in {m.fanIn} · fan-out {m.fanOut}
                    </span>
                    <span className="ml-auto font-mono text-[11.5px] text-warning">{m.score.toFixed(2)}</span>
                  </div>
                  <ScoreBar value={m.score * 100} tone={m.score > 0.8 ? "danger" : "warning"} />
                </li>
              ))}
            </ul>
          </Panel>

          <Panel>
            <PanelHeader title="Orphan components" subtitle="reachable from no entry point" icon={<PackageX className="size-3.5" />} />
            <ul className="divide-y divide-border">
              {dependencyRisks.orphans.map((o) => (
                <li key={o} className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">{o}</li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>

      <Panel>
        <PanelHeader title="External dependencies" subtitle="264 packages · 9 outdated · 2 with known CVEs" icon={<AlertOctagon className="size-3.5" />} />
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-border">
              {["Package", "Installed", "Latest", "Used by", "Advisory", "Status"].map((h) => (
                <th key={h} className="label-xs px-4 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {dependencyRisks.external.map((d) => (
              <tr key={d.name} className="hover:bg-surface-2/60">
                <td className="px-4 py-2.5 font-mono text-[12px]">{d.name}</td>
                <td className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">{d.version}</td>
                <td className="px-4 py-2.5 font-mono text-[11.5px] text-subtle">{d.latest}</td>
                <td className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">{d.usedBy} modules</td>
                <td className="px-4 py-2.5 font-mono text-[11.5px] text-danger">{"cve" in d ? d.cve : "—"}</td>
                <td className="px-4 py-2.5">
                  <Tag tone={severityTone(d.severity)}>
                    {d.severity === "ok" ? "current" : d.severity === "warning" ? "outdated" : "vulnerable"}
                  </Tag>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <AiNote>
        The <span className="mono text-violet-soft">auth ↔ billing</span> cycle is the highest-leverage fix in this
        repository: it blocks independent deployment of both services and forces 34 auth tests to run on every billing
        change. Extracting scope resolution into <span className="mono text-violet-soft">packages/identity</span> removes
        the cycle and drops auth&apos;s fan-in from 41 to 29.
      </AiNote>
    </div>
  );
}
