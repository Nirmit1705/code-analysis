import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { GraduationCap, CheckCircle2, Circle, ArrowRight } from "lucide-react";

import { AiNote, Panel, PanelHeader, ScoreBar, Tag } from "@/components/ui-kit";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/onboarding")({
  head: () => ({
    meta: [
      { title: "Developer Onboarding — CodeLens AI" },
      { name: "description", content: "A generated learning path that walks a new engineer through this codebase in the order the system actually works." },
      { property: "og:title", content: "Developer Onboarding — CodeLens AI" },
      { property: "og:description", content: "Turn a repository into a guided curriculum for new team members." },
    ],
  }),
  component: OnboardingPath,
});

const path = [
  {
    title: "1 · The request lifecycle",
    est: "35 min",
    done: true,
    files: ["apps/gateway/src/server.ts", "apps/gateway/src/plugins/auth.ts"],
    body: "Start at the gateway. Read how a request is authenticated, rate limited and forwarded. Everything else in the system assumes this contract.",
  },
  {
    title: "2 · Authentication and sessions",
    est: "50 min",
    done: true,
    files: ["services/auth/src/auth.service.ts", "services/auth/src/jwt.strategy.ts"],
    body: "The highest fan-in module in the repository. Understand token minting, refresh rotation and how scopes are resolved before touching any service.",
  },
  {
    title: "3 · The interview domain",
    est: "1h 10m",
    done: false,
    files: ["services/interview/src/interview.service.ts", "services/interview/src/scoring/rubric.ts"],
    body: "The core product domain. scheduleInterview() is the most complex function in the codebase — read it alongside DEBT-091 so you understand both the behaviour and the planned split.",
  },
  {
    title: "4 · Data access patterns",
    est: "45 min",
    done: false,
    files: ["packages/db/prisma/schema.prisma", "services/user/src/user.repository.ts"],
    body: "All persistence flows through packages/db. Learn the tenant scoping rule and the cursor pagination convention before writing a repository method.",
  },
  {
    title: "5 · Async processing",
    est: "40 min",
    done: false,
    files: ["packages/cache/src/queues.ts", "workers/analytics/pipeline.py"],
    body: "Queues and the analytics ETL. This is where read-replica routing and the 15 minute aggregation window matter.",
  },
  {
    title: "6 · Ship a first change",
    est: "1h",
    done: false,
    files: ["services/interview/src/interview.controller.ts"],
    body: "A scoped starter task: add a filter parameter to GET /api/interviews. Small blast radius, 52 covering tests, exercises the whole stack.",
  },
];

function OnboardingPath() {
  const [open, setOpen] = useState(2);
  const completed = path.filter((p) => p.done).length;

  return (
    <div className="mx-auto max-w-5xl space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Developer Onboarding</h1>
          <p className="text-[12px] text-muted-foreground">
            Generated from the dependency graph — ordered by what depends on what.
          </p>
        </div>
        <Tag tone="violet" className="ml-auto">est. 5h total</Tag>
      </div>

      <Panel ambient>
        <div className="flex flex-wrap items-center gap-4 p-4">
          <GraduationCap className="size-5 text-violet-soft" />
          <div className="min-w-[200px] flex-1">
            <div className="mb-1.5 flex items-baseline text-[12px]">
              <span>Progress</span>
              <span className="ml-auto font-mono text-[11.5px] text-muted-foreground">{completed} / {path.length} modules</span>
            </div>
            <ScoreBar value={(completed / path.length) * 100} />
          </div>
          <Link
            to="/app/codebase"
            className="focus-ring flex items-center gap-1.5 rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] px-3 py-1.5 text-[12px] hover:bg-[oklch(0.745_0.19_44)]"
          >
            Continue in codebase <ArrowRight className="size-3.5" />
          </Link>
        </div>
      </Panel>

      <div className="space-y-2">
        {path.map((m, i) => (
          <Panel key={m.title}>
            <button
              onClick={() => setOpen(i === open ? -1 : i)}
              className="focus-ring flex w-full items-center gap-3 px-4 py-3 text-left"
            >
              {m.done ? (
                <CheckCircle2 className="size-4 text-success" />
              ) : (
                <Circle className="size-4 text-border-strong" />
              )}
              <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{m.title}</span>
              <Tag mono>{m.est}</Tag>
            </button>
            {open === i ? (
              <div className="space-y-3 border-t border-border p-4">
                <p className="text-[12.5px] leading-relaxed text-secondary-foreground">{m.body}</p>
                <div>
                  <p className="label-xs mb-1.5">Read these files</p>
                  <div className="flex flex-wrap gap-1.5">
                    {m.files.map((f) => <Tag key={f} mono>{f}</Tag>)}
                  </div>
                </div>
              </div>
            ) : null}
          </Panel>
        ))}
      </div>

      <AiNote>
        This path is ordered so that every module you read depends only on modules you have already read. When the auth ↔
        billing cycle is removed, step 2 shortens by roughly 20 minutes because scope resolution becomes a standalone
        package with its own tests.
      </AiNote>
    </div>
  );
}
