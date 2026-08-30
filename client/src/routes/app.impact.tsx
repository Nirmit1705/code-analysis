import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Target, ChevronRight, AlertTriangle } from "lucide-react";

import { AiNote, Panel, PanelHeader, Ring, Tag } from "@/components/ui-kit";
import { impactTargets } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/impact")({
  head: () => ({
    meta: [
      { title: "Impact Analysis — Substrate" },
      { name: "description", content: "Select a file, function, endpoint or module and see everything affected downstream before you ship a change." },
      { property: "og:title", content: "Impact Analysis — Substrate" },
      { property: "og:description", content: "Direct and indirect blast radius, API impact and risk scoring for any change." },
    ],
  }),
  component: Impact,
});

const results: Record<string, {
  risk: "HIGH" | "MEDIUM" | "LOW";
  score: number;
  direct: Array<{ file: string; why: string }>;
  indirect: Array<{ file: string; hops: number }>;
  apis: string[];
  tests: number;
  explain: string;
}> = {
  "services/auth/src/auth.service.ts": {
    risk: "HIGH",
    score: 84,
    direct: [
      { file: "services/auth/src/auth.controller.ts", why: "calls validateCredentials() and issueTokens()" },
      { file: "services/auth/src/auth.guard.ts", why: "resolves scopes from tokens minted here" },
      { file: "services/billing/src/billing.service.ts", why: "imports AuthService directly (coupling violation)" },
    ],
    indirect: [
      { file: "services/user/src/user.controller.ts", hops: 2 },
      { file: "services/user/src/profile.service.ts", hops: 2 },
      { file: "apps/gateway/src/routes/auth.ts", hops: 2 },
      { file: "workers/analytics/pipeline.py", hops: 3 },
    ],
    apis: ["POST /api/login", "POST /api/register", "POST /api/token/refresh", "GET /api/me"],
    tests: 34,
    explain:
      "This change may affect authentication middleware used by four modules. Token shape is consumed by auth.guard.ts and by billing's seat checks, so any change to claims requires a coordinated deploy. Refresh rotation writes to Redis — altering the key format invalidates all live sessions.",
  },
  "services/interview/src/interview.service.ts": {
    risk: "MEDIUM",
    score: 61,
    direct: [
      { file: "services/interview/src/interview.controller.ts", why: "primary caller for all interview routes" },
      { file: "services/interview/src/scheduler.ts", why: "shares conflict-detection helpers" },
    ],
    indirect: [
      { file: "workers/analytics/pipeline.py", hops: 2 },
      { file: "apps/web/src/routes/interviews.index.tsx", hops: 3 },
    ],
    apis: ["POST /api/interviews", "GET /api/interviews/:id", "POST /api/interviews/:id/score"],
    tests: 52,
    explain:
      "scheduleInterview() has a cyclomatic complexity of 34 and no isolated unit tests for timezone resolution. Split the function before changing scheduling behaviour, otherwise regressions surface only in end-to-end runs.",
  },
  "services/billing/src/webhooks/stripe.ts": {
    risk: "HIGH",
    score: 78,
    direct: [{ file: "services/billing/src/billing.service.ts", why: "applies subscription and seat mutations" }],
    indirect: [{ file: "services/auth/src/auth.guard.ts", hops: 2 }, { file: "apps/web/src/routes/settings.billing.tsx", hops: 3 }],
    apis: ["POST /api/public/webhooks/stripe"],
    tests: 11,
    explain:
      "The handler is not idempotent, so a change to event processing order can double-apply seat changes. Add a processed-event table keyed on the Stripe event id before modifying this file.",
  },
  "packages/cache/src/redis.ts": {
    risk: "HIGH",
    score: 88,
    direct: [
      { file: "services/auth/src/auth.service.ts", why: "session storage" },
      { file: "apps/gateway/src/plugins/rate-limit.ts", why: "counter storage" },
      { file: "packages/cache/src/queues.ts", why: "BullMQ connection" },
    ],
    indirect: [{ file: "services/interview/src/scheduler.ts", hops: 2 }, { file: "workers/analytics/pipeline.py", hops: 3 }],
    apis: ["ALL /api/*"],
    tests: 19,
    explain:
      "Every authenticated request touches this module through either rate limiting or session lookup. A connection or serialization change is effectively a platform-wide change.",
  },
  "apps/gateway/src/plugins/rate-limit.ts": {
    risk: "MEDIUM",
    score: 55,
    direct: [{ file: "apps/gateway/src/server.ts", why: "plugin registration" }],
    indirect: [{ file: "services/auth/src/auth.controller.ts", hops: 2 }],
    apis: ["POST /api/login", "POST /api/register"],
    tests: 8,
    explain:
      "Two plugins declare divergent windows (60s and 120s). Consolidate before tuning limits, or the effective limit depends on registration order.",
  },
};

