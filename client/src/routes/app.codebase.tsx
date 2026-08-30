import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { ChevronRight, ChevronDown, FileCode2, Search, Sparkles, Folder } from "lucide-react";

import { AiNote, PanelHeader, ScoreBar, Tag } from "@/components/ui-kit";
import { authServiceSource, fileInsight } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/codebase")({
  head: () => ({
    meta: [
      { title: "Codebase Explorer — Substrate" },
      { name: "description", content: "IDE-style codebase explorer with file tree, syntax-aware code viewer and an AI insights panel per file." },
      { property: "og:title", content: "Codebase Explorer — Substrate" },
      { property: "og:description", content: "Read any file alongside its purpose, dependencies, dependents, complexity and related APIs." },
    ],
  }),
  component: Codebase,
});

type Tree = { name: string; children?: Tree[]; file?: boolean };

const tree: Tree[] = [
  {
    name: "apps",
    children: [
      { name: "web", children: [{ name: "src/main.tsx", file: true }, { name: "src/lib/api-client.ts", file: true }] },
      { name: "gateway", children: [{ name: "src/server.ts", file: true }, { name: "src/plugins/rate-limit.ts", file: true }] },
    ],
  },
  {
    name: "services",
    children: [
      {
        name: "auth",
        children: [
          { name: "src/auth.controller.ts", file: true },
          { name: "src/auth.service.ts", file: true },
          { name: "src/jwt.strategy.ts", file: true },
          { name: "src/auth.guard.ts", file: true },
        ],
      },
      { name: "interview", children: [{ name: "src/interview.service.ts", file: true }, { name: "src/scheduler.ts", file: true }] },
      { name: "billing", children: [{ name: "src/billing.service.ts", file: true }, { name: "src/webhooks/stripe.ts", file: true }] },
    ],
  },
  { name: "packages", children: [{ name: "cache/src/redis.ts", file: true }, { name: "integrations/src/stripe.ts", file: true }] },
  { name: "db", children: [{ name: "schema.prisma", file: true }] },
];

const KEYWORDS = /\b(import|from|export|class|const|await|async|return|throw|new|private|readonly|constructor|if|this)\b/g;

function highlight(line: string) {
  if (line.trim().startsWith("//")) {
    return <span className="text-subtle italic">{line}</span>;
  }
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const m of line.matchAll(KEYWORDS)) {
    const i = m.index!;
    parts.push(line.slice(last, i));
    parts.push(
      <span key={`${i}-${m[0]}`} className="text-violet-soft">
        {m[0]}
      </span>,
    );
    last = i + m[0].length;
  }
  parts.push(line.slice(last));
  return parts;
}

function Node({ node, depth, selected, onSelect }: { node: Tree; depth: number; selected: string; onSelect: (n: string) => void }) {
  const [open, setOpen] = useState(depth < 1 || node.name === "auth" || node.name === "services");
  if (node.file) {
    const active = selected === node.name;
    return (
      <button
        onClick={() => onSelect(node.name)}
        style={{ paddingLeft: depth * 12 + 8 }}
        className={cn(
          "flex w-full items-center gap-1.5 py-[3px] pr-2 text-left font-mono text-[11.5px] transition-colors",
          active ? "bg-violet/14 text-foreground" : "text-muted-foreground hover:bg-surface-2",
        )}
      >
        <FileCode2 className={cn("size-3 shrink-0", active ? "text-violet-soft" : "text-subtle")} />
        <span className="truncate">{node.name.split("/").pop()}</span>
      </button>
    );
  }
  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{ paddingLeft: depth * 12 + 8 }}
        className="flex w-full items-center gap-1 py-[3px] text-left font-mono text-[11.5px] text-foreground hover:bg-surface-2"
      >
        {open ? <ChevronDown className="size-3 text-subtle" /> : <ChevronRight className="size-3 text-subtle" />}
        <Folder className="size-3 text-info/80" />
        <span className="truncate">{node.name}</span>
      </button>
      {open
        ? node.children?.map((c) => (
            <Node key={c.name} node={c} depth={depth + 1} selected={selected} onSelect={onSelect} />
          ))
        : null}
    </div>
  );
}

