import { createFileRoute } from "@tanstack/react-router";
import { History, RefreshCcw } from "lucide-react";

import { Panel, PanelHeader, Sparkline, StatCell, Tag } from "@/components/ui-kit";
import { analysisHistory, debtTrend } from "@/lib/repo-data";

export const Route = createFileRoute("/app/history")({
  head: () => ({
    meta: [
      { title: "Analysis History — CodeLens AI" },
      { name: "description", content: "Every analysis run for this repository with architecture and debt scores over time." },
      { property: "og:title", content: "Analysis History — CodeLens AI" },
      { property: "og:description", content: "Track how architecture health and technical debt move commit by commit." },
    ],
  }),
  component: HistoryPage,
});

function HistoryPage() {
  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Analysis History</h1>
          <p className="text-[12px] text-muted-foreground">
            Runs are triggered on push to tracked branches, or manually.
          </p>
        </div>
        <button className="focus-ring ml-auto flex items-center gap-1.5 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-2.5 py-1.5 text-[12px] hover:bg-[oklch(0.745_0.19_44)]">
          <RefreshCcw className="size-3.5" /> Re-analyze main
        </button>
      </div>

      <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
        <div className="bg-surface"><StatCell label="Total analyses" value="2,291" sub="since Feb 2026" /></div>
        <div className="bg-surface"><StatCell label="Median duration" value="6m 12s" sub="3,184 files" /></div>
        <div className="bg-surface"><StatCell label="Architecture trend" value="+4 pts" tone="success" sub="last 30 days" /></div>
        <div className="bg-surface"><StatCell label="Debt trend" value="−14 pts" tone="success" sub="since March" /></div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1fr_300px]">
        <Panel>
          <PanelHeader title="Recent runs" icon={<History className="size-3.5" />} />
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border">
                {["Run", "Branch", "Commit", "Files", "Architecture", "Debt", "Completed"].map((h) => (
                  <th key={h} className="label-xs px-4 py-2 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {analysisHistory.map((a) => (
                <tr key={a.id} className="hover:bg-surface-2/60">
                  <td className="px-4 py-2.5 font-mono text-[11.5px] text-violet-soft">{a.id}</td>
                  <td className="px-4 py-2.5 font-mono text-[11.5px]">{a.branch}</td>
                  <td className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">{a.commit}</td>
                  <td className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">{a.files.toLocaleString()}</td>
                  <td className="px-4 py-2.5"><Tag tone={a.arch >= 80 ? "success" : "warning"} mono>{a.arch}</Tag></td>
                  <td className="px-4 py-2.5"><Tag tone={a.debt >= 72 ? "warning" : "success"} mono>{a.debt}</Tag></td>
                  <td className="px-4 py-2.5 text-[11.5px] text-subtle">{a.date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel>
          <PanelHeader title="Score trend" subtitle="architecture vs debt" />
          <div className="space-y-5 p-4">
            <div>
              <p className="label-xs mb-1.5">Architecture health</p>
              <Sparkline points={debtTrend.map((d) => d.score)} tone="success" width={240} height={44} />
            </div>
            <div>
              <p className="label-xs mb-1.5">Technical debt</p>
              <Sparkline points={debtTrend.map((d) => d.debt)} tone="warning" width={240} height={44} />
            </div>
            <p className="text-[11.5px] leading-relaxed text-muted-foreground">
              Debt fell fastest in June and July, when the duplicated pagination cluster shrank from 19 to 12 sites.
            </p>
          </div>
        </Panel>
      </div>
    </div>
  );
}