function Impact() {
  const [target, setTarget] = useState(impactTargets[0]!);
  const r = results[target]!;
  const tone = r.risk === "HIGH" ? "danger" : r.risk === "MEDIUM" ? "warning" : "success";

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div>
        <h1 className="text-[17px] font-semibold tracking-tight">Impact Analysis</h1>
        <p className="text-[12px] text-muted-foreground">
          Select a file, function, endpoint or module to compute its blast radius on branch main.
        </p>
      </div>

      <div className="grid gap-3 xl:grid-cols-[300px_1fr]">
        <Panel className="h-fit">
          <PanelHeader title="Select a target" subtitle="files · functions · modules · APIs" icon={<Target className="size-3.5" />} />
          <ul className="divide-y divide-border">
            {impactTargets.map((t) => (
              <li key={t}>
                <button
                  onClick={() => setTarget(t)}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-2.5 text-left font-mono text-[11.5px] transition-colors",
                    target === t ? "bg-violet/12 text-foreground" : "text-muted-foreground hover:bg-surface-2",
                  )}
                >
                  <span className="truncate">{t}</span>
                  <ChevronRight className={cn("ml-auto size-3", target === t ? "text-violet-soft" : "text-subtle")} />
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-3">
          <Panel ambient>
            <div className="flex flex-wrap items-center gap-4 p-4">
              <div>
                <p className="label-xs">Selected</p>
                <p className="mt-1 font-mono text-[14px]">{target}</p>
              </div>
              <div className="ml-auto flex items-center gap-5">
                <div className="text-right">
                  <p className="label-xs">Affected files</p>
                  <p className="font-mono text-[17px]">{r.direct.length + r.indirect.length}</p>
                </div>
                <div className="text-right">
                  <p className="label-xs">Tests covering</p>
                  <p className="font-mono text-[17px]">{r.tests}</p>
                </div>
                <div className="flex items-center gap-3 rounded-md border border-border bg-surface-2/50 px-4 py-2">
                  <Ring value={r.score} tone={tone} />
                  <div>
                    <p className="label-xs">Risk</p>
                    <p className={cn("flex items-center gap-1.5 font-mono text-[15px]", `text-${tone === "danger" ? "danger" : tone === "warning" ? "warning" : "success"}`)}>
                      <AlertTriangle className="size-3.5" /> {r.risk}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </Panel>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel>
              <PanelHeader title="Directly affected" subtitle="1 hop · import or call site" />
              <ul className="divide-y divide-border">
                {r.direct.map((d) => (
                  <li key={d.file} className="px-4 py-2.5">
                    <p className="font-mono text-[11.5px] text-foreground">{d.file}</p>
                    <p className="mt-0.5 text-[11.5px] text-muted-foreground">{d.why}</p>
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel>
              <PanelHeader title="Indirectly affected" subtitle="2+ hops through the dependency graph" />
              <ul className="divide-y divide-border">
                {r.indirect.map((d) => (
                  <li key={d.file} className="flex items-center gap-2 px-4 py-2.5">
                    <p className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-muted-foreground">{d.file}</p>
                    <Tag tone={d.hops > 2 ? "neutral" : "warning"} mono>{d.hops} hops</Tag>
                  </li>
                ))}
              </ul>
            </Panel>
          </div>

          <Panel>
            <PanelHeader title="Potential API impact" subtitle="endpoints reachable from this change" />
            <div className="flex flex-wrap gap-2 p-4">
              {r.apis.map((a) => (
                <span key={a} className="rounded-md border border-cyan/25 bg-cyan/[0.07] px-2.5 py-1 font-mono text-[11.5px] text-cyan">
                  {a}
                </span>
              ))}
            </div>
          </Panel>

          <AiNote>{r.explain}</AiNote>
        </div>
      </div>
    </div>
  );
}
