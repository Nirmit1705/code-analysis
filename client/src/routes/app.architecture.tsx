import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Search, Layers, X } from "lucide-react";

import { ArchGraph, KIND_META, nodeById, type ModuleKind } from "@/components/arch-graph";
import { AiNote, Panel, PanelHeader, Tag } from "@/components/ui-kit";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { toArchGraphEdges } from "@/lib/graph-layout";
import {
  circularSummary,
  useArchitecture,
  useComponentInsight,
} from "@/lib/queries/architecture";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/architecture")({
  head: () => ({
    meta: [
      { title: "Architecture Explorer — CodeLens AI" },
      {
        name: "description",
        content:
          "Interactive architecture graph inferred from source: components, boundaries and data flows.",
      },
      { property: "og:title", content: "Architecture Explorer — CodeLens AI" },
      {
        property: "og:description",
        content: "Zoom, filter and inspect every component of your system.",
      },
    ],
  }),
  component: Architecture,
});

const kinds: Array<{ id: ModuleKind; label: string }> = [
  { id: "frontend", label: "Frontend" },
  { id: "gateway", label: "Gateway" },
  { id: "service", label: "Services" },
  { id: "data", label: "Data" },
  { id: "cache", label: "Cache" },
  { id: "external", label: "External" },
];