function Codebase() {
  const [selected, setSelected] = useState("src/auth.service.ts");
  const [filter, setFilter] = useState("");
  const lines = authServiceSource.split("\n");

  return (
    <div className="grid h-[calc(100vh-3rem)] grid-cols-[224px_1fr] gap-0 xl:grid-cols-[224px_1fr_320px]">
      {/* LEFT — file tree */}
      <aside className="flex min-h-0 flex-col border-r border-border bg-surface/40">
        <div className="flex items-center gap-2 border-b border-border px-2.5 py-2">
          <Search className="size-3.5 text-subtle" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Find file"
            className="w-full bg-transparent font-mono text-[11.5px] outline-none placeholder:text-subtle"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-1.5">
          {tree.map((n) => (
            <Node key={n.name} node={n} depth={0} selected={selected} onSelect={setSelected} />
          ))}
        </div>
        <div className="border-t border-border px-2.5 py-2 font-mono text-[10.5px] text-subtle">
          3,184 files · 47 modules
        </div>
      </aside>

      {/* CENTER — code viewer */}
      <section className="flex min-h-0 flex-col bg-background">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <FileCode2 className="size-3.5 text-violet-soft" />
          <span className="font-mono text-[12px]">{fileInsight.path}</span>
          <Tag tone="neutral" mono>TypeScript</Tag>
          <Tag tone="warning">complexity {fileInsight.complexity.score}</Tag>
          <span className="ml-auto font-mono text-[11px] text-subtle">{fileInsight.complexity.loc} lines</span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <pre className="min-w-full">
            <code className="block py-2 font-mono text-[12px] leading-[1.55]">
              {lines.map((l, i) => (
                <span
                  key={i}
                  className={cn(
                    "flex px-1 hover:bg-surface/70",
                    l.includes("NOTE:") && "bg-warning/[0.07]",
                  )}
                >
                  <span className="w-10 shrink-0 pr-3 text-right text-subtle select-none">{i + 1}</span>
                  <span className="whitespace-pre text-secondary-foreground">{highlight(l)}</span>
                </span>
              ))}
            </code>
          </pre>
        </div>
      </section>

      {/* RIGHT — AI insights */}
      <aside className="hidden min-h-0 flex-col overflow-y-auto border-l border-border bg-surface/40 xl:flex">
        <PanelHeader title="AI Insights" subtitle={selected.split("/").pop() ?? ""} icon={<Sparkles className="size-3.5" />} />
        <div className="space-y-4 p-3.5">
          <div>
            <p className="label-xs mb-1.5">Purpose</p>
            <p className="text-[12.5px] leading-relaxed text-secondary-foreground">{fileInsight.purpose}</p>
          </div>

          <div>
            <p className="label-xs mb-1.5">Complexity</p>
            <div className="rounded-md border border-border bg-surface p-3">
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-[17px] text-warning">{fileInsight.complexity.score}</span>
                <span className="text-[11.5px] text-warning">{fileInsight.complexity.label}</span>
              </div>
              <div className="mt-2">
                <ScoreBar value={70} tone="warning" />
              </div>
              <p className="mt-2 font-mono text-[11px] text-subtle">
                {fileInsight.complexity.functions} functions · {fileInsight.complexity.branches} branches
              </p>
            </div>
          </div>

          <div>
            <p className="label-xs mb-1.5">Dependencies</p>
            <ul className="space-y-1">
              {fileInsight.dependencies.map((d) => (
                <li key={d} className="font-mono text-[11.5px] text-muted-foreground">{d}</li>
              ))}
            </ul>
          </div>

          <div>
            <p className="label-xs mb-1.5">Used by</p>
            <ul className="space-y-1">
              {fileInsight.usedBy.map((d) => (
                <li key={d} className="font-mono text-[11.5px] text-muted-foreground">{d}</li>
              ))}
            </ul>
          </div>

          <div>
            <p className="label-xs mb-1.5">Related APIs</p>
            <ul className="space-y-1">
              {fileInsight.apis.map((a) => (
                <li key={a} className="font-mono text-[11.5px] text-cyan">{a}</li>
              ))}
            </ul>
          </div>

          <AiNote title="AI explanation">{fileInsight.explanation}</AiNote>
        </div>
      </aside>
    </div>
  );
}
