import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { BookOpen, Download, RefreshCcw } from "lucide-react";

import { AiNote, Panel, PanelHeader, Tag } from "@/components/ui-kit";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/documentation")({
  head: () => ({
    meta: [
      { title: "Documentation — Substrate" },
      { name: "description", content: "An engineering wiki generated from the codebase: overview, architecture, modules, data models and setup." },
      { property: "og:title", content: "Documentation — Substrate" },
      { property: "og:description", content: "Always-current documentation derived from the code, not from stale hand-written pages." },
    ],
  }),
  component: Documentation,
});

const sections = [
  { id: "overview", title: "Project Overview" },
  { id: "architecture", title: "Architecture" },
  { id: "modules", title: "Modules" },
  { id: "data", title: "Data Models" },
  { id: "setup", title: "Setup & Deployment" },
];

const body: Record<string, { updated: string; source: string; blocks: Array<{ h?: string; p?: string; code?: string; list?: string[] }> }> = {
  overview: {
    updated: "generated 12 minutes ago",
    source: "README.md · package.json · 3,184 source files",
    blocks: [
      { p: "HireSight is a multi-tenant interview intelligence platform. Organisations schedule structured interviews, reviewers score candidates against rubrics, and an asynchronous worker aggregates transcripts into hiring signals." },
      { h: "System shape", p: "A Fastify gateway fronts four NestJS services (auth, user, interview, billing) plus a Python analytics worker. PostgreSQL is the system of record; Redis backs sessions, rate limiting and the BullMQ queues." },
      { h: "Runtime topology", list: [
        "apps/web — React 19 + TanStack Router frontend, deployed to the edge",
        "apps/gateway — Fastify 4 API gateway, JWT verification and rate limiting",
        "services/* — NestJS domain services communicating over HTTP and queues",
        "workers/analytics — Python 3.12 ETL, reads interview events every 15 minutes",
      ] },
    ],
  },
  architecture: {
    updated: "generated 12 minutes ago",
    source: "import graph · route registrations · docker-compose.yml",
    blocks: [
      { p: "Requests enter through the gateway, which verifies the access token, applies per-route rate limits and forwards to the owning service. Services never call each other's databases; the one exception is the auth ↔ billing import cycle flagged as DEBT-114." },
      { h: "Authentication flow", code: `client → POST /api/login
gateway (rate-limit: 5/min per IP)
  → auth.controller.login()
    → auth.service.validateCredentials()
      → user.repository.findByEmail()   [postgres]
    → auth.service.issueTokens()        [redis: session:*]
  ← 200 { accessToken, refreshToken, user }` },
      { h: "Boundaries worth preserving", list: [
        "Only the gateway is internet-facing; services bind to the private network",
        "packages/db owns all Prisma access — services must not import the client directly",
        "The analytics worker is read-only except for the aggregates schema",
      ] },
    ],
  },
  modules: {
    updated: "generated 12 minutes ago",
    source: "per-package barrel files · 112 undocumented exports detected",
    blocks: [
      { h: "services/auth", p: "Credential validation, token minting and rotation, scope resolution. Highest fan-in module in the repository (41 inbound references)." },
      { h: "services/interview", p: "Scheduling, rubric assignment and scoring. scheduleInterview() carries a cyclomatic complexity of 34 and is the top complexity hotspot." },
      { h: "packages/cache", p: "Thin Redis wrapper plus BullMQ connection factory. Touched by every authenticated request, so treat changes here as platform-wide." },
      { h: "packages/ui", p: "Shared design-system components. 38 exports currently lack documented props." },
    ],
  },
  data: {
    updated: "generated 12 minutes ago",
    source: "packages/db/prisma/schema.prisma · 41 models",
    blocks: [
      { p: "Every tenant-scoped table carries org_id and is protected by a row-level policy. Cursor pagination is implemented per-repository today, which DEBT-097 recommends consolidating." },
      { code: `model Interview {
  id          String   @id @default(cuid())
  orgId       String   @map("org_id")
  candidateId String
  scheduledAt DateTime
  status      InterviewStatus
  scores      Score[]

  @@index([orgId, scheduledAt])
}` },
      { h: "Hot tables", list: [
        "interview_events — 41M rows, scanned by the analytics worker",
        "sessions — Redis only, 12 minute TTL with refresh rotation",
        "subscriptions — mutated by the Stripe webhook (non-idempotent, DEBT-108 adjacent)",
      ] },
    ],
  },
  setup: {
    updated: "generated 12 minutes ago",
    source: "Makefile · docker-compose.yml · .github/workflows",
    blocks: [
      { h: "Local development", code: `pnpm install
docker compose up -d postgres redis
pnpm db:migrate
pnpm dev            # gateway + services + web` },
      { h: "Deployment", p: "CI runs typecheck, unit and integration suites, then builds one image per service. Services deploy independently except auth and billing, which must ship together until the cycle is removed." },
    ],
  },
};

function Documentation() {
  const [active, setActive] = useState("overview");
  const doc = body[active]!;

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Documentation</h1>
          <p className="text-[12px] text-muted-foreground">
            Written from the code on every analysis — never manually maintained.
          </p>
        </div>
        <div className="ml-auto flex gap-2">
          <button className="focus-ring flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] hover:border-border-strong">
            <RefreshCcw className="size-3.5" /> Regenerate
          </button>
          <button className="focus-ring flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] hover:border-border-strong">
            <Download className="size-3.5" /> Export Markdown
          </button>
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[220px_1fr]">
        <Panel className="h-fit">
          <PanelHeader title="Contents" icon={<BookOpen className="size-3.5" />} />
          <ul className="p-2">
            {sections.map((s) => (
              <li key={s.id}>
                <button
                  onClick={() => setActive(s.id)}
                  className={cn(
                    "focus-ring w-full rounded-md px-2.5 py-1.5 text-left text-[12.5px]",
                    active === s.id ? "bg-violet/14 text-foreground" : "text-muted-foreground hover:bg-surface-2",
                  )}
                >
                  {s.title}
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel>
          <PanelHeader
            title={sections.find((s) => s.id === active)!.title}
            subtitle={doc.source}
            actions={<Tag tone="violet">{doc.updated}</Tag>}
          />
          <article className="max-w-3xl space-y-4 p-5">
            {doc.blocks.map((b, i) => (
              <div key={i} className="space-y-2">
                {b.h ? <h3 className="text-[13.5px] font-semibold">{b.h}</h3> : null}
                {b.p ? <p className="text-[13px] leading-relaxed text-secondary-foreground">{b.p}</p> : null}
                {b.list ? (
                  <ul className="space-y-1.5">
                    {b.list.map((l) => (
                      <li key={l} className="flex gap-2 text-[12.5px] text-muted-foreground">
                        <span className="mt-[7px] size-1 shrink-0 rounded-full bg-violet" />
                        {l}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {b.code ? (
                  <pre className="overflow-x-auto rounded-md border border-border bg-surface-2/60 p-3 font-mono text-[11.5px] leading-relaxed text-muted-foreground">
                    {b.code}
                  </pre>
                ) : null}
              </div>
            ))}
            <AiNote title="Confidence">
              This section was derived from static analysis with high confidence. Statements about intent are inferred from
              naming, call graphs and commit messages — review before publishing externally.
            </AiNote>
          </article>
        </Panel>
      </div>
    </div>
  );
}
