import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Search, ArrowDown, FileCode2 } from "lucide-react";

import { AiNote, Panel, PanelHeader, Tag } from "@/components/ui-kit";
import { traceFlows } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/tracer")({
  head: () => ({
    meta: [
      { title: "Feature Tracer — CodeLens AI" },
      { name: "description", content: "Trace any feature end to end, from UI event through gateway, services and repositories to the database." },
      { property: "og:title", content: "Feature Tracer — CodeLens AI" },
      { property: "og:description", content: "Follow a feature across every service it touches, with per-node files, functions and AI explanations." },
    ],
  }),
  component: Tracer,
});

const featureNames = Object.keys(traceFlows);

function Tracer() {
  const [feature, setFeature] = useState(featureNames[0]!);
  const [query, setQuery] = useState(featureNames[0]!);
  const [step, setStep] = useState(3);
  const flow = traceFlows[feature]!;
  const node = flow[step] ?? flow[0]!;

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Feature Tracer</h1>
          <p className="text-[12px] text-muted-foreground">
            Resolve a feature into the exact execution path across the system.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <div className="flex items-center gap-2 rounded-md border border-input bg-background px-2 py-1.5">
            <Search className="size-3.5 text-subtle" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search a feature"
              className="w-56 bg-transparent text-[12.5px] outline-none placeholder:text-subtle"
            />
          </div>
          {featureNames.map((f) => (
            <button
              key={f}
              onClick={() => {
                setFeature(f);
                setQuery(f);
                setStep(0);
              }}
              className={cn(
                "focus-ring rounded-md border px-2.5 py-1.5 text-[12px]",
                feature === f
                  ? "border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] "
                  : "border-border text-muted-foreground hover:border-border-strong",
              )}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1fr_360px]">
        <Panel className="overflow-hidden">
          <PanelHeader
            title={feature}
            subtitle={`${flow.length} hops · 4 services · 1 database write`}
            actions={<Tag tone="violet" caps>trace resolved</Tag>}
          />
          <div className="p-5">
            <ol className="mx-auto max-w-md space-y-0">
              {flow.map((n, i) => {
                const active = i === step;
                return (
                  <li key={n.label}>
                    <button
                      onClick={() => setStep(i)}
                      className={cn(
                        "focus-ring w-full rounded-lg border px-4 py-3 text-left transition-all",
                        active
                          ? "border-violet/50 bg-violet/12"
                          : "border-border bg-surface hover:border-border-strong hover:bg-surface-2",
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[10.5px] text-subtle">{String(i + 1).padStart(2, "0")}</span>
                        <span className="text-[13px] font-medium">{n.label}</span>
                        <Tag tone={active ? "violet" : "neutral"} className="ml-auto">{n.sub}</Tag>
                      </div>
                      <p className="mt-1 truncate pl-6 font-mono text-[11px] text-subtle">{n.file}</p>
                    </button>
                    {i < flow.length - 1 ? (
                      <div className="flex h-6 items-center justify-center">
                        <ArrowDown className={cn("size-3.5", i < step ? "text-violet-soft" : "text-border-strong")} />
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          </div>
        </Panel>

        <Panel className="h-fit">
          <PanelHeader title={node.label} subtitle={node.sub} icon={<FileCode2 className="size-3.5" />} />
          <div className="space-y-4 p-4">
            <div>
              <p className="label-xs mb-1">Component description</p>
              <p className="text-[12.5px] leading-relaxed text-secondary-foreground">{node.note}</p>
            </div>
            <div className="grid gap-3">
              <div>
                <p className="label-xs mb-1">File</p>
                <p className="font-mono text-[11.5px] text-muted-foreground">{node.file}</p>
              </div>
              <div>
                <p className="label-xs mb-1">Function</p>
                <p className="font-mono text-[11.5px] text-cyan">{node.fn}</p>
              </div>
              <div>
                <p className="label-xs mb-1.5">Dependencies</p>
                <div className="flex flex-wrap gap-1">
                  {node.deps.length ? node.deps.map((d) => <Tag key={d} mono>{d}</Tag>) : <span className="text-[11.5px] text-subtle">none</span>}
                </div>
              </div>
            </div>
            <AiNote title="AI explanation">
              This hop is {step === flow.length - 1 ? "the terminal write of the trace" : "an intermediate boundary"}. CodeLens AI
              resolved it by following the import graph and route registrations — no annotations or manual mapping required.
            </AiNote>
          </div>
        </Panel>
      </div>
    </div>
  );
}
