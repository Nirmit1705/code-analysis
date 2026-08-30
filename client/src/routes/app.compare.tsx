import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { GitCompareArrows, Plus, Minus, ArrowRight } from "lucide-react";

import { AiNote, Panel, PanelHeader, StatCell, Tag } from "@/components/ui-kit";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/compare")({
  head: () => ({
    meta: [
      { title: "Compare Branches — Substrate" },
      { name: "description", content: "Compare two branches or commits architecturally: new components, removed modules, changed dependencies and debt delta." },
      { property: "og:title", content: "Compare Branches — Substrate" },
      { property: "og:description", content: "An architectural diff, not a line diff." },
    ],
  }),
  component: Compare,
});

const branches = ["main", "feature/payment", "release/2.4", "fix/session-rotation"];

const added = [
  { name: "services/billing/src/webhooks/stripe.ts", note: "new public webhook handler" },
  { name: "services/billing/src/seat-policy.ts", note: "seat allocation rules" },
  { name: "packages/db/prisma/migrations/20260817_seats", note: "schema migration" },
];
const removed = [{ name: "services/billing/src/legacy-plan.ts", note: "superseded by seat-policy.ts" }];
const changedDeps = [
  { edge: "billing → auth", change: "new import of AuthService", risk: "high" },
  { edge: "billing → packages/db", change: "3 new repository calls", risk: "low" },
  { edge: "gateway → billing", change: "1 new public route", risk: "medium" },
];

function Compare() {
  const [base, setBase] = useState("main");
  const [head, setHead] = useState("feature/payment");

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div>
        <h1 className="text-[17px] font-semibold tracking-tight">Compare</h1>
        <p className="text-[12px] text-muted-foreground">Architectural diff between two branches or commits.</p>
      </div>

      <Panel ambient>
        <div className="flex flex-wrap items-center gap-3 p-4">
          <GitCompareArrows className="size-4 text-violet-soft" />
          <Selector value={base} onChange={setBase} />
          <ArrowRight className="size-3.5 text-subtle" />
          <Selector value={head} onChange={setHead} />
          <Tag tone="warning" className="ml-auto">architecture score 82 → 76</Tag>
          <Tag tone="danger">debt 72 → 78</Tag>
        </div>
      </Panel>

      <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
        <div className="bg-surface"><StatCell label="New components" value="2" tone="success" sub="billing subsystem" /></div>
        <div className="bg-surface"><StatCell label="Removed components" value="1" sub="legacy-plan.ts" /></div>
        <div className="bg-surface"><StatCell label="Changed dependencies" value="3" tone="warning" sub="1 introduces a cycle" /></div>
        <div className="bg-surface"><StatCell label="New endpoints" value="1" tone="info" sub="POST /api/public/webhooks/stripe" /></div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel>
          <PanelHeader title="Added" subtitle={`present in ${head}`} icon={<Plus className="size-3.5" />} />
          <ul className="divide-y divide-border">
            {added.map((a) => (
              <li key={a.name} className="px-4 py-2.5">
                <p className="font-mono text-[11.5px] text-success">+ {a.name}</p>
                <p className="mt-0.5 text-[11.5px] text-muted-foreground">{a.note}</p>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel>
          <PanelHeader title="Removed" subtitle={`only in ${base}`} icon={<Minus className="size-3.5" />} />
          <ul className="divide-y divide-border">
            {removed.map((a) => (
              <li key={a.name} className="px-4 py-2.5">
                <p className="font-mono text-[11.5px] text-danger">− {a.name}</p>
                <p className="mt-0.5 text-[11.5px] text-muted-foreground">{a.note}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <Panel>
        <PanelHeader title="Dependency changes" subtitle="edges added, removed or re-weighted" />
        <ul className="divide-y divide-border">
          {changedDeps.map((d) => (
            <li key={d.edge} className="flex items-center gap-3 px-4 py-2.5">
              <span className="font-mono text-[12px]">{d.edge}</span>
              <span className="text-[11.5px] text-muted-foreground">{d.change}</span>
              <Tag tone={d.risk === "high" ? "danger" : d.risk === "medium" ? "warning" : "success"} className="ml-auto">
                {d.risk} risk
              </Tag>
            </li>
          ))}
        </ul>
      </Panel>

      <AiNote>
        The branch <span className="mono text-violet-soft">{head}</span> introduces a direct import from billing into
        auth, which closes a new cycle and is the reason the architecture score drops 6 points. Route seat verification
        through <span className="mono text-violet-soft">packages/identity</span> before merging to keep both services
        independently deployable.
      </AiNote>
    </div>
  );
}

function Selector({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="focus-ring flex items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12px] hover:border-border-strong"
      >
        {value}
      </button>
      {open ? (
        <ul className="absolute z-30 mt-1 w-56 overflow-hidden rounded-md border border-border bg-surface-2 shadow-xl">
          {branches.map((b) => (
            <li key={b}>
              <button
                onClick={() => {
                  onChange(b);
                  setOpen(false);
                }}
                className={cn(
                  "w-full px-3 py-2 text-left font-mono text-[12px] hover:bg-violet/12",
                  b === value && "text-violet-soft",
                )}
              >
                {b}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