function Architecture() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const architecture = useArchitecture(activeRepositoryId);

  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState<ModuleKind[]>([]);
  const [group, setGroup] = useState<"service" | "module">("service");

  // Generated on demand, only once a component is selected (see
  // `component_insight_service`); repeats are served from the backend cache.
  const insight = useComponentInsight(
    activeRepositoryId,
    architecture.data?.analysis_run_id ?? null,
    selected,
  );

  const data = architecture.data;
  // `ArchGraph` lays itself out; these are the plain backend nodes.
  const positioned = data?.nodes ?? [];
  const archGraphEdges = data ? toArchGraphEdges(data.edges) : [];
  const edges = data?.edges ?? [];

  const node = selected ? nodeById(positioned, selected) : null;
  // In a cycle either through a flagged edge to another node, or entirely
  // within itself (`circular_files`, which has no edge to carry a flag).
  const isCircularNode = node
    ? node.circular_files > 0 ||
      edges.some((e) => e.circular && (e.source === node.id || e.target === node.id))
    : false;
  const dependencies = node
    ? edges.filter((e) => e.source === node.id).map((e) => nodeById(positioned, e.target)!)
    : [];
  const dependents = node
    ? edges.filter((e) => e.target === node.id).map((e) => nodeById(positioned, e.source)!)
    : [];

  const subtitle = !data?.analyzed
    ? "—"
    : `${data.nodes.length} components · ${data.edges.length} inferred edges · ${circularSummary(data)}`;

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Architecture Explorer</h1>
          <p className="text-[12px] text-muted-foreground">{subtitle}</p>
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
                  group === g
                    ? "bg-violet/18 text-foreground"
                    : "text-muted-foreground hover:bg-surface-2",
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
                "focus-ring flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11.5px]",
                on
                  ? "border-violet/45 bg-violet/15 text-foreground"
                  : "border-border text-muted-foreground hover:border-border-strong",
              )}
            >
              <span
                className="size-2.5 rounded-[4px]"
                style={{ background: KIND_META[k.id].color }}
              />
              {k.label}
            </button>
          );
        })}
        {active.length ? (
          <button
            onClick={() => setActive([])}
            className="px-1.5 text-[11.5px] text-subtle hover:text-foreground"
          >
            clear filters
          </button>
        ) : null}
        <span className="ml-auto font-mono text-[11px] text-subtle">
          Scroll to zoom · drag to pan · click a node for detail
        </span>
      </div>

      {repositories.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading…</p>
        </Panel>
      ) : activeRepositoryId === null ? (
        <Panel>
          <div className="p-10 text-center">
            <p className="text-[13px] text-subtle">No repository connected yet.</p>
            <Link
              to="/onboarding"
              className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline"
            >
              Connect a repository
            </Link>
          </div>
        </Panel>
      ) : architecture.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading architecture…</p>
        </Panel>
      ) : architecture.isError || !data ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-danger">
            Failed to load the architecture graph.
          </p>
        </Panel>
      ) : !data.analyzed ? (
        <Panel>
          <div className="p-10 text-center">
            <p className="text-[13px] text-subtle">This repository hasn't been analyzed yet.</p>
            <Link
              to="/onboarding"
              className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline"
            >
              Run the first analysis
            </Link>
          </div>
        </Panel>
      ) : (
        <>
          <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[1fr_352px]">
            <ArchGraph
              nodes={positioned}
              edges={archGraphEdges}
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
                  <div className="grid grid-cols-2 gap-3">
                    <div className="rounded-md border border-border bg-surface-2/40 px-3 py-2">
                      <p className="label-xs">Files</p>
                      <p className="mt-0.5 font-mono text-[15px]">{node.files}</p>
                    </div>
                    <div className="rounded-md border border-border bg-surface-2/40 px-3 py-2">
                      <p className="label-xs">Lines of code</p>
                      <p className="mt-0.5 font-mono text-[15px]">{node.loc.toLocaleString()}</p>
                    </div>
                  </div>

                  {isCircularNode ? (
                    <Tag tone="danger">
                      {node.circular_files > 0
                        ? `${node.circular_files} of ${node.files} files are in a circular dependency`
                        : "Part of a circular dependency"}
                    </Tag>
                  ) : null}

                  <div>
                    <p className="label-xs mb-1.5">Key files</p>
                    {node.key_files.length ? (
                      <ul className="space-y-1">
                        {node.key_files.map((f) => (
                          <li
                            key={f}
                            className="truncate rounded-sm border border-border bg-surface-2/50 px-2 py-1 font-mono text-[11.5px] text-muted-foreground"
                          >
                            {f}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-[11.5px] text-subtle">none</p>
                    )}
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <p className="label-xs mb-1.5">Dependencies</p>
                      <div className="flex flex-wrap gap-1">
                        {dependencies.length ? (
                          dependencies.map((d) => <Tag key={d.id}>{d.label}</Tag>)
                        ) : (
                          <span className="text-[11.5px] text-subtle">none</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="label-xs mb-1.5">Dependents</p>
                      <div className="flex flex-wrap gap-1">
                        {dependents.length ? (
                          dependents.map((d) => <Tag key={d.id}>{d.label}</Tag>)
                        ) : (
                          <span className="text-[11.5px] text-subtle">none</span>
                        )}
                      </div>
                    </div>
                  </div>

                  {node.apis.length ? (
                    <div>
                      <p className="label-xs mb-1.5">APIs</p>
                      <ul className="space-y-1">
                        {node.apis.map((a) => (
                          <li key={a} className="font-mono text-[11.5px] text-cyan">
                            {a}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}

                  {insight.isLoading ? (
                    <div
                      className="rounded-md border border-violet/22 bg-violet/[0.07] p-3"
                      aria-busy="true"
                    >
                      <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-violet-soft uppercase">
                        <span className="pulse-node inline-block size-1.5 rounded-full bg-violet" />
                        AI insight
                      </p>
                      <div className="flex flex-col gap-2" aria-hidden>
                        {["w-[94%]", "w-[82%]", "w-[60%]"].map((w) => (
                          <span
                            key={w}
                            className={cn("h-3 animate-pulse rounded-md bg-surface-3", w)}
                          />
                        ))}
                      </div>
                    </div>
                  ) : (
                    <AiNote>
                      {insight.data?.insight ?? "No AI insight available for this component."}
                    </AiNote>
                  )}
                </div>
              </Panel>
            ) : (
              <Panel className="hidden xl:flex">
                <div className="m-auto max-w-[220px] p-6 text-center text-[12.5px] text-subtle">
                  Select a component in the graph to inspect its files, dependencies and endpoints.
                </div>
              </Panel>
            )}
          </div>

          <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3 xl:grid-cols-6">
            {positioned.slice(0, 6).map((n) => (
              <button
                key={n.id}
                onClick={() => setSelected(n.id)}
                className="bg-surface px-3 py-2.5 text-left transition-colors hover:bg-surface-2"
              >
                <p className="truncate text-[12px] font-medium">{n.label}</p>
                <p className="mt-0.5 font-mono text-[11px] text-subtle">
                  {n.files} files · {n.kind}
                </p>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
