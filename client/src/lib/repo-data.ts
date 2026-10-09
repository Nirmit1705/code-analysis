export const PRODUCT = {
  name: "CodeLens AI",
  tagline: "Codebase Intelligence",
};

export const repo = {
  name: "hiresight/hiresight-platform",
  short: "HireSight",
  branch: "main",
  branches: [
    { name: "main", commit: "a3f91c2", message: "fix(auth): refresh token rotation window", when: "2h ago" },
    { name: "feature/payment", commit: "7de0b41", message: "feat(billing): stripe subscription webhooks", when: "5h ago" },
    { name: "release/2.4", commit: "c1b7742", message: "chore: bump node to 20.11", when: "2d ago" },
    { name: "fix/interview-latency", commit: "9a02fe5", message: "perf: batch redis reads in scheduler", when: "4d ago" },
  ],
  lastAnalyzed: "14 minutes ago",
  visibility: "private" as const,
  languages: [
    { name: "TypeScript", pct: 68.4, color: "var(--info)" },
    { name: "JavaScript", pct: 16.2, color: "var(--warning)" },
    { name: "Python", pct: 11.1, color: "var(--cyan)" },
    { name: "SQL", pct: 4.3, color: "var(--magenta)" },
  ],
  stats: {
    files: 3184,
    loc: 412_907,
    modules: 47,
    apis: 118,
    dependencies: 264,
    contributors: 23,
  },
};

export const healthMetrics = [
  { key: "architecture", label: "Architecture Health", value: 82, delta: +3, tone: "success" as const, note: "3 boundary violations" },
  { key: "quality", label: "Code Quality", value: 76, delta: -2, tone: "warning" as const, note: "avg complexity 7.4" },
  { key: "dependency", label: "Dependency Health", value: 64, delta: -6, tone: "warning" as const, note: "9 outdated · 2 CVE" },
  { key: "docs", label: "Documentation Coverage", value: 41, delta: +11, tone: "danger" as const, note: "112 undocumented exports" },
  { key: "debt", label: "Technical Debt", value: 72, delta: +4, tone: "warning" as const, note: "moderate" },
];

export type GraphNode = {
  id: string;
  label: string;
  kind: "frontend" | "gateway" | "service" | "data" | "external" | "cache";
  x: number;
  y: number;
  health: number;
  files: number;
  description: string;
  keyFiles: string[];
  apis: string[];
  insight: string;
};

