import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Plug, Search } from "lucide-react";

import { AiNote, Panel, PanelHeader, Tag } from "@/components/ui-kit";
import { apiEndpoints } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/api")({
  head: () => ({
    meta: [
      { title: "API Reference — Substrate" },
      { name: "description", content: "Every endpoint discovered in the codebase with request shape, response shape, auth requirements and call sites." },
      { property: "og:title", content: "API Reference — Substrate" },
      { property: "og:description", content: "A reference generated from route handlers and types, not from hand-written specs." },
    ],
  }),
  component: ApiReference,
});

const methodTone: Record<string, string> = {
  GET: "text-success border-success/30 bg-success/10",
  POST: "text-info border-info/30 bg-info/10",
  PUT: "text-warning border-warning/30 bg-warning/10",
  DELETE: "text-danger border-danger/30 bg-danger/10",
};

function ApiReference() {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(apiEndpoints[0]!.path);
  const list = apiEndpoints.filter((e) => (e.path + e.service).toLowerCase().includes(q.toLowerCase()));
  const ep = apiEndpoints.find((e) => e.path === sel)!;

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">API Reference</h1>
          <p className="text-[12px] text-muted-foreground">
            48 endpoints discovered across 4 services · generated from handlers and types.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2 rounded-md border border-input bg-background px-2 py-1.5">
          <Search className="size-3.5 text-subtle" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter endpoints"
            className="w-56 bg-transparent text-[12.5px] outline-none placeholder:text-subtle"
          />
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[420px_1fr]">
        <Panel className="h-fit overflow-hidden">
          <PanelHeader title="Endpoints" subtitle={`${list.length} shown`} icon={<Plug className="size-3.5" />} />
          <ul className="divide-y divide-border">
            {list.map((e) => (
              <li key={e.path}>
                <button
                  onClick={() => setSel(e.path)}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors",
                    sel === e.path ? "bg-violet/12" : "hover:bg-surface-2",
                  )}
                >
                  <span className={cn("rounded border px-1.5 py-0.5 font-mono text-[10px]", methodTone[e.method])}>
                    {e.method}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{e.path}</span>
                  <span className="font-mono text-[10.5px] text-subtle">{e.calls}</span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-3">
          <Panel ambient>
            <div className="flex flex-wrap items-center gap-3 p-4">
              <span className={cn("rounded border px-2 py-1 font-mono text-[11px]", methodTone[ep.method])}>{ep.method}</span>
              <span className="font-mono text-[15px]">{ep.path}</span>
              <Tag tone="violet" className="ml-auto">{ep.service}</Tag>
              <Tag mono>{ep.auth}</Tag>
            </div>
          </Panel>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel>
              <PanelHeader title="Request body" />
              <pre className="overflow-x-auto p-4 font-mono text-[11.5px] leading-relaxed text-cyan">{ep.body}</pre>
            </Panel>
            <Panel>
              <PanelHeader title="Response" subtitle="200 OK" />
              <pre className="overflow-x-auto p-4 font-mono text-[11.5px] leading-relaxed text-success">{ep.response}</pre>
            </Panel>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel>
              <PanelHeader title="Implementation files" />
              <ul className="divide-y divide-border">
                {ep.files.map((f) => (
                  <li key={f} className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">{f}</li>
                ))}
              </ul>
            </Panel>
            <Panel>
              <PanelHeader title="Called from" subtitle="frontend and internal callers" />
              <ul className="divide-y divide-border">
                {ep.usedBy.map((f) => (
                  <li key={f} className="px-4 py-2.5 font-mono text-[11.5px] text-muted-foreground">{f}</li>
                ))}
              </ul>
            </Panel>
          </div>

          <AiNote>
            Auth requirement <span className="mono text-violet-soft">{ep.auth}</span> was inferred from the guard chain on
            the handler, not from documentation. Traffic figures come from the last 7 days of gateway logs, so an endpoint
            with low volume and high coupling is usually the safest place to start refactoring.
          </AiNote>
        </div>
      </div>
    </div>
  );
}
