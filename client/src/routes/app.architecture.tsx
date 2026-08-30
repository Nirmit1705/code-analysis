import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Search, Layers, X } from "lucide-react";

import { ArchGraph, nodeById } from "@/components/arch-graph";
import { AiNote, Panel, PanelHeader, Ring, Tag } from "@/components/ui-kit";
import { graphEdges, graphNodes, type GraphNode } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/architecture")({
  head: () => ({
    meta: [
      { title: "Architecture Explorer — Substrate" },
      { name: "description", content: "Interactive architecture graph inferred from source: components, boundaries, data flows and per-component health." },
      { property: "og:title", content: "Architecture Explorer — Substrate" },
      { property: "og:description", content: "Zoom, filter and inspect every component of your system with AI-generated reasoning." },
    ],
  }),
  component: Architecture,
});

const kinds: Array<{ id: GraphNode["kind"]; label: string }> = [
  { id: "frontend", label: "Frontend" },
  { id: "gateway", label: "Gateway" },
  { id: "service", label: "Services" },
  { id: "data", label: "Data" },
  { id: "cache", label: "Cache" },
  { id: "external", label: "External" },
];

function Architecture() {
  const [selected, setSelected] = useState<string | null>("auth");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState<GraphNode["kind"][]>([]);
  const [group, setGroup] = useState<"service" | "module">("service");

  const node = selected ? nodeById(selected) : null;
  const dependencies = node ? graphEdges.filter((e) => e.from === node.id).map((e) => nodeById(e.to)!) : [];
  const dependents = node ? graphEdges.filter((e) => e.to === node.id).map((e) => nodeById(e.from)!) : [];

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Architecture Explorer</h1>
          <p className="text-[12px] text-muted-foreground">
            9 components · 14 inferred edges · 2 risk paths · rebuilt on commit a3f91c2
          </p>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 rounded-md border border-input bg-background px-2 py-1.5">
            <Search className="size-3.5 text-subtle" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search components"
              className="w-40 bg-transparent font-mono text-[12px] outline-none placeholder:text-subtle"
            />
          </div>
          <div className="flex items-center overflow-hidden rounded-md border border-border">
            {(["service", "module"] as const).map((g) => (
              <button
                key={g}
                onClick={() => setGroup(g)}
                className={cn(
                  "px-2.5 py-1.5 text-[12px] capitalize",
                  group === g ? "bg-violet/18 text-foreground" : "text-muted-foreground hover:bg-surface-2",
                )}
              >
                <Layers className="mr-1 inline size-3" /> by {g}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {kinds.map((k) => {
          const on = active.includes(k.id);
          return (
            <button
              key={k.id}
              onClick={() => setActive((a) => (on ? a.filter((x) => x !== k.id) : [...a, k.id]))}
              className={cn(
                "focus-ring rounded-sm border px-2 py-1 text-[11.5px]",
                on ? "border-violet/45 bg-violet/15 text-foreground" : "border-border text-muted-foreground hover:border-border-strong",
              )}
            >
              {k.label}
            </button>
          );
        })}
        {active.length ? (
          <button onClick={() => setActive([])} className="px-1.5 text-[11.5px] text-subtle hover:text-foreground">
            clear filters
          </button>
        ) : null}
        <span className="ml-auto font-mono text-[11px] text-subtle">
          scroll to zoom · drag to pan · click a node for detail
        </span>
      </div>

      <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[1fr_352px]">
        <ArchGraph
          selectedId={selected}
          onSelect={setSelected}
          filterKinds={active}
          query={query}
          className="h-full min-h-[420px]"
        />

        {node ? (
          <Panel className="flex min-h-0 flex-col overflow-hidden">
            <PanelHeader
              title={node.label}
              subtitle={`${node.kind} · ${node.files} files`}
              actions={
                <button
                  onClick={() => setSelected(null)}
                  className="focus-ring rounded-sm p-1 text-subtle hover:bg-surface-2 hover:text-foreground"
                >
                  <X className="size-3.5" />
                </button>
              }
            />
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
              <div className="flex items-center gap-3">
                <Ring value={node.health} tone={node.health >= 80 ? "success" : node.health >= 70 ? "warning" : "danger"} />
                <div>
                  <p className="label-xs">Component health</p>
                  <p className="text-[12px] text-muted-foreground">
                    {node.health >= 80 ? "Healthy" : node.health >= 70 ? "Watch" : "At risk"}
                  </p>
                </div>
              </div>

              <p className="text-[12.5px] leading-relaxed text-secondary-foreground">{node.description}</p>

              <div>
                <p className="label-xs mb-1.5">Key files</p>
                <ul className="space-y-1">
                  {node.keyFiles.map((f) => (
                    <li key={f} className="truncate rounded-sm border border-border bg-surface-2/50 px-2 py-1 font-mono text-[11.5px] text-muted-foreground">
                      {f}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <p className="label-xs mb-1.5">Dependencies</p>
                  <div className="flex flex-wrap gap-1">
                    {dependencies.length ? dependencies.map((d) => <Tag key={d.id}>{d.label}</Tag>) : <span className="text-[11.5px] text-subtle">none</span>}
                  </div>
                </div>
                <div>
                  <p className="label-xs mb-1.5">Dependents</p>
                  <div className="flex flex-wrap gap-1">
                    {dependents.length ? dependents.map((d) => <Tag key={d.id}>{d.label}</Tag>) : <span className="text-[11.5px] text-subtle">none</span>}
                  </div>
                </div>
              </div>

              {node.apis.length ? (
                <div>
                  <p className="label-xs mb-1.5">APIs</p>
                  <ul className="space-y-1">
                    {node.apis.map((a) => (
                      <li key={a} className="font-mono text-[11.5px] text-cyan">{a}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <AiNote>{node.insight}</AiNote>
            </div>
          </Panel>
        ) : (
          <Panel className="hidden xl:flex">
            <div className="m-auto max-w-[220px] p-6 text-center text-[12.5px] text-subtle">
              Select a component in the graph to inspect its files, dependencies, endpoints and health.
            </div>
          </Panel>
        )}
      </div>

      <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3 xl:grid-cols-6">
        {graphNodes.slice(0, 6).map((n) => (
          <button
            key={n.id}
            onClick={() => setSelected(n.id)}
            className="bg-surface px-3 py-2.5 text-left transition-colors hover:bg-surface-2"
          >
            <p className="truncate text-[12px] font-medium">{n.label}</p>
            <p className="mt-0.5 font-mono text-[11px] text-subtle">
              {n.files} files · health {n.health}
            </p>
          </button>
        ))}
      </div>
    </div>
  );
}