export const graphNodes: GraphNode[] = [
  { id: "web", label: "React Frontend", kind: "frontend", x: 90, y: 210, health: 88, files: 1042, description: "Vite + React 19 SPA. Route-level code splitting, TanStack Query data layer, shared design system in packages/ui.", keyFiles: ["apps/web/src/main.tsx", "apps/web/src/routes/__root.tsx", "apps/web/src/lib/api-client.ts"], apis: ["GET /api/me", "GET /api/interviews"], insight: "37% of API calls bypass api-client.ts and use raw fetch, defeating retry and auth refresh logic." },
  { id: "gateway", label: "API Gateway", kind: "gateway", x: 320, y: 210, health: 79, files: 148, description: "Fastify edge layer handling routing, rate limiting, request tracing and schema validation before service dispatch.", keyFiles: ["apps/gateway/src/server.ts", "apps/gateway/src/plugins/rate-limit.ts", "apps/gateway/src/routes/index.ts"], apis: ["ALL /api/*"], insight: "Rate limit config is duplicated in two plugins with divergent windows (60s vs 120s)." },
  { id: "auth", label: "Auth Service", kind: "service", x: 560, y: 92, health: 61, files: 96, description: "JWT issuance, refresh rotation, RBAC guards and OAuth (GitHub, Google) exchange.", keyFiles: ["services/auth/src/auth.controller.ts", "services/auth/src/auth.service.ts", "services/auth/src/jwt.strategy.ts", "services/auth/src/auth.guard.ts"], apis: ["POST /api/login", "POST /api/register", "POST /api/token/refresh"], insight: "Authentication module has high coupling with three unrelated services (billing, notification, analytics)." },
  { id: "interview", label: "Interview Service", kind: "service", x: 560, y: 210, health: 84, files: 214, description: "Interview scheduling, transcript pipeline, scoring rubric evaluation and candidate feedback generation.", keyFiles: ["services/interview/src/interview.service.ts", "services/interview/src/scoring/rubric.ts", "services/interview/src/scheduler.ts"], apis: ["POST /api/interviews", "GET /api/interviews/:id", "POST /api/interviews/:id/score"], insight: "Scheduler holds Redis connections per request instead of pooling — visible latency spike above 200 rps." },
  { id: "billing", label: "Billing Service", kind: "service", x: 560, y: 328, health: 73, files: 87, description: "Stripe subscription lifecycle, invoice reconciliation, seat metering and webhook ingestion.", keyFiles: ["services/billing/src/billing.service.ts", "services/billing/src/webhooks/stripe.ts"], apis: ["POST /api/billing/checkout", "POST /api/public/webhooks/stripe"], insight: "Webhook handler is not idempotent; duplicate Stripe deliveries can double-count seats." },
  { id: "analytics", label: "Analytics Worker", kind: "service", x: 560, y: 436, health: 69, files: 63, description: "Python ETL worker aggregating interview funnels into warehouse tables on a 15 minute cadence.", keyFiles: ["workers/analytics/pipeline.py", "workers/analytics/models/funnel.py"], apis: ["internal: analytics.aggregate"], insight: "Worker reads directly from the primary Postgres instance, competing with request traffic." },
  { id: "postgres", label: "PostgreSQL", kind: "data", x: 830, y: 150, health: 86, files: 41, description: "Primary relational store. 64 tables, Prisma schema, migrations under db/migrations.", keyFiles: ["db/schema.prisma", "db/migrations/20240915_add_scores.sql"], apis: [], insight: "Two hot tables (interview_events, audit_log) lack composite indexes used by the analytics worker." },
  { id: "redis", label: "Redis", kind: "cache", x: 830, y: 268, health: 91, files: 12, description: "Session cache, rate limit counters, BullMQ job queues for transcript processing.", keyFiles: ["packages/cache/src/redis.ts", "packages/cache/src/queues.ts"], apis: [], insight: "Session TTL (7d) exceeds refresh token lifetime (24h), leaving stale sessions resolvable." },
  { id: "external", label: "External APIs", kind: "external", x: 830, y: 386, health: 74, files: 28, description: "Stripe, GitHub OAuth, Deepgram transcription, SendGrid email and S3 object storage.", keyFiles: ["packages/integrations/src/stripe.ts", "packages/integrations/src/deepgram.ts"], apis: ["stripe.com/v1", "api.deepgram.com/v1/listen"], insight: "Deepgram calls have no timeout configured; a slow upstream blocks the transcript queue." },
];

export const graphEdges: Array<{ from: string; to: string; label?: string; risk?: "high" | "med" }> = [
  { from: "web", to: "gateway", label: "https" },
  { from: "gateway", to: "auth", label: "jwt" },
  { from: "gateway", to: "interview" },
  { from: "gateway", to: "billing" },
  { from: "auth", to: "postgres" },
  { from: "auth", to: "redis" },
  { from: "interview", to: "postgres" },
  { from: "interview", to: "redis" },
  { from: "interview", to: "external", label: "deepgram" },
  { from: "billing", to: "postgres" },
  { from: "billing", to: "external", label: "stripe", risk: "med" },
  { from: "analytics", to: "postgres", risk: "high" },
  { from: "interview", to: "analytics" },
  { from: "auth", to: "billing", risk: "high" },
];

export const recentCommits = [
  { sha: "a3f91c2", message: "fix(auth): refresh token rotation window", author: "m.okafor", components: ["Auth Service", "Redis"], impact: "high", date: "2h ago" },
  { sha: "7de0b41", message: "feat(billing): stripe subscription webhooks", author: "j.lindqvist", components: ["Billing Service", "External APIs"], impact: "high", date: "5h ago" },
  { sha: "5cc21ab", message: "refactor(interview): extract rubric evaluator", author: "s.ramirez", components: ["Interview Service"], impact: "medium", date: "9h ago" },
  { sha: "e91b330", message: "chore(web): migrate list views to TanStack Query", author: "a.deshpande", components: ["React Frontend"], impact: "low", date: "1d ago" },
  { sha: "b7740de", message: "perf(db): add composite index on interview_events", author: "m.okafor", components: ["PostgreSQL", "Analytics Worker"], impact: "medium", date: "1d ago" },
  { sha: "1fa8c04", message: "fix(gateway): normalize rate-limit window to 60s", author: "t.nakamura", components: ["API Gateway"], impact: "low", date: "2d ago" },
];
