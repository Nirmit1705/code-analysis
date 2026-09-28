import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Github, Settings, Trash2, RefreshCcw } from "lucide-react";

import { Panel, PanelHeader, Tag } from "@/components/ui-kit";
import { repo } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/repository")({
  head: () => ({
    meta: [
      { title: "Repository Settings — CodeLens AI" },
      { name: "description", content: "Configure tracked branches, analysis schedule, ignore patterns and access for a connected repository." },
      { property: "og:title", content: "Repository Settings — CodeLens AI" },
      { property: "og:description", content: "Control how CodeLens AI analyses this repository." },
    ],
  }),
  component: RepositorySettings,
});

const trackedBranches = ["main", "release/2.4", "feature/payment"];

function RepositorySettings() {
  const [branches, setBranches] = useState(trackedBranches);
  const [schedule, setSchedule] = useState("on-push");
  const [ignore, setIgnore] = useState("dist/\nbuild/\n**/*.snap\ncoverage/\nfixtures/**");

  return (
    <div className="mx-auto max-w-4xl space-y-3 p-4">
      <div>
        <h1 className="text-[17px] font-semibold tracking-tight">Repository Settings</h1>
        <p className="text-[12px] text-muted-foreground">Applies to {repo.name} only.</p>
      </div>

      <Panel>
        <PanelHeader title="Connection" icon={<Github className="size-3.5" />} actions={<Tag tone="success">connected</Tag>} />
        <dl className="grid gap-px bg-border sm:grid-cols-2">
          {[
            ["Repository", repo.name],
            ["Default branch", repo.branch],
            ["Installation", "GitHub App · hiresight org"],
            ["Access", "read-only · contents, metadata"],
            ["Last analysis", "12 minutes ago · a3f91c2"],
            ["Indexed size", "184 MB · 3,184 files"],
          ].map(([k, v]) => (
            <div key={k} className="bg-surface px-4 py-3">
              <dt className="label-xs">{k}</dt>
              <dd className="mt-1 font-mono text-[12px]">{v}</dd>
            </div>
          ))}
        </dl>
      </Panel>

      <Panel>
        <PanelHeader title="Tracked branches" subtitle="analysed automatically" />
        <div className="space-y-2 p-4">
          {["main", "release/2.4", "feature/payment", "develop", "fix/session-rotation"].map((b) => {
            const on = branches.includes(b);
            return (
              <label key={b} className="flex cursor-pointer items-center gap-3 rounded-md border border-border bg-surface-2/40 px-3 py-2">
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => setBranches((p) => (on ? p.filter((x) => x !== b) : [...p, b]))}
                  className="size-3.5 accent-violet"
                />
                <span className="font-mono text-[12px]">{b}</span>
                {b === repo.branch ? <Tag className="ml-auto">default</Tag> : null}
              </label>
            );
          })}
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Analysis schedule" icon={<RefreshCcw className="size-3.5" />} />
        <div className="grid gap-2 p-4 sm:grid-cols-3">
          {[
            { id: "on-push", title: "On every push", sub: "recommended" },
            { id: "daily", title: "Daily at 02:00", sub: "lower cost" },
            { id: "manual", title: "Manual only", sub: "full control" },
          ].map((o) => (
            <button
              key={o.id}
              onClick={() => setSchedule(o.id)}
              className={cn(
                "focus-ring rounded-md border p-3 text-left",
                schedule === o.id ? "border-violet/45 bg-violet/12" : "border-border bg-surface hover:border-border-strong",
              )}
            >
              <p className="text-[12.5px] font-medium">{o.title}</p>
              <p className="mt-0.5 text-[11px] text-subtle">{o.sub}</p>
            </button>
          ))}
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Ignore patterns" subtitle="gitignore syntax · excluded from indexing" icon={<Settings className="size-3.5" />} />
        <div className="p-4">
          <textarea
            value={ignore}
            onChange={(e) => setIgnore(e.target.value)}
            rows={6}
            className="focus-ring w-full resize-none rounded-md border border-input bg-background p-3 font-mono text-[11.5px] outline-none"
          />
        </div>
      </Panel>

      <Panel className="border-danger/25">
        <PanelHeader title="Danger zone" subtitle="these actions cannot be undone" />
        <div className="flex flex-wrap items-center gap-3 p-4">
          <button className="focus-ring flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] hover:border-border-strong">
            Purge index and re-analyze
          </button>
          <button className="focus-ring flex items-center gap-1.5 rounded-md border border-danger/35 bg-danger/10 px-2.5 py-1.5 text-[12px] text-danger hover:bg-danger/18">
            <Trash2 className="size-3.5" /> Disconnect repository
          </button>
        </div>
      </Panel>
    </div>
  );
}
